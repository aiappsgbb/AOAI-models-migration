"""
Model Evaluator Module
Runs evaluation tests against Azure OpenAI models.

Supports both sequential and parallel (async) execution.  The async variants
(``evaluate_classification_async``, ``evaluate_dialog_async``,
``evaluate_general_async``) send API requests concurrently via
``asyncio.gather`` with a configurable semaphore so that rate-limits are
respected while still obtaining a significant speed-up (typically 5-10×).
"""

import json
import asyncio
import re
import traceback
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import logging

from ..clients.azure_openai import AzureOpenAIClient, CompletionResult
from ..utils.prompt_loader import PromptLoader
from ..utils.category_parser import extract_categories_from_prompt as _extract_categories_from_prompt
from ..utils.data_loader import (
    DataLoader, 
    ClassificationScenario, 
    DialogScenario,
    GeneralTestCase,
    RAGScenario,
    ToolCallingScenario,
)
from .metrics import (
    MetricsCalculator,
    ClassificationMetrics,
    ConsistencyMetrics,
    LatencyMetrics,
    QualityMetrics,
    ToolCallingMetrics,
)


logger = logging.getLogger(__name__)

# Maps evaluation type to the prompt template key required
_EVAL_PROMPT_TYPES = {
    'classification': 'classification_agent_system',
    'dialog':         'dialog_agent_system',
    'rag':            'rag_agent_system',
    'tool_calling':   'tool_calling_agent_system',
    # 'general' does not use prompt templates
}


# ── Tool-calling response-parsing helpers ────────────────────────────

def _extract_tools_from_json(response_text: str) -> set:
    """Extract tool names from JSON structures in a model response.

    Looks for ``tool_name``, ``function_name``, or ``function.name`` inside
    common wrapper keys (``selected_tools``, ``tool_calls``, etc.) found in
    JSON code-blocks, standalone JSON objects, or the entire response.

    Returns a *set* of lower-cased tool name strings.
    """
    found: set = set()

    def _scan(obj):
        if isinstance(obj, dict):
            for key in ('tool_name', 'function_name'):
                v = obj.get(key)
                if isinstance(v, str) and v:
                    found.add(v.lower())
            # OpenAI-style nested {"function": {"name": "..."}}
            fn = obj.get('function')
            if isinstance(fn, dict):
                n = fn.get('name')
                if isinstance(n, str) and n:
                    found.add(n.lower())
            # "name" only when an "arguments" sibling exists (avoids false
            # positives from unrelated JSON with a "name" field)
            if 'arguments' in obj:
                n = obj.get('name')
                if isinstance(n, str) and n:
                    found.add(n.lower())
            for arr_key in ('selected_tools', 'tool_calls', 'tools', 'functions'):
                for item in obj.get(arr_key, []):
                    if isinstance(item, dict):
                        _scan(item)
        elif isinstance(obj, list):
            for item in obj:
                _scan(item)

    # 1. whole response is JSON
    try:
        _scan(json.loads(response_text.strip()))
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # 2. fenced code blocks  ```json … ```
    for m in re.finditer(r'```(?:json)?\s*\n([\s\S]*?)\n\s*```', response_text):
        try:
            _scan(json.loads(m.group(1)))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # 3. standalone { … } objects (only when nothing found yet)
    if not found:
        for m in re.finditer(
            r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response_text,
        ):
            try:
                _scan(json.loads(m.group()))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

    return found


# Verbs that commonly prefix tool/function names
_VERB_PREFIXES = frozenset({
    'get', 'set', 'check', 'create', 'update', 'delete', 'list',
    'find', 'search', 'add', 'remove', 'send', 'cancel', 'submit',
    'fetch', 'retrieve', 'calculate', 'validate', 'verify', 'process',
    'generate', 'make', 'activate', 'deactivate', 'block', 'unblock',
    'schedule', 'modify', 'change', 'order',
})


def _tool_name_in_text(tool_name: str, response_lower: str) -> bool:
    """Heuristic check whether *tool_name* is referenced in free text.

    Strategies (tried in order):
    1. Exact substring               ``get_data_usage`` in text
    2. Underscores → spaces          ``get data usage`` in text
    3. Function-call syntax           ``get_data_usage(`` in text
    4. "Object phrase" after stripping leading verb prefixes,
       e.g. ``data usage`` from ``get_data_usage``
    5. All significant object-part words present (non-adjacent)
    """
    tl = tool_name.lower()

    # 1. exact substring
    if tl in response_lower:
        return True

    # 2. underscores → spaces
    spaced = tl.replace('_', ' ')
    if len(spaced) > 4 and spaced in response_lower:
        return True

    # 3. function-call syntax: tool_name(
    if re.search(rf'\b{re.escape(tl)}\s*\(', response_lower):
        return True

    # 4 & 5. strip leading verbs, match "object" words
    parts = tl.split('_')
    idx = 0
    while idx < len(parts) and parts[idx] in _VERB_PREFIXES:
        idx += 1
    obj_parts = parts[idx:] if idx < len(parts) else parts

    if obj_parts:
        obj_phrase = ' '.join(obj_parts)
        # 4. contiguous object phrase
        if len(obj_phrase) > 4 and obj_phrase in response_lower:
            return True
        # 5. all significant object words present (≥2 words, each len>2)
        significant = [w for w in obj_parts if len(w) > 2]
        if len(significant) >= 2 and all(w in response_lower for w in significant):
            return True

    return False


def _extract_params_from_json(response_text: str) -> dict:
    """Extract ``{tool_name: {param: value, …}}`` from JSON in the response."""
    params: dict = {}

    def _collect(obj):
        if not isinstance(obj, dict):
            return
        for arr_key in ('selected_tools', 'tool_calls', 'tools'):
            for tool in obj.get(arr_key, []):
                if not isinstance(tool, dict):
                    continue
                name = ''
                for nk in ('tool_name', 'function_name', 'name'):
                    if nk in tool and isinstance(tool[nk], str):
                        name = tool[nk]
                        break
                if not name:
                    fn = tool.get('function')
                    if isinstance(fn, dict) and isinstance(fn.get('name'), str):
                        name = fn['name']
                args = tool.get('arguments', tool.get('params', {}))
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, ValueError):
                        args = {}
                if name and isinstance(args, dict):
                    params[name.lower()] = args

    try:
        _collect(json.loads(response_text.strip()))
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    for m in re.finditer(r'```(?:json)?\s*\n([\s\S]*?)\n\s*```', response_text):
        try:
            _collect(json.loads(m.group(1)))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    return params


class MissingPromptsError(Exception):
    """Raised when a model has no prompts for the requested evaluation type."""
    pass


@dataclass
class EvaluationResult:
    """Container for evaluation results"""
    model_name: str
    evaluation_type: str
    timestamp: str
    scenarios_tested: int
    classification_metrics: Optional[ClassificationMetrics] = None
    consistency_metrics: Optional[ConsistencyMetrics] = None
    latency_metrics: Optional[LatencyMetrics] = None
    quality_metrics: Optional[QualityMetrics] = None
    tool_calling_metrics: Optional[ToolCallingMetrics] = None
    realtime_metrics: Optional['RealtimeMetrics'] = None
    raw_results: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            'model_name': self.model_name,
            'evaluation_type': self.evaluation_type,
            'timestamp': self.timestamp,
            'scenarios_tested': self.scenarios_tested,
            'classification_metrics': self.classification_metrics.to_dict() if self.classification_metrics else None,
            'consistency_metrics': self.consistency_metrics.to_dict() if self.consistency_metrics else None,
            'latency_metrics': self.latency_metrics.to_dict() if self.latency_metrics else None,
            'quality_metrics': self.quality_metrics.to_dict() if self.quality_metrics else None,
            'tool_calling_metrics': self.tool_calling_metrics.to_dict() if self.tool_calling_metrics else None,
            'realtime_metrics': self.realtime_metrics.to_dict() if self.realtime_metrics else None,
            'raw_results': self.raw_results,
            'error_count': len(self.errors),
            'errors': self.errors,
        }
        
    def save(self, output_dir: str = "data/results"):
        """Save results to JSON file (atomic write to prevent truncation)"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        filename = f"{self.model_name}_{self.evaluation_type}_{self.timestamp.replace(':', '-')}.json"
        final_path = output_path / filename
        tmp_path = final_path.with_suffix('.json.tmp')
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, indent=2)
            tmp_path.replace(final_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise


class ModelEvaluator:
    """
    Comprehensive model evaluator for Azure OpenAI models.
    Supports classification, dialog, and general capability testing.
    """
    
    def __init__(
        self,
        client: AzureOpenAIClient,
        prompt_loader: Optional[PromptLoader] = None,
        data_loader: Optional[DataLoader] = None,
        consistency_runs: int = 3,
        max_concurrent: int = 5,
    ):
        """
        Initialize the evaluator.
        
        Args:
            client: Configured AzureOpenAIClient
            prompt_loader: PromptLoader instance (optional)
            data_loader: DataLoader instance (optional)
            consistency_runs: Number of runs for consistency testing
            max_concurrent: Max parallel API calls (semaphore limit)
        """
        self.client = client
        self.prompt_loader = prompt_loader or PromptLoader()
        self.data_loader = data_loader or DataLoader()
        self.metrics_calc = MetricsCalculator()
        self.consistency_runs = consistency_runs
        self.max_concurrent = max(1, max_concurrent)

    def _get_model_semaphore(self, model_name: str) -> "asyncio.Semaphore":
        """Return a semaphore respecting the per-model concurrency limit.

        If the model's ``ModelConfig.max_concurrent`` is set, the effective
        limit is ``min(global, per-model)`` so that low-RPM backends like
        Gemini free-tier don't exhaust their quota.
        """
        limit = self.max_concurrent
        if model_name in self.client.models:
            model_limit = self.client.models[model_name].max_concurrent
            if model_limit is not None:
                limit = min(limit, model_limit)
        return asyncio.Semaphore(max(1, limit))

    def _should_measure_consistency(
        self, model_name: str, measure_consistency: bool
    ) -> bool:
        """Decide whether consistency runs should actually execute.

        Returns *False* (auto-disabling consistency) when the model uses a
        rate-limited backend such as Gemini free-tier (5 RPM, ~20 RPD),
        where the extra requests would quickly exhaust the daily quota.
        For all other backends the caller's original ``measure_consistency``
        flag is honoured.
        """
        if not measure_consistency:
            return False
        config = self.client.models.get(model_name)
        if config is not None and getattr(config, "backend", "azure") == "gemini":
            logger.info(
                "[SKIP-CONSISTENCY] Consistency runs auto-disabled for '%s' "
                "(backend=gemini, rate-limited).",
                model_name,
            )
            return False
        return True

    def _check_prompts_exist(self, model_name: str, evaluation_type: str) -> None:
        """Pre-check that the required prompt template exists for a model.

        Raises ``MissingPromptsError`` with a user-friendly message if the
        prompt file is not found, preventing noisy per-scenario errors.
        """
        prompt_type = _EVAL_PROMPT_TYPES.get(evaluation_type)
        if prompt_type is None:
            return  # e.g. 'general' doesn't need prompt templates
        if not self.prompt_loader.has_prompt(model_name, prompt_type):
            raise MissingPromptsError(
                f"No prompts found for model '{model_name}' "
                f"(expected '{prompt_type}.md'). "
                f"Go to the Prompts page and generate or import prompts "
                f"for this model before running the evaluation."
            )

    def evaluate_classification(
        self,
        model_name: str,
        scenarios: Optional[List[ClassificationScenario]] = None,
        measure_consistency: bool = True
    ) -> EvaluationResult:
        """
        Evaluate classification performance on test scenarios.
        Delegates to the async implementation for parallel execution.
        
        Args:
            model_name: Name of a registered model (e.g. 'gpt4', 'gpt4o', 'gpt5')
            scenarios: List of scenarios (default: load from data)
            measure_consistency: Whether to run consistency tests
            
        Returns:
            EvaluationResult with comprehensive metrics
        """
        return _run_in_loop(self.evaluate_classification_async(
            model_name, scenarios, measure_consistency
        ))

    async def evaluate_classification_async(
        self,
        model_name: str,
        scenarios: Optional[List[ClassificationScenario]] = None,
        measure_consistency: bool = True
    ) -> EvaluationResult:
        """
        Async/parallel classification evaluation.
        
        All scenarios are dispatched concurrently (bounded by
        ``self.max_concurrent``).  Consistency runs for each scenario also
        execute in parallel within the same semaphore.
        """
        self._check_prompts_exist(model_name, 'classification')
        measure_consistency = self._should_measure_consistency(model_name, measure_consistency)

        scenarios = scenarios or self.data_loader.load_classification_scenarios()
        
        sem = self._get_model_semaphore(model_name)
        _effective = sem._value
        logger.info(f"=== Classification evaluation: model={model_name}, scenarios={len(scenarios)}, concurrency={_effective} ===")
        test_cats = sorted(set(s.expected_category for s in scenarios))
        logger.info(f"Test data categories ({len(test_cats)}): {test_cats}")
        try:
            cls_prompt_path = Path(self.prompt_loader.prompts_dir) / model_name / "classification_agent_system.md"
            if cls_prompt_path.exists():
                _prompt_text = cls_prompt_path.read_text(encoding="utf-8")
                first_line = _prompt_text.splitlines()[0][:100]
                logger.info(f"Active classification prompt ({cls_prompt_path.parent.parent.name}): {first_line}")
                # ── Category alignment check ────────────────────────
                prompt_cats = _extract_categories_from_prompt(_prompt_text)
                if prompt_cats:
                    prompt_set = set(prompt_cats)
                    data_set = set(test_cats)
                    mismatched = sorted(data_set - prompt_set)
                    if mismatched:
                        logger.warning(
                            "[CATEGORY MISMATCH] %d test-data categories "
                            "not in %s's prompt taxonomy: %s. "
                            "Accuracy for these will be ~0%%. "
                            "Regenerate test data to realign.",
                            len(mismatched), model_name, mismatched,
                        )
                    else:
                        logger.info(
                            "[OK] All %d test-data categories found "
                            "in %s's prompt taxonomy",
                            len(data_set), model_name,
                        )
        except Exception:
            pass

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="classification",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(scenarios)
        )

        async def _process_one(scenario: ClassificationScenario) -> Optional[Dict]:
            """Process a single scenario + optional consistency runs."""
            try:
                # Primary call — acquire and release semaphore
                async with sem:
                    messages = self.prompt_loader.load_classification_prompt(
                        model=model_name,
                        customer_message=scenario.customer_input,
                        context=scenario.get_context_dict(),
                    )
                    completion = await self.client.complete_async(
                        messages=messages,
                        model_name=model_name,
                        response_format={"type": "json_object"}
                    )
                # Semaphore released here — safe for consistency runs

                prediction = self.metrics_calc.extract_classification_from_response(
                    completion.content
                )
                
                pred_cat = prediction.get('category', 'unknown')
                exp_cat = scenario.expected_category
                match = 'OK' if self.metrics_calc._normalise_category(pred_cat) == self.metrics_calc._normalise_category(exp_cat) else 'FAIL'
                logger.info(
                    f"  [{match}] {scenario.id}: predicted='{pred_cat}' "
                    f"expected='{exp_cat}' (latency={completion.metrics.total_time:.2f}s)"
                )

                # Consistency runs (semaphore already released, no deadlock)
                consistency_responses: Optional[List[str]] = None
                if measure_consistency:
                    consistency_responses = [completion.content]
                    
                    async def _one_repeat():
                        async with sem:
                            r = await self.client.complete_async(
                                messages=messages,
                                model_name=model_name,
                                response_format={"type": "json_object"}
                            )
                            return r.content
                    
                    repeats = await asyncio.gather(
                        *[_one_repeat() for _ in range(self.consistency_runs - 1)]
                    )
                    consistency_responses.extend(repeats)

                return {
                        'prediction': prediction,
                        'ground_truth': {
                            'expected_category': scenario.expected_category,
                            'expected_subcategory': scenario.expected_subcategory,
                            'expected_priority': scenario.expected_priority,
                            'expected_sentiment': scenario.expected_sentiment,
                        },
                        'latency': completion.metrics.total_time,
                        'token_data': {
                            'prompt_tokens': completion.metrics.prompt_tokens,
                            'completion_tokens': completion.metrics.completion_tokens,
                            'cached_tokens': completion.metrics.cached_tokens,
                            'reasoning_tokens': completion.metrics.reasoning_tokens,
                        },
                        'raw': {
                            'scenario_id': scenario.id,
                            'input': scenario.customer_input,
                            'expected': {
                                'category': scenario.expected_category,
                                'subcategory': scenario.expected_subcategory,
                                'priority': scenario.expected_priority,
                                'sentiment': scenario.expected_sentiment,
                            },
                            'predicted': prediction,
                            'latency': completion.metrics.total_time,
                            'tokens': completion.metrics.total_tokens,
                            'token_detail': {
                                'prompt': completion.metrics.prompt_tokens,
                                'completion': completion.metrics.completion_tokens,
                                'cached': completion.metrics.cached_tokens,
                                'reasoning': completion.metrics.reasoning_tokens,
                            },
                        },
                        'consistency_responses': consistency_responses,
                    }
            except Exception as e:
                logger.error(f"Error evaluating scenario {scenario.id}: {e}\n{traceback.format_exc()}")
                result.errors.append(f"{scenario.id}: {str(e)}")
                return None

        # Launch all scenarios in parallel
        outcomes = await asyncio.gather(*[_process_one(s) for s in scenarios])

        # Aggregate results (maintain order)
        predictions = []
        ground_truth = []
        latencies = []
        token_data = []
        raw_results = []
        responses_for_consistency = []

        for out in outcomes:
            if out is None:
                continue
            predictions.append(out['prediction'])
            ground_truth.append(out['ground_truth'])
            latencies.append(out['latency'])
            token_data.append(out['token_data'])
            raw_results.append(out['raw'])
            if out.get('consistency_responses'):
                responses_for_consistency.append(out['consistency_responses'])

        # Calculate metrics
        if predictions:
            result.classification_metrics = self.metrics_calc.calculate_classification_metrics(
                predictions, ground_truth
            )
            result.latency_metrics = self.metrics_calc.calculate_latency_metrics(
                latencies, token_data=token_data, model_name=model_name
            )
            result.quality_metrics = self.metrics_calc.calculate_quality_metrics(
                [p.get('raw_response', '') for p in predictions]
            )
            
        if responses_for_consistency:
            result.consistency_metrics = self.metrics_calc.calculate_consistency_metrics(
                responses_for_consistency
            )
            
        result.scenarios_tested = len(predictions)
        result.raw_results = raw_results
        return result
        
    def evaluate_dialog(
        self,
        model_name: str,
        scenarios: Optional[List[DialogScenario]] = None,
        measure_consistency: bool = True
    ) -> EvaluationResult:
        """
        Evaluate dialog/follow-up question generation.
        Delegates to the async implementation for parallel execution.
        """
        return _run_in_loop(self.evaluate_dialog_async(
            model_name, scenarios, measure_consistency
        ))

    async def evaluate_dialog_async(
        self,
        model_name: str,
        scenarios: Optional[List[DialogScenario]] = None,
        measure_consistency: bool = True
    ) -> EvaluationResult:
        """
        Async/parallel dialog evaluation.

        Metrics measured:
        - Follow-up quality (keyword overlap with expected rules)
        - Context gap coverage (does the response address information gaps)
        - Rule compliance (how well follow-up rules are respected)
        - Empathy / tone (professional opener detection)
        - Optimal similarity (word-level similarity to gold-standard follow-up)
        - Resolution efficiency (question count vs expected turns)
        - Consistency / reproducibility (multiple runs)
        - Latency analytics (mean, P95, std, tokens/sec)
        - Cost & token analytics (cost per request, cache hit rate, reasoning %)
        """
        self._check_prompts_exist(model_name, 'dialog')
        measure_consistency = self._should_measure_consistency(model_name, measure_consistency)

        scenarios = scenarios or self.data_loader.load_dialog_scenarios()
        
        sem = self._get_model_semaphore(model_name)
        _effective = sem._value
        logger.info(f"=== Dialog evaluation: model={model_name}, scenarios={len(scenarios)}, concurrency={_effective} ===")

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="dialog",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(scenarios)
        )

        async def _process_one(scenario: DialogScenario) -> Optional[Dict]:
            try:
                # Primary call — acquire and release semaphore
                async with sem:
                    messages = self.prompt_loader.load_dialog_prompt(
                        model=model_name,
                        conversation=scenario.get_conversation_list()
                    )
                    completion = await self.client.complete_async(
                        messages=messages,
                        model_name=model_name
                    )
                # Semaphore released here — safe for consistency runs

                response_questions = self._extract_questions(completion.content)
                
                logger.info(
                    f"  {scenario.id}: questions={len(response_questions)} "
                    f"latency={completion.metrics.total_time:.2f}s "
                    f"tokens={completion.metrics.total_tokens}"
                )

                # Consistency runs (semaphore already released, no deadlock)
                consistency_responses: Optional[List[str]] = None
                if measure_consistency:
                    consistency_responses = [completion.content]

                    async def _one_repeat():
                        async with sem:
                            r = await self.client.complete_async(
                                messages=messages,
                                model_name=model_name
                            )
                            return r.content

                    repeats = await asyncio.gather(
                        *[_one_repeat() for _ in range(self.consistency_runs - 1)]
                    )
                    consistency_responses.extend(repeats)

                return {
                        'completion': completion,
                        'response_questions': response_questions,
                        'scenario': scenario,
                        'raw': {
                            'scenario_id': scenario.id,
                            'conversation': scenario.get_conversation_list(),
                            'response': completion.content,
                            'context_gaps': scenario.get_context_gaps_list(),
                            'questions_generated': response_questions,
                            'question_count': len(response_questions),
                            'expected_turns': scenario.expected_resolution_turns,
                            'latency': completion.metrics.total_time,
                            'tokens': completion.metrics.total_tokens,
                            'token_detail': {
                                'prompt': completion.metrics.prompt_tokens,
                                'completion': completion.metrics.completion_tokens,
                                'cached': completion.metrics.cached_tokens,
                                'reasoning': completion.metrics.reasoning_tokens,
                            },
                        },
                        'consistency_responses': consistency_responses,
                    }
            except Exception as e:
                logger.error(f"Error evaluating dialog {scenario.id}: {e}")
                result.errors.append(f"{scenario.id}: {str(e)}")
                return None

        outcomes = await asyncio.gather(*[_process_one(s) for s in scenarios])

        # Aggregate
        latencies = []
        generated_questions = []
        expected_questions = []
        dialog_responses = []
        dialog_context_gaps = []
        dialog_rules = []
        dialog_optimal = []
        dialog_expected_turns = []
        question_counts = []
        token_data = []
        responses_for_consistency = []
        raw_results = []

        for out in outcomes:
            if out is None:
                continue
            c = out['completion']
            s = out['scenario']
            latencies.append(c.metrics.total_time)
            token_data.append({
                'prompt_tokens': c.metrics.prompt_tokens,
                'completion_tokens': c.metrics.completion_tokens,
                'cached_tokens': c.metrics.cached_tokens,
                'reasoning_tokens': c.metrics.reasoning_tokens,
            })
            generated_questions.append(out['response_questions'])
            expected_questions.append(s.get_follow_up_rules_list())
            dialog_responses.append(c.content)
            dialog_context_gaps.append(s.get_context_gaps_list())
            dialog_rules.append(s.get_follow_up_rules_list())
            dialog_optimal.append(s.optimal_follow_up)
            dialog_expected_turns.append(s.expected_resolution_turns)
            question_counts.append(len(out['response_questions']))
            raw_results.append(out['raw'])
            if out.get('consistency_responses'):
                responses_for_consistency.append(out['consistency_responses'])

        # Calculate metrics
        if latencies:
            result.latency_metrics = self.metrics_calc.calculate_latency_metrics(
                latencies, token_data=token_data, model_name=model_name
            )
            
        if generated_questions:
            follow_up_quality = self.metrics_calc.calculate_follow_up_quality(
                generated_questions, expected_questions
            )
            context_gap_coverage = self.metrics_calc.calculate_context_gap_coverage(
                dialog_responses, dialog_context_gaps
            )
            rule_compliance = self.metrics_calc.calculate_rule_compliance(
                dialog_responses, dialog_rules
            )
            empathy_score = self.metrics_calc.calculate_empathy_score(
                dialog_responses
            )
            optimal_similarity = self.metrics_calc.calculate_optimal_similarity(
                dialog_responses, dialog_optimal
            )
            resolution_efficiency = self.metrics_calc.calculate_resolution_efficiency(
                question_counts, dialog_expected_turns
            )
            import numpy as _np
            avg_questions = float(_np.mean(question_counts)) if question_counts else 0.0
            
            logger.info(
                f"Dialog metrics: follow_up_quality={follow_up_quality:.2f} "
                f"context_coverage={context_gap_coverage:.2f} "
                f"rule_compliance={rule_compliance:.2f} "
                f"empathy={empathy_score:.2f} "
                f"optimal_sim={optimal_similarity:.2f} "
                f"resolution_eff={resolution_efficiency:.2f} "
                f"avg_questions={avg_questions:.1f}"
            )
            
            result.quality_metrics = QualityMetrics(
                follow_up_quality=follow_up_quality,
                relevance=context_gap_coverage,
                rule_compliance=rule_compliance,
                empathy_score=empathy_score,
                optimal_similarity=optimal_similarity,
                resolution_efficiency=resolution_efficiency,
                question_count_avg=avg_questions,
            )

        if responses_for_consistency:
            result.consistency_metrics = self.metrics_calc.calculate_consistency_metrics(
                responses_for_consistency
            )
            
        result.scenarios_tested = len(dialog_responses)
        result.raw_results = raw_results
        return result
        
    def evaluate_general(
        self,
        model_name: str,
        test_cases: Optional[List[GeneralTestCase]] = None
    ) -> EvaluationResult:
        """
        Evaluate general capabilities with diverse test cases.
        Delegates to the async implementation for parallel execution.
        """
        return _run_in_loop(self.evaluate_general_async(model_name, test_cases))

    async def evaluate_general_async(
        self,
        model_name: str,
        test_cases: Optional[List[GeneralTestCase]] = None
    ) -> EvaluationResult:
        """
        Async/parallel general-capability evaluation.
        
        Each test case may specify ``run_count`` repetitions; all calls are
        dispatched concurrently via the shared semaphore.
        """
        test_cases = test_cases or self.data_loader.load_general_tests()
        
        sem = self._get_model_semaphore(model_name)
        _effective = sem._value
        logger.info(f"=== General evaluation: model={model_name}, test_cases={len(test_cases)}, concurrency={_effective} ===")

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="general",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(test_cases)
        )

        async def _process_one(test: GeneralTestCase) -> Optional[Dict]:
            try:
                conv = test.get_conversation_list()
                if conv:
                    messages = [{"role": m["role"], "content": m["content"]} 
                                for m in conv]
                else:
                    messages = [{"role": "user", "content": test.prompt}]

                async def _one_run():
                    async with sem:
                        return await self.client.complete_async(
                            messages=messages,
                            model_name=model_name
                        )

                completions = await asyncio.gather(
                    *[_one_run() for _ in range(test.run_count)]
                )
                test_responses = [c.content for c in completions]
                test_latencies = [c.metrics.total_time for c in completions]
                test_token_data = [
                    {
                        'prompt_tokens': c.metrics.prompt_tokens,
                        'completion_tokens': c.metrics.completion_tokens,
                        'cached_tokens': c.metrics.cached_tokens,
                        'reasoning_tokens': c.metrics.reasoning_tokens,
                    }
                    for c in completions
                ]

                return {
                    'responses': test_responses,
                    'latencies': test_latencies,
                    'token_data': test_token_data,
                    'raw': {
                        'test_id': test.id,
                        'test_type': test.test_type,
                        'complexity': test.complexity,
                        'prompt': test.prompt,
                        'responses': test_responses,
                        'latencies': test_latencies,
                        'expected_behavior': test.expected_behavior,
                        'token_detail': test_token_data,
                    },
                }
            except Exception as e:
                logger.error(f"Error evaluating test {test.id}: {e}")
                result.errors.append(f"{test.id}: {str(e)}")
                return None

        outcomes = await asyncio.gather(*[_process_one(t) for t in test_cases])

        latencies = []
        responses = []
        token_data = []
        raw_results = []
        for out in outcomes:
            if out is None:
                continue
            responses.extend(out['responses'])
            latencies.extend(out['latencies'])
            token_data.extend(out['token_data'])
            raw_results.append(out['raw'])

        if latencies:
            result.latency_metrics = self.metrics_calc.calculate_latency_metrics(
                latencies, token_data=token_data, model_name=model_name
            )
            
        if responses:
            result.quality_metrics = self.metrics_calc.calculate_quality_metrics(
                responses, expected_format="text"
            )
            
        result.raw_results = raw_results
        return result

    # ── RAG evaluation ────────────────────────────────────────────────

    def evaluate_rag(
        self,
        model_name: str,
        scenarios: Optional[List[RAGScenario]] = None,
        measure_consistency: bool = True,
    ) -> EvaluationResult:
        """Evaluate RAG (Retrieval-Augmented Generation) performance.
        Delegates to the async implementation."""
        return _run_in_loop(self.evaluate_rag_async(
            model_name, scenarios, measure_consistency
        ))

    async def evaluate_rag_async(
        self,
        model_name: str,
        scenarios: Optional[List[RAGScenario]] = None,
        measure_consistency: bool = True,
    ) -> EvaluationResult:
        """Async/parallel RAG evaluation.

        Metrics measured:
        - Groundedness (does the response stick to the context?)
        - Relevance (does it answer the query?)
        - Completeness (does it cover all context-supported facts?)
        - Latency analytics
        - Consistency / reproducibility
        """
        self._check_prompts_exist(model_name, 'rag')
        measure_consistency = self._should_measure_consistency(model_name, measure_consistency)

        scenarios = scenarios or self.data_loader.load_rag_scenarios()

        sem = self._get_model_semaphore(model_name)
        _effective = sem._value
        logger.info(
            f"=== RAG evaluation: model={model_name}, scenarios={len(scenarios)}, "
            f"concurrency={_effective} ==="
        )

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="rag",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(scenarios),
        )

        async def _process_one(scenario: RAGScenario) -> Optional[Dict]:
            try:
                async with sem:
                    messages = self.prompt_loader.load_rag_prompt(
                        model=model_name,
                        query=scenario.query,
                        context=scenario.context,
                    )
                    completion = await self.client.complete_async(
                        messages=messages,
                        model_name=model_name,
                    )

                response_text = completion.content

                # Simple groundedness heuristic: overlap of context keywords in response
                context_words = set(scenario.context.lower().split())
                response_words = set(response_text.lower().split())
                common = context_words & response_words
                groundedness = len(common) / max(len(context_words), 1)

                # Simple relevance heuristic: overlap with ground_truth
                gt_words = set(scenario.ground_truth.lower().split())
                resp_gt_common = gt_words & response_words
                relevance = len(resp_gt_common) / max(len(gt_words), 1)

                logger.info(
                    f"  {scenario.id}: groundedness={groundedness:.2f} "
                    f"relevance={relevance:.2f} "
                    f"latency={completion.metrics.total_time:.2f}s"
                )

                # Consistency runs
                consistency_responses: Optional[List[str]] = None
                if measure_consistency:
                    consistency_responses = [response_text]

                    async def _one_repeat():
                        async with sem:
                            r = await self.client.complete_async(
                                messages=messages, model_name=model_name,
                            )
                            return r.content

                    repeats = await asyncio.gather(
                        *[_one_repeat() for _ in range(self.consistency_runs - 1)]
                    )
                    consistency_responses.extend(repeats)

                return {
                    'groundedness': groundedness,
                    'relevance': relevance,
                    'latency': completion.metrics.total_time,
                    'token_data': {
                        'prompt_tokens': completion.metrics.prompt_tokens,
                        'completion_tokens': completion.metrics.completion_tokens,
                        'cached_tokens': completion.metrics.cached_tokens,
                        'reasoning_tokens': completion.metrics.reasoning_tokens,
                    },
                    'raw': {
                        'scenario_id': scenario.id,
                        'query': scenario.query,
                        'context': scenario.context,
                        'ground_truth': scenario.ground_truth,
                        'response': response_text,
                        'groundedness': groundedness,
                        'relevance': relevance,
                        'latency': completion.metrics.total_time,
                        'tokens': completion.metrics.total_tokens,
                        'token_detail': {
                            'prompt': completion.metrics.prompt_tokens,
                            'completion': completion.metrics.completion_tokens,
                            'cached': completion.metrics.cached_tokens,
                            'reasoning': completion.metrics.reasoning_tokens,
                        },
                    },
                    'consistency_responses': consistency_responses,
                }
            except Exception as e:
                logger.error(f"Error evaluating RAG scenario {scenario.id}: {e}\n{traceback.format_exc()}")
                result.errors.append(f"{scenario.id}: {str(e)}")
                return None

        outcomes = await asyncio.gather(*[_process_one(s) for s in scenarios])

        latencies = []
        groundedness_scores = []
        relevance_scores = []
        token_data = []
        raw_results = []
        responses_for_consistency = []

        for out in outcomes:
            if out is None:
                continue
            latencies.append(out['latency'])
            groundedness_scores.append(out['groundedness'])
            relevance_scores.append(out['relevance'])
            token_data.append(out['token_data'])
            raw_results.append(out['raw'])
            if out.get('consistency_responses'):
                responses_for_consistency.append(out['consistency_responses'])

        if latencies:
            result.latency_metrics = self.metrics_calc.calculate_latency_metrics(
                latencies, token_data=token_data, model_name=model_name,
            )
        if groundedness_scores:
            import numpy as _np
            avg_groundedness = float(_np.mean(groundedness_scores))
            avg_relevance = float(_np.mean(relevance_scores))
            result.quality_metrics = QualityMetrics(
                relevance=avg_relevance,
                groundedness=avg_groundedness,
            )
        if responses_for_consistency:
            result.consistency_metrics = self.metrics_calc.calculate_consistency_metrics(
                responses_for_consistency,
            )

        result.scenarios_tested = len(raw_results)
        result.raw_results = raw_results
        return result

    # ── Tool Calling evaluation ───────────────────────────────────────

    def evaluate_tool_calling(
        self,
        model_name: str,
        scenarios: Optional[List[ToolCallingScenario]] = None,
        measure_consistency: bool = True,
    ) -> EvaluationResult:
        """Evaluate tool-calling accuracy.
        Delegates to the async implementation."""
        return _run_in_loop(self.evaluate_tool_calling_async(
            model_name, scenarios, measure_consistency
        ))

    async def evaluate_tool_calling_async(
        self,
        model_name: str,
        scenarios: Optional[List[ToolCallingScenario]] = None,
        measure_consistency: bool = True,
    ) -> EvaluationResult:
        """Async/parallel tool-calling evaluation.

        Metrics measured:
        - Tool selection accuracy (did it choose the right tool(s)?)
        - Parameter extraction accuracy
        - Latency analytics
        - Consistency / reproducibility
        """
        self._check_prompts_exist(model_name, 'tool_calling')
        measure_consistency = self._should_measure_consistency(model_name, measure_consistency)

        scenarios = scenarios or self.data_loader.load_tool_calling_scenarios()

        sem = self._get_model_semaphore(model_name)
        _effective = sem._value
        logger.info(
            f"=== Tool Calling evaluation: model={model_name}, scenarios={len(scenarios)}, "
            f"concurrency={_effective} ==="
        )

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="tool_calling",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(scenarios),
        )

        async def _process_one(scenario: ToolCallingScenario) -> Optional[Dict]:
            try:
                async with sem:
                    tools_list = scenario.get_tools_list()
                    messages = self.prompt_loader.load_tool_calling_prompt(
                        model=model_name,
                        query=scenario.query,
                        available_tools=tools_list,
                    )
                    completion = await self.client.complete_async(
                        messages=messages,
                        model_name=model_name,
                    )

                response_text = completion.content
                response_lower = response_text.lower()

                # ── Evaluate tool selection accuracy ──────────────
                expected_calls = scenario.get_expected_calls_list()

                # Try structured JSON extraction first, fall back to
                # heuristic text matching.
                json_tools = _extract_tools_from_json(response_text)

                if not expected_calls:
                    # Expected: no tool call — check model didn't force one
                    available_names = {
                        t.get('function', {}).get('name', '').lower()
                        for t in tools_list
                        if isinstance(t, dict) and 'function' in t
                    }
                    if json_tools:
                        tool_accuracy = 0.0 if json_tools & available_names else 1.0
                    else:
                        tool_accuracy = 1.0 if not any(
                            _tool_name_in_text(n, response_lower)
                            for n in available_names if n
                        ) else 0.0
                else:
                    if json_tools:
                        matched = sum(
                            1 for tc in expected_calls
                            if tc.lower() in json_tools
                        )
                    else:
                        matched = sum(
                            1 for tc in expected_calls
                            if _tool_name_in_text(tc, response_lower)
                        )
                    tool_accuracy = matched / len(expected_calls)

                # ── Evaluate parameter extraction ─────────────────
                param_accuracy = 0.0
                expected_params = scenario.get_expected_params_dict()
                if expected_params:
                    json_params = _extract_params_from_json(response_text)
                    total_params = 0
                    matched_params = 0
                    for key, val in expected_params.items():
                        if isinstance(val, dict):
                            # key = tool name, val = {param: value}
                            tool_json_args = json_params.get(key.lower(), {})
                            for pk, pv in val.items():
                                total_params += 1
                                pv_str = str(pv).lower()
                                # Skip placeholder values like <customer_account_id>
                                if pv_str.startswith('<') and pv_str.endswith('>'):
                                    matched_params += 1
                                    continue
                                # Check JSON-extracted params first
                                if pk in tool_json_args and str(tool_json_args[pk]).lower() == pv_str:
                                    matched_params += 1
                                elif pv_str in response_lower:
                                    matched_params += 1
                        else:
                            total_params += 1
                            val_str = str(val).lower()
                            if val_str.startswith('<') and val_str.endswith('>'):
                                matched_params += 1
                                continue
                            if val_str in response_lower:
                                matched_params += 1
                    param_accuracy = matched_params / max(total_params, 1)
                else:
                    param_accuracy = 1.0  # no params expected

                logger.info(
                    f"  {scenario.id}: tool_acc={tool_accuracy:.2f} "
                    f"param_acc={param_accuracy:.2f} "
                    f"latency={completion.metrics.total_time:.2f}s"
                )

                # Consistency runs
                consistency_responses: Optional[List[str]] = None
                if measure_consistency:
                    consistency_responses = [response_text]

                    async def _one_repeat():
                        async with sem:
                            r = await self.client.complete_async(
                                messages=messages, model_name=model_name,
                            )
                            return r.content

                    repeats = await asyncio.gather(
                        *[_one_repeat() for _ in range(self.consistency_runs - 1)]
                    )
                    consistency_responses.extend(repeats)

                return {
                    'tool_accuracy': tool_accuracy,
                    'param_accuracy': param_accuracy,
                    'latency': completion.metrics.total_time,
                    'token_data': {
                        'prompt_tokens': completion.metrics.prompt_tokens,
                        'completion_tokens': completion.metrics.completion_tokens,
                        'cached_tokens': completion.metrics.cached_tokens,
                        'reasoning_tokens': completion.metrics.reasoning_tokens,
                    },
                    'raw': {
                        'scenario_id': scenario.id,
                        'query': scenario.query,
                        'available_tools': [
                            t.get('function', {}).get('name', '') for t in tools_list
                            if isinstance(t, dict)
                        ],
                        'expected_tool_calls': expected_calls,
                        'response': response_text,
                        'tool_accuracy': tool_accuracy,
                        'param_accuracy': param_accuracy,
                        'latency': completion.metrics.total_time,
                        'tokens': completion.metrics.total_tokens,
                        'token_detail': {
                            'prompt': completion.metrics.prompt_tokens,
                            'completion': completion.metrics.completion_tokens,
                            'cached': completion.metrics.cached_tokens,
                            'reasoning': completion.metrics.reasoning_tokens,
                        },
                    },
                    'consistency_responses': consistency_responses,
                }
            except Exception as e:
                logger.error(f"Error evaluating tool calling scenario {scenario.id}: {e}\n{traceback.format_exc()}")
                result.errors.append(f"{scenario.id}: {str(e)}")
                return None

        outcomes = await asyncio.gather(*[_process_one(s) for s in scenarios])

        latencies = []
        tool_accuracies = []
        param_accuracies = []
        token_data = []
        raw_results = []
        responses_for_consistency = []

        for out in outcomes:
            if out is None:
                continue
            latencies.append(out['latency'])
            tool_accuracies.append(out['tool_accuracy'])
            param_accuracies.append(out['param_accuracy'])
            token_data.append(out['token_data'])
            raw_results.append(out['raw'])
            if out.get('consistency_responses'):
                responses_for_consistency.append(out['consistency_responses'])

        if latencies:
            result.latency_metrics = self.metrics_calc.calculate_latency_metrics(
                latencies, token_data=token_data, model_name=model_name,
            )
        if tool_accuracies:
            import numpy as _np
            avg_tool_acc = float(_np.mean(tool_accuracies))
            avg_param_acc = float(_np.mean(param_accuracies))
            combined_accuracy = (avg_tool_acc + avg_param_acc) / 2
            result.tool_calling_metrics = ToolCallingMetrics(
                tool_selection_accuracy=avg_tool_acc,
                parameter_accuracy=avg_param_acc,
                combined_accuracy=combined_accuracy,
            )
            result.quality_metrics = QualityMetrics(
                format_compliance=avg_tool_acc,
                completeness=avg_param_acc,
            )
        if responses_for_consistency:
            result.consistency_metrics = self.metrics_calc.calculate_consistency_metrics(
                responses_for_consistency,
            )

        result.scenarios_tested = len(raw_results)
        result.raw_results = raw_results
        return result

    # ── Full evaluation suite ─────────────────────────────────────────
        
    def run_full_evaluation(
        self,
        model_name: str,
        save_results: bool = True
    ) -> Dict[str, EvaluationResult]:
        """
        Run complete evaluation suite on a model.
        
        Args:
            model_name: Name of registered model
            save_results: Whether to save results to files
            
        Returns:
            Dictionary of evaluation results by type
        """
        results = {}
        
        logger.info(f"Starting full evaluation for {model_name}")
        
        # Classification evaluation
        logger.info("Running classification evaluation...")
        results['classification'] = self.evaluate_classification(model_name)
        
        # Dialog evaluation
        logger.info("Running dialog evaluation...")
        results['dialog'] = self.evaluate_dialog(model_name)
        
        # General evaluation
        logger.info("Running general capability evaluation...")
        results['general'] = self.evaluate_general(model_name)

        # RAG evaluation
        try:
            logger.info("Running RAG evaluation...")
            results['rag'] = self.evaluate_rag(model_name)
        except FileNotFoundError:
            logger.info("RAG test data not found — skipping")

        # Tool Calling evaluation
        try:
            logger.info("Running tool calling evaluation...")
            results['tool_calling'] = self.evaluate_tool_calling(model_name)
        except FileNotFoundError:
            logger.info("Tool calling test data not found — skipping")
        
        # Save results if requested
        if save_results:
            for eval_type, result in results.items():
                result.save()
                logger.info(f"Saved {eval_type} results")
                
        return results
        
    def _extract_questions(self, response: str) -> List[str]:
        """Extract questions from a response"""
        # Find sentences ending with ?
        questions = re.findall(r'[^.!?\n]*\?', response)
        
        # Clean up whitespace and filter empty strings
        questions = [q.strip() for q in questions if q.strip()]
        
        return questions


# ---------------------------------------------------------------------------
# Persistent background event loop for running async code from sync callers
# ---------------------------------------------------------------------------

import concurrent.futures as _cf
import threading as _threading

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[_threading.Thread] = None
_loop_lock = _threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Return the shared background event loop, creating it on first use."""
    global _loop, _loop_thread
    if _loop is not None and _loop.is_running():
        return _loop
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        _loop = asyncio.new_event_loop()

        def _run():
            asyncio.set_event_loop(_loop)
            _loop.run_forever()

        _loop_thread = _threading.Thread(target=_run, daemon=True, name="eval-loop")
        _loop_thread.start()
        return _loop


def _run_in_loop(coro):
    """Run an async coroutine from synchronous code.

    Uses a persistent background event loop to avoid the overhead of
    creating and destroying a loop on every call.  The loop lives in a
    daemon thread and is reused across all invocations.

    **ContextVar propagation**: the calling thread's ``contextvars``
    snapshot (including ``_current_run_id``) is captured via
    ``copy_context()`` and forwarded to the Task so that log-capture
    handlers running inside the coroutine can see the correct run-id.
    """
    import contextvars as _ctx

    loop = _ensure_loop()
    ctx = _ctx.copy_context()

    # We cannot use asyncio.run_coroutine_threadsafe directly because
    # it does NOT propagate the caller's ContextVars.  Instead we
    # schedule a Task manually with the captured context.
    result_future: _cf.Future = _cf.Future()

    def _schedule():
        try:
            task = loop.create_task(coro, context=ctx)
            task.add_done_callback(_on_done)
        except BaseException as exc:
            if not result_future.done():
                result_future.set_exception(exc)

    def _on_done(task: asyncio.Task):
        if task.cancelled():
            result_future.cancel()
        elif task.exception() is not None:
            result_future.set_exception(task.exception())
        else:
            result_future.set_result(task.result())

    loop.call_soon_threadsafe(_schedule)
    return result_future.result()


# Example usage
if __name__ == "__main__":
    print("Model Evaluator Module")
    print("=" * 50)
    print("\nUsage:")
    print("  evaluator = ModelEvaluator(client)")
    print("  results = evaluator.run_full_evaluation('<model_name>')")
