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
    QualityMetrics
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

        scenarios = scenarios or self.data_loader.load_classification_scenarios()
        
        logger.info(f"=== Classification evaluation: model={model_name}, scenarios={len(scenarios)}, concurrency={self.max_concurrent} ===")
        test_cats = sorted(set(s.expected_category for s in scenarios))
        logger.info(f"Test data categories ({len(test_cats)}): {test_cats}")
        try:
            cls_prompt_path = Path(f"prompts/{model_name}/classification_agent_system.md")
            if cls_prompt_path.exists():
                first_line = cls_prompt_path.read_text(encoding="utf-8").splitlines()[0][:100]
                logger.info(f"Active classification prompt: {first_line}")
        except Exception:
            pass

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="classification",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(scenarios)
        )
        
        sem = asyncio.Semaphore(self.max_concurrent)

        async def _process_one(scenario: ClassificationScenario) -> Optional[Dict]:
            """Process a single scenario + optional consistency runs."""
            try:
                # Primary call — acquire and release semaphore
                async with sem:
                    messages = self.prompt_loader.load_classification_prompt(
                        model=model_name,
                        customer_message=scenario.customer_input,
                        context=scenario.context,
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

        scenarios = scenarios or self.data_loader.load_dialog_scenarios()
        
        logger.info(f"=== Dialog evaluation: model={model_name}, scenarios={len(scenarios)}, concurrency={self.max_concurrent} ===")
        
        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="dialog",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(scenarios)
        )
        
        sem = asyncio.Semaphore(self.max_concurrent)

        async def _process_one(scenario: DialogScenario) -> Optional[Dict]:
            try:
                # Primary call — acquire and release semaphore
                async with sem:
                    messages = self.prompt_loader.load_dialog_prompt(
                        model=model_name,
                        conversation=scenario.conversation
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
                            'scenario': scenario.scenario,
                            'category': scenario.category,
                            'conversation': scenario.conversation,
                            'response': completion.content,
                            'context_gaps': scenario.context_gaps,
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
            expected_questions.append(s.follow_up_rules)
            dialog_responses.append(c.content)
            dialog_context_gaps.append(s.context_gaps)
            dialog_rules.append(s.follow_up_rules)
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
        
        logger.info(f"=== General evaluation: model={model_name}, test_cases={len(test_cases)}, concurrency={self.max_concurrent} ===")

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="general",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(test_cases)
        )
        
        sem = asyncio.Semaphore(self.max_concurrent)

        async def _process_one(test: GeneralTestCase) -> Optional[Dict]:
            try:
                if test.conversation:
                    messages = [{"role": m["role"], "content": m["content"]} 
                                for m in test.conversation]
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

                return {
                    'responses': test_responses,
                    'latencies': test_latencies,
                    'raw': {
                        'test_id': test.id,
                        'test_type': test.test_type,
                        'complexity': test.complexity,
                        'prompt': test.prompt,
                        'responses': test_responses,
                        'latencies': test_latencies,
                        'expected_output': test.expected_output,
                        'expected_behavior': test.expected_behavior,
                    },
                }
            except Exception as e:
                logger.error(f"Error evaluating test {test.id}: {e}")
                result.errors.append(f"{test.id}: {str(e)}")
                return None

        outcomes = await asyncio.gather(*[_process_one(t) for t in test_cases])

        latencies = []
        responses = []
        raw_results = []
        for out in outcomes:
            if out is None:
                continue
            responses.extend(out['responses'])
            latencies.extend(out['latencies'])
            raw_results.append(out['raw'])

        if latencies:
            result.latency_metrics = self.metrics_calc.calculate_latency_metrics(latencies)
            
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

        scenarios = scenarios or self.data_loader.load_rag_scenarios()

        logger.info(
            f"=== RAG evaluation: model={model_name}, scenarios={len(scenarios)}, "
            f"concurrency={self.max_concurrent} ==="
        )

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="rag",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(scenarios),
        )

        sem = asyncio.Semaphore(self.max_concurrent)

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

        scenarios = scenarios or self.data_loader.load_tool_calling_scenarios()

        logger.info(
            f"=== Tool Calling evaluation: model={model_name}, scenarios={len(scenarios)}, "
            f"concurrency={self.max_concurrent} ==="
        )

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="tool_calling",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(scenarios),
        )

        sem = asyncio.Semaphore(self.max_concurrent)

        async def _process_one(scenario: ToolCallingScenario) -> Optional[Dict]:
            try:
                async with sem:
                    messages = self.prompt_loader.load_tool_calling_prompt(
                        model=model_name,
                        query=scenario.query,
                        available_tools=scenario.available_tools,
                    )
                    completion = await self.client.complete_async(
                        messages=messages,
                        model_name=model_name,
                    )

                response_text = completion.content
                response_lower = response_text.lower()

                # Evaluate tool selection accuracy
                expected_calls = scenario.expected_tool_calls
                if not expected_calls:
                    # Expected: no tool call — check model didn't force one
                    tool_accuracy = 1.0 if not any(
                        t.get('function', {}).get('name', '').lower() in response_lower
                        for t in scenario.available_tools
                        if isinstance(t, dict) and 'function' in t
                    ) else 0.0
                else:
                    matched = sum(
                        1 for tc in expected_calls if tc.lower() in response_lower
                    )
                    tool_accuracy = matched / len(expected_calls)

                # Evaluate parameter extraction (check if expected param values appear in response)
                param_accuracy = 0.0
                expected_params = scenario.expected_parameters
                if expected_params:
                    total_params = 0
                    matched_params = 0
                    for key, val in expected_params.items():
                        if isinstance(val, dict):
                            for pk, pv in val.items():
                                total_params += 1
                                if str(pv).lower() in response_lower:
                                    matched_params += 1
                        else:
                            total_params += 1
                            if str(val).lower() in response_lower:
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
                            t.get('function', {}).get('name', '') for t in scenario.available_tools
                            if isinstance(t, dict)
                        ],
                        'expected_tool_calls': scenario.expected_tool_calls,
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
            result.classification_metrics = ClassificationMetrics(
                accuracy=combined_accuracy,
                f1_score=avg_tool_acc,
                precision=avg_param_acc,
                recall=avg_tool_acc,
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
# Helpers
# ---------------------------------------------------------------------------

def _run_in_loop(coro):
    """Run an async coroutine from synchronous code.
    
    If there is already a running event loop (e.g. inside Jupyter or an
    outer ``asyncio.run``), we schedule on that loop via a background thread
    so we never hit "cannot run nested event loop".  Otherwise we simply
    use ``asyncio.run``.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # We're inside an existing event loop — run in a new thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)


# Example usage
if __name__ == "__main__":
    print("Model Evaluator Module")
    print("=" * 50)
    print("\nUsage:")
    print("  evaluator = ModelEvaluator(client)")
    print("  results = evaluator.run_full_evaluation('<model_name>')")
