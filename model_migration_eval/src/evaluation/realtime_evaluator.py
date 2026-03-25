"""
Realtime (Speech-to-Speech) Evaluator
=====================================

Orchestrates the TTS → Realtime API → Transcript → Metrics pipeline
for all 5 evaluation task types.

The evaluator reuses the **same test scenarios** as the text evaluator;
the difference is that user text is first converted to audio via TTS,
sent through the Realtime WebSocket API, and the model's transcript
is then evaluated with the same metrics logic.

Returns standard ``EvaluationResult`` objects so the comparator and
UI work without changes.
"""

import asyncio
import json
import logging
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from ..clients.azure_openai import AzureOpenAIClient, ModelConfig
from ..clients.tts_client import TTSClient, TTSConfig
from ..clients.realtime_client import RealtimeClient, RealtimeConfig, is_realtime_available
from ..utils.audio_utils import AudioSegment, pcm16_duration_ms
from ..utils.data_loader import (
    DataLoader,
    ClassificationScenario,
    DialogScenario,
    GeneralTestCase,
    RAGScenario,
    ToolCallingScenario,
)
from ..utils.prompt_loader import PromptLoader
from .evaluator import EvaluationResult
from .metrics import (
    MetricsCalculator,
    ClassificationMetrics,
    ConsistencyMetrics,
    LatencyMetrics,
    QualityMetrics,
    ToolCallingMetrics,
)
from .realtime_metrics import RealtimeMetrics

logger = logging.getLogger(__name__)


class RealtimeEvaluator:
    """Evaluates Realtime (speech-to-speech) models across all task types.

    Uses the same test data as ``ModelEvaluator`` but routes input through
    TTS → Realtime WebSocket → transcript extraction before computing
    the standard metrics.
    """

    def __init__(
        self,
        azure_client: AzureOpenAIClient,
        prompt_loader: Optional[PromptLoader] = None,
        data_loader: Optional[DataLoader] = None,
        tts_config: Optional[TTSConfig] = None,
        consistency_runs: int = 2,
        max_concurrent: int = 2,
        realtime_endpoint: Optional[str] = None,
        realtime_api_version: Optional[str] = None,
        tts_endpoint: Optional[str] = None,
    ):
        self.azure_client = azure_client
        self.prompt_loader = prompt_loader or PromptLoader()
        self.data_loader = data_loader or DataLoader()
        self.metrics_calc = MetricsCalculator()
        self.consistency_runs = consistency_runs
        self.max_concurrent = max(1, max_concurrent)

        # Optional dedicated endpoint for Realtime WebSocket voice models.
        self._realtime_endpoint = realtime_endpoint
        self._realtime_api_version = realtime_api_version or azure_client.api_version

        # Optional separate endpoint for TTS synthesis.
        # Priority: tts_endpoint > realtime_endpoint > main azure endpoint.
        # This handles the case where gpt-4o-mini-tts is deployed on a
        # different account than gpt-realtime (e.g. different regions).
        self._tts_endpoint = tts_endpoint or realtime_endpoint

        # TTS client — uses the sync Azure OpenAI client for audio.speech
        self._tts_config = tts_config or TTSConfig()
        self._tts: Optional[TTSClient] = None
        self._tts_openai_client = None  # separate OpenAI client if endpoint differs

        # Realtime client — created lazily per model
        self._realtime: Optional[RealtimeClient] = None

    def _get_voice_token_provider(self):
        """Return the token provider for the voice endpoint.

        Always uses Entra ID (DefaultAzureCredential).  If a separate
        realtime endpoint is configured the same credential works because
        the scope (cognitiveservices) is the same.
        """
        return self.azure_client._token_provider

    def _ensure_tts(self) -> TTSClient:
        """Lazy-init the TTS client.

        Uses ``_tts_endpoint`` when set (which may differ from the Realtime
        endpoint when gpt-4o-mini-tts is on a different account).
        Falls back to the main Azure OpenAI client if no dedicated TTS
        endpoint is configured.
        """
        if self._tts is None:
            if self._tts_endpoint:
                # Build a separate AzureOpenAI client for the TTS endpoint
                if self._tts_openai_client is None:
                    from openai import AzureOpenAI as _AzureOpenAI
                    auth_kwargs: dict = {}
                    tp = self._get_voice_token_provider()
                    if tp:
                        auth_kwargs["azure_ad_token_provider"] = tp
                    else:
                        raise ValueError(
                            "TTS endpoint requires Entra ID authentication. "
                            "Ensure DefaultAzureCredential is configured (az login / Managed Identity)."
                        )
                    self._tts_openai_client = _AzureOpenAI(
                        azure_endpoint=self._tts_endpoint,
                        api_version=self._realtime_api_version,
                        timeout=120.0,
                        max_retries=3,
                        **auth_kwargs,
                    )
                self._tts = TTSClient(self._tts_openai_client, self._tts_config)
            else:
                self._tts = TTSClient(self.azure_client.client, self._tts_config)
        return self._tts

    def _ensure_realtime(self) -> RealtimeClient:
        """Lazy-init the Realtime WebSocket client, using the dedicated voice endpoint when configured."""
        if self._realtime is None:
            endpoint = self._realtime_endpoint or self.azure_client.endpoint
            self._realtime = RealtimeClient(
                endpoint=endpoint,
                api_version=self._realtime_api_version,
                token_provider=self._get_voice_token_provider(),
            )
        return self._realtime

    def _get_model_config(self, model_name: str) -> ModelConfig:
        if model_name not in self.azure_client.models:
            raise ValueError(f"Model '{model_name}' not registered.")
        return self.azure_client.models[model_name]

    # ── Dispatch ────────────────────────────────────────────────────────

    async def evaluate_async(
        self,
        model_name: str,
        evaluation_type: str,
        measure_consistency: bool = False,
    ) -> EvaluationResult:
        """Dispatch to the appropriate task evaluator."""
        if evaluation_type == "classification":
            return await self._evaluate_classification(model_name, measure_consistency)
        elif evaluation_type == "dialog":
            return await self._evaluate_dialog(model_name, measure_consistency)
        elif evaluation_type == "rag":
            return await self._evaluate_rag(model_name, measure_consistency)
        elif evaluation_type == "tool_calling":
            return await self._evaluate_tool_calling(model_name, measure_consistency)
        else:
            return await self._evaluate_general(model_name)

    # ── Classification ──────────────────────────────────────────────────

    async def _evaluate_classification(
        self, model_name: str, measure_consistency: bool = False,
    ) -> EvaluationResult:
        config = self._get_model_config(model_name)
        scenarios = self.data_loader.load_classification_scenarios()
        sem = asyncio.Semaphore(self.max_concurrent)

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="classification",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(scenarios),
        )

        # Pre-synthesize all audio (sync — uses cache)
        tts = self._ensure_tts()
        tts_results = {
            s.id: tts.synthesize(s.customer_input, config.voice or "alloy")
            for s in scenarios
        }

        realtime = self._ensure_realtime()

        async def _process(scenario: ClassificationScenario):
            try:
                tts_r = tts_results[scenario.id]
                instructions = self.prompt_loader.load_prompt(
                    model_name, "classification_agent_system",
                )
                rt_config = RealtimeConfig(
                    deployment_name=config.deployment_name,
                    voice=config.voice or "alloy",
                    modalities=["text"],  # classification only needs JSON text, not audio
                    instructions=instructions + "\n\nRespond ONLY with a valid JSON object containing your classification.",
                    temperature=config.temperature,
                    max_response_output_tokens=config.max_tokens,
                )

                async with sem:
                    rt_result = await realtime.send_audio(tts_r.audio.data, rt_config)

                prediction = self.metrics_calc.extract_classification_from_response(
                    rt_result.transcript
                )

                return {
                    'prediction': prediction,
                    'ground_truth': {
                        'expected_category': scenario.expected_category,
                        'expected_subcategory': scenario.expected_subcategory,
                        'expected_priority': scenario.expected_priority,
                        'expected_sentiment': scenario.expected_sentiment,
                    },
                    'latency': rt_result.session_time_ms / 1000.0,
                    'token_data': {
                        'prompt_tokens': rt_result.input_tokens,
                        'completion_tokens': rt_result.output_tokens,
                        'cached_tokens': 0,
                        'reasoning_tokens': 0,
                    },
                    'rt_metrics': {
                        'ttfa_ms': rt_result.time_to_first_audio_ms,
                        'session_ms': rt_result.session_time_ms,
                        'ws_connect_ms': rt_result.ws_connect_time_ms,
                        'input_audio_duration_ms': tts_r.audio.duration_ms,
                        'output_audio_duration_ms': pcm16_duration_ms(rt_result.audio_data),
                        'input_audio_tokens': rt_result.input_audio_tokens,
                        'output_audio_tokens': rt_result.output_audio_tokens,
                        'tts_latency_ms': tts_r.tts_latency_ms,
                        'tts_cached': tts_r.cached,
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
                        'transcript': rt_result.transcript,
                        'latency': rt_result.session_time_ms / 1000.0,
                        'tokens': rt_result.input_tokens + rt_result.output_tokens,
                        'token_detail': {
                            'prompt': rt_result.input_tokens,
                            'completion': rt_result.output_tokens,
                            'cached': 0,
                            'reasoning': 0,
                        },
                    },
                }
            except Exception as e:
                logger.error("Realtime classification %s: %s\n%s", scenario.id, e, traceback.format_exc())
                result.errors.append(f"{scenario.id}: {e}")
                return None

        outcomes = await asyncio.gather(*[_process(s) for s in scenarios])

        predictions, ground_truth, latencies, token_data, raw_results = [], [], [], [], []
        rt_metrics_list = []
        for out in outcomes:
            if out is None:
                continue
            predictions.append(out['prediction'])
            ground_truth.append(out['ground_truth'])
            latencies.append(out['latency'])
            token_data.append(out['token_data'])
            raw_results.append(out['raw'])
            rt_metrics_list.append(out['rt_metrics'])

        if predictions:
            result.classification_metrics = self.metrics_calc.calculate_classification_metrics(
                predictions, ground_truth
            )
            result.latency_metrics = self.metrics_calc.calculate_latency_metrics(
                latencies, token_data=token_data, model_name=model_name,
            )

        result.realtime_metrics = self._aggregate_rt_metrics(rt_metrics_list, model_name)
        result.scenarios_tested = len(predictions)
        result.raw_results = raw_results
        return result

    # ── Dialog ──────────────────────────────────────────────────────────

    async def _evaluate_dialog(
        self, model_name: str, measure_consistency: bool = False,
    ) -> EvaluationResult:
        config = self._get_model_config(model_name)
        scenarios = self.data_loader.load_dialog_scenarios()
        sem = asyncio.Semaphore(self.max_concurrent)
        tts = self._ensure_tts()
        realtime = self._ensure_realtime()

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="dialog",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(scenarios),
        )

        async def _process(scenario: DialogScenario):
            try:
                conv = scenario.get_conversation_list()
                last_user = ""
                for msg in reversed(conv):
                    if msg.get("role") in ("user", "customer"):
                        last_user = msg.get("message", msg.get("content", ""))
                        break
                if not last_user:
                    last_user = "Hello, I need help."

                tts_r = tts.synthesize(last_user, config.voice or "alloy")

                instructions = self.prompt_loader.load_prompt(model_name, "dialog_agent_system")
                conv_text = "\n".join(
                    f"{m.get('role', 'user')}: {m.get('message', m.get('content', ''))}"
                    for m in conv
                )
                full_instructions = f"{instructions}\n\nConversation so far:\n{conv_text}"

                rt_config = RealtimeConfig(
                    deployment_name=config.deployment_name,
                    voice=config.voice or "alloy",
                    modalities=["text", "audio"],
                    instructions=full_instructions,
                    temperature=config.temperature,
                    max_response_output_tokens=config.max_tokens,
                )

                async with sem:
                    rt_result = await realtime.send_audio(tts_r.audio.data, rt_config)

                import re
                questions = re.findall(r'[^.!?\n]*\?', rt_result.transcript)
                questions = [q.strip() for q in questions if q.strip()]

                return {
                    'response': rt_result.transcript,
                    'questions': questions,
                    'scenario': scenario,
                    'latency': rt_result.session_time_ms / 1000.0,
                    'token_data': {
                        'prompt_tokens': rt_result.input_tokens,
                        'completion_tokens': rt_result.output_tokens,
                        'cached_tokens': 0, 'reasoning_tokens': 0,
                    },
                    'rt_metrics': {
                        'ttfa_ms': rt_result.time_to_first_audio_ms,
                        'session_ms': rt_result.session_time_ms,
                        'ws_connect_ms': rt_result.ws_connect_time_ms,
                        'input_audio_duration_ms': tts_r.audio.duration_ms,
                        'output_audio_duration_ms': pcm16_duration_ms(rt_result.audio_data),
                        'input_audio_tokens': rt_result.input_audio_tokens,
                        'output_audio_tokens': rt_result.output_audio_tokens,
                        'tts_latency_ms': tts_r.tts_latency_ms,
                        'tts_cached': tts_r.cached,
                    },
                    'raw': {
                        'scenario_id': scenario.id,
                        'conversation': conv,
                        'response': rt_result.transcript,
                        'context_gaps': scenario.get_context_gaps_list(),
                        'questions_generated': questions,
                        'question_count': len(questions),
                        'expected_turns': scenario.expected_resolution_turns,
                        'category': getattr(scenario, 'category', ''),
                        'latency': rt_result.session_time_ms / 1000.0,
                        'tokens': rt_result.input_tokens + rt_result.output_tokens,
                        'token_detail': {
                            'prompt_tokens': rt_result.input_tokens,
                            'completion_tokens': rt_result.output_tokens,
                            'cached_tokens': 0,
                            'reasoning_tokens': 0,
                        },
                    },
                }
            except Exception as e:
                logger.error("Realtime dialog %s: %s", scenario.id, e)
                result.errors.append(f"{scenario.id}: {e}")
                return None

        outcomes = await asyncio.gather(*[_process(s) for s in scenarios])

        latencies, raw_results, rt_metrics_list = [], [], []
        dialog_responses, dialog_context_gaps, dialog_rules = [], [], []
        dialog_optimal, dialog_expected_turns, question_counts = [], [], []
        generated_questions, expected_questions = [], []

        for out in outcomes:
            if out is None:
                continue
            s = out['scenario']
            latencies.append(out['latency'])
            raw_results.append(out['raw'])
            rt_metrics_list.append(out['rt_metrics'])
            dialog_responses.append(out['response'])
            dialog_context_gaps.append(s.get_context_gaps_list())
            dialog_rules.append(s.get_follow_up_rules_list())
            dialog_optimal.append(s.optimal_follow_up)
            dialog_expected_turns.append(s.expected_resolution_turns)
            question_counts.append(len(out['questions']))
            generated_questions.append(out['questions'])
            expected_questions.append(s.get_follow_up_rules_list())

        if latencies:
            token_data = [out['token_data'] for out in outcomes if out]
            result.latency_metrics = self.metrics_calc.calculate_latency_metrics(
                latencies, token_data=token_data, model_name=model_name,
            )

        if generated_questions:
            follow_up_quality = self.metrics_calc.calculate_follow_up_quality(
                generated_questions, expected_questions,
            )
            context_gap_coverage = self.metrics_calc.calculate_context_gap_coverage(
                dialog_responses, dialog_context_gaps,
            )
            rule_compliance = self.metrics_calc.calculate_rule_compliance(
                dialog_responses, dialog_rules,
            )
            empathy_score = self.metrics_calc.calculate_empathy_score(dialog_responses)
            optimal_similarity = self.metrics_calc.calculate_optimal_similarity(
                dialog_responses, dialog_optimal,
            )
            resolution_efficiency = self.metrics_calc.calculate_resolution_efficiency(
                question_counts, dialog_expected_turns,
            )
            result.quality_metrics = QualityMetrics(
                follow_up_quality=follow_up_quality,
                relevance=context_gap_coverage,
                rule_compliance=rule_compliance,
                empathy_score=empathy_score,
                optimal_similarity=optimal_similarity,
                resolution_efficiency=resolution_efficiency,
                question_count_avg=float(np.mean(question_counts)) if question_counts else 0.0,
            )

        result.realtime_metrics = self._aggregate_rt_metrics(rt_metrics_list, model_name)
        result.scenarios_tested = len(raw_results)
        result.raw_results = raw_results
        return result

    # ── General ─────────────────────────────────────────────────────────

    async def _evaluate_general(self, model_name: str) -> EvaluationResult:
        config = self._get_model_config(model_name)
        test_cases = self.data_loader.load_general_tests()
        sem = asyncio.Semaphore(self.max_concurrent)
        tts = self._ensure_tts()
        realtime = self._ensure_realtime()

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="general",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(test_cases),
        )

        async def _process(test: GeneralTestCase):
            try:
                prompt_text = test.prompt or "Hello"
                tts_r = tts.synthesize(prompt_text, config.voice or "alloy")

                rt_config = RealtimeConfig(
                    deployment_name=config.deployment_name,
                    voice=config.voice or "alloy",
                    modalities=["text", "audio"],
                    instructions="You are a helpful assistant. Answer the user's question clearly.",
                    temperature=config.temperature,
                    max_response_output_tokens=config.max_tokens,
                )

                async with sem:
                    rt_result = await realtime.send_audio(tts_r.audio.data, rt_config)

                return {
                    'response': rt_result.transcript,
                    'latency': rt_result.session_time_ms / 1000.0,
                    'token_data': {
                        'prompt_tokens': rt_result.input_tokens,
                        'completion_tokens': rt_result.output_tokens,
                        'cached_tokens': 0, 'reasoning_tokens': 0,
                    },
                    'rt_metrics': {
                        'ttfa_ms': rt_result.time_to_first_audio_ms,
                        'session_ms': rt_result.session_time_ms,
                        'ws_connect_ms': rt_result.ws_connect_time_ms,
                        'input_audio_duration_ms': tts_r.audio.duration_ms,
                        'output_audio_duration_ms': pcm16_duration_ms(rt_result.audio_data),
                        'input_audio_tokens': rt_result.input_audio_tokens,
                        'output_audio_tokens': rt_result.output_audio_tokens,
                        'tts_latency_ms': tts_r.tts_latency_ms,
                        'tts_cached': tts_r.cached,
                    },
                    'raw': {
                        'test_id': test.id,
                        'test_type': test.test_type,
                        'complexity': getattr(test, 'complexity', ''),
                        'prompt': prompt_text,
                        'responses': [rt_result.transcript],
                        'latencies': [rt_result.session_time_ms / 1000.0],
                        'expected_behavior': test.expected_behavior,
                        'token_detail': [{
                            'prompt_tokens': rt_result.input_tokens,
                            'completion_tokens': rt_result.output_tokens,
                            'cached_tokens': 0,
                            'reasoning_tokens': 0,
                        }],
                    },
                }
            except Exception as e:
                logger.error("Realtime general %s: %s", test.id, e)
                result.errors.append(f"{test.id}: {e}")
                return None

        outcomes = await asyncio.gather(*[_process(t) for t in test_cases])

        latencies, responses, raw_results, rt_metrics_list = [], [], [], []
        for out in outcomes:
            if out is None:
                continue
            latencies.append(out['latency'])
            responses.append(out['response'])
            raw_results.append(out['raw'])
            rt_metrics_list.append(out['rt_metrics'])

        if latencies:
            token_data = [out['token_data'] for out in outcomes if out]
            result.latency_metrics = self.metrics_calc.calculate_latency_metrics(
                latencies, token_data=token_data, model_name=model_name,
            )
        if responses:
            result.quality_metrics = self.metrics_calc.calculate_quality_metrics(
                responses, expected_format="text",
            )

        result.realtime_metrics = self._aggregate_rt_metrics(rt_metrics_list, model_name)
        result.scenarios_tested = len(raw_results)
        result.raw_results = raw_results
        return result

    # ── RAG ─────────────────────────────────────────────────────────────

    async def _evaluate_rag(
        self, model_name: str, measure_consistency: bool = False,
    ) -> EvaluationResult:
        config = self._get_model_config(model_name)
        scenarios = self.data_loader.load_rag_scenarios()
        sem = asyncio.Semaphore(self.max_concurrent)
        tts = self._ensure_tts()
        realtime = self._ensure_realtime()

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="rag",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(scenarios),
        )

        async def _process(scenario: RAGScenario):
            try:
                tts_r = tts.synthesize(scenario.query, config.voice or "alloy")

                instructions = self.prompt_loader.load_prompt(model_name, "rag_agent_system")
                full_instructions = (
                    f"{instructions}\n\nContext information:\n{scenario.context}\n\n"
                    "Answer the user's question based ONLY on the context provided."
                )

                rt_config = RealtimeConfig(
                    deployment_name=config.deployment_name,
                    voice=config.voice or "alloy",
                    modalities=["text", "audio"],
                    instructions=full_instructions,
                    temperature=config.temperature,
                    max_response_output_tokens=config.max_tokens,
                )

                async with sem:
                    rt_result = await realtime.send_audio(tts_r.audio.data, rt_config)

                response_text = rt_result.transcript
                context_words = set(scenario.context.lower().split())
                response_words = set(response_text.lower().split())
                groundedness = len(context_words & response_words) / max(len(context_words), 1)
                gt_words = set(scenario.ground_truth.lower().split())
                relevance = len(gt_words & response_words) / max(len(gt_words), 1)

                return {
                    'groundedness': groundedness,
                    'relevance': relevance,
                    'latency': rt_result.session_time_ms / 1000.0,
                    'token_data': {
                        'prompt_tokens': rt_result.input_tokens,
                        'completion_tokens': rt_result.output_tokens,
                        'cached_tokens': 0, 'reasoning_tokens': 0,
                    },
                    'rt_metrics': {
                        'ttfa_ms': rt_result.time_to_first_audio_ms,
                        'session_ms': rt_result.session_time_ms,
                        'ws_connect_ms': rt_result.ws_connect_time_ms,
                        'input_audio_duration_ms': tts_r.audio.duration_ms,
                        'output_audio_duration_ms': pcm16_duration_ms(rt_result.audio_data),
                        'input_audio_tokens': rt_result.input_audio_tokens,
                        'output_audio_tokens': rt_result.output_audio_tokens,
                        'tts_latency_ms': tts_r.tts_latency_ms,
                        'tts_cached': tts_r.cached,
                    },
                    'raw': {
                        'scenario_id': scenario.id,
                        'query': scenario.query,
                        'context': scenario.context,
                        'ground_truth': scenario.ground_truth,
                        'response': response_text,
                        'groundedness': groundedness,
                        'relevance': relevance,
                        'latency': rt_result.session_time_ms / 1000.0,
                        'tokens': rt_result.input_tokens + rt_result.output_tokens,
                        'token_detail': {
                            'prompt': rt_result.input_tokens,
                            'completion': rt_result.output_tokens,
                            'cached': 0,
                            'reasoning': 0,
                        },
                    },
                }
            except Exception as e:
                logger.error("Realtime RAG %s: %s", scenario.id, e)
                result.errors.append(f"{scenario.id}: {e}")
                return None

        outcomes = await asyncio.gather(*[_process(s) for s in scenarios])

        latencies, raw_results, rt_metrics_list = [], [], []
        groundedness_scores, relevance_scores = [], []
        for out in outcomes:
            if out is None:
                continue
            latencies.append(out['latency'])
            groundedness_scores.append(out['groundedness'])
            relevance_scores.append(out['relevance'])
            raw_results.append(out['raw'])
            rt_metrics_list.append(out['rt_metrics'])

        if latencies:
            token_data = [out['token_data'] for out in outcomes if out]
            result.latency_metrics = self.metrics_calc.calculate_latency_metrics(
                latencies, token_data=token_data, model_name=model_name,
            )
        if groundedness_scores:
            result.quality_metrics = QualityMetrics(
                relevance=float(np.mean(relevance_scores)),
                groundedness=float(np.mean(groundedness_scores)),
            )

        result.realtime_metrics = self._aggregate_rt_metrics(rt_metrics_list, model_name)
        result.scenarios_tested = len(raw_results)
        result.raw_results = raw_results
        return result

    # ── Tool Calling ────────────────────────────────────────────────────

    async def _evaluate_tool_calling(
        self, model_name: str, measure_consistency: bool = False,
    ) -> EvaluationResult:
        config = self._get_model_config(model_name)
        scenarios = self.data_loader.load_tool_calling_scenarios()
        sem = asyncio.Semaphore(self.max_concurrent)
        tts = self._ensure_tts()
        realtime = self._ensure_realtime()

        result = EvaluationResult(
            model_name=model_name,
            evaluation_type="tool_calling",
            timestamp=datetime.now().isoformat(),
            scenarios_tested=len(scenarios),
        )

        async def _process(scenario: ToolCallingScenario):
            try:
                tts_r = tts.synthesize(scenario.query, config.voice or "alloy")

                tools_list = scenario.get_tools_list()
                instructions = self.prompt_loader.load_prompt(model_name, "tool_calling_agent_system")

                # Convert tools to Realtime API format
                rt_tools = []
                for t in tools_list:
                    if isinstance(t, dict) and "function" in t:
                        fn = t["function"]
                        rt_tools.append({
                            "type": "function",
                            "name": fn.get("name", ""),
                            "description": fn.get("description", ""),
                            "parameters": fn.get("parameters", {}),
                        })

                rt_config = RealtimeConfig(
                    deployment_name=config.deployment_name,
                    voice=config.voice or "alloy",
                    modalities=["text", "audio"],
                    instructions=instructions,
                    temperature=config.temperature,
                    max_response_output_tokens=config.max_tokens,
                    tools=rt_tools if rt_tools else None,
                )

                async with sem:
                    rt_result = await realtime.send_audio(tts_r.audio.data, rt_config)

                expected_calls = scenario.get_expected_calls_list()

                # Tool calls come as native WebSocket events — more reliable!
                detected_tools = {tc.name.lower() for tc in rt_result.tool_calls if tc.name}

                if not expected_calls:
                    tool_accuracy = 1.0 if not detected_tools else 0.0
                else:
                    matched = sum(1 for ec in expected_calls if ec.lower() in detected_tools)
                    tool_accuracy = matched / len(expected_calls)

                # Parameter accuracy from tool call arguments
                param_accuracy = 1.0
                expected_params = scenario.get_expected_params_dict()
                if expected_params and rt_result.tool_calls:
                    total_params, matched_params = 0, 0
                    for tc in rt_result.tool_calls:
                        try:
                            args = json.loads(tc.arguments) if tc.arguments else {}
                        except json.JSONDecodeError:
                            args = {}
                        exp = expected_params.get(tc.name, {})
                        if isinstance(exp, dict):
                            for pk, pv in exp.items():
                                total_params += 1
                                if str(args.get(pk, "")).lower() == str(pv).lower():
                                    matched_params += 1
                    param_accuracy = matched_params / max(total_params, 1) if total_params else 1.0

                return {
                    'tool_accuracy': tool_accuracy,
                    'param_accuracy': param_accuracy,
                    'latency': rt_result.session_time_ms / 1000.0,
                    'token_data': {
                        'prompt_tokens': rt_result.input_tokens,
                        'completion_tokens': rt_result.output_tokens,
                        'cached_tokens': 0, 'reasoning_tokens': 0,
                    },
                    'rt_metrics': {
                        'ttfa_ms': rt_result.time_to_first_audio_ms,
                        'session_ms': rt_result.session_time_ms,
                        'ws_connect_ms': rt_result.ws_connect_time_ms,
                        'input_audio_duration_ms': tts_r.audio.duration_ms,
                        'output_audio_duration_ms': pcm16_duration_ms(rt_result.audio_data),
                        'input_audio_tokens': rt_result.input_audio_tokens,
                        'output_audio_tokens': rt_result.output_audio_tokens,
                        'tts_latency_ms': tts_r.tts_latency_ms,
                        'tts_cached': tts_r.cached,
                    },
                    'raw': {
                        'scenario_id': scenario.id,
                        'query': scenario.query,
                        'available_tools': [t.get('function', {}).get('name', '') for t in tools_list if isinstance(t, dict)],
                        'expected_tool_calls': expected_calls,
                        'detected_tool_calls': [tc.name for tc in rt_result.tool_calls],
                        'response': rt_result.transcript,
                        'tool_accuracy': tool_accuracy,
                        'param_accuracy': param_accuracy,
                        'latency': rt_result.session_time_ms / 1000.0,
                        'tokens': rt_result.input_tokens + rt_result.output_tokens,
                        'token_detail': {
                            'prompt': rt_result.input_tokens,
                            'completion': rt_result.output_tokens,
                            'cached': 0,
                            'reasoning': 0,
                        },
                    },
                }
            except Exception as e:
                logger.error("Realtime tool_calling %s: %s", scenario.id, e)
                result.errors.append(f"{scenario.id}: {e}")
                return None

        outcomes = await asyncio.gather(*[_process(s) for s in scenarios])

        latencies, raw_results, rt_metrics_list = [], [], []
        tool_accuracies, param_accuracies = [], []
        for out in outcomes:
            if out is None:
                continue
            latencies.append(out['latency'])
            tool_accuracies.append(out['tool_accuracy'])
            param_accuracies.append(out['param_accuracy'])
            raw_results.append(out['raw'])
            rt_metrics_list.append(out['rt_metrics'])

        if latencies:
            token_data = [out['token_data'] for out in outcomes if out]
            result.latency_metrics = self.metrics_calc.calculate_latency_metrics(
                latencies, token_data=token_data, model_name=model_name,
            )
        if tool_accuracies:
            avg_tool = float(np.mean(tool_accuracies))
            avg_param = float(np.mean(param_accuracies))
            result.tool_calling_metrics = ToolCallingMetrics(
                tool_selection_accuracy=avg_tool,
                parameter_accuracy=avg_param,
                combined_accuracy=(avg_tool + avg_param) / 2,
            )

        result.realtime_metrics = self._aggregate_rt_metrics(rt_metrics_list, model_name)
        result.scenarios_tested = len(raw_results)
        result.raw_results = raw_results
        return result

    # ── Aggregate realtime metrics ──────────────────────────────────────

    def _aggregate_rt_metrics(
        self, rt_list: List[Dict], model_name: str,
    ) -> Optional[RealtimeMetrics]:
        """Aggregate per-scenario realtime metrics into summary stats."""
        if not rt_list:
            return None

        ttfa = [m['ttfa_ms'] for m in rt_list if m.get('ttfa_ms', 0) > 0]
        sessions = [m['session_ms'] for m in rt_list]
        ws_connects = [m['ws_connect_ms'] for m in rt_list]
        in_dur = [m['input_audio_duration_ms'] for m in rt_list]
        out_dur = [m['output_audio_duration_ms'] for m in rt_list]
        in_tok = [m['input_audio_tokens'] for m in rt_list]
        out_tok = [m['output_audio_tokens'] for m in rt_list]
        tts_lat = [m['tts_latency_ms'] for m in rt_list if not m.get('tts_cached')]
        tts_cached_count = sum(1 for m in rt_list if m.get('tts_cached'))

        # Cost calculation
        rates = self.metrics_calc.get_cost_rates(model_name)
        audio_in_rate = rates.get('audio_input', 0.06) / 1000.0
        audio_out_rate = rates.get('audio_output', 0.24) / 1000.0
        total_audio_cost = sum(
            m.get('input_audio_tokens', 0) * audio_in_rate +
            m.get('output_audio_tokens', 0) * audio_out_rate
            for m in rt_list
        )

        return RealtimeMetrics(
            mean_time_to_first_audio_ms=float(np.mean(ttfa)) if ttfa else 0.0,
            mean_session_time_ms=float(np.mean(sessions)) if sessions else 0.0,
            mean_ws_connect_time_ms=float(np.mean(ws_connects)) if ws_connects else 0.0,
            p95_session_time_ms=float(np.percentile(sessions, 95)) if len(sessions) > 1 else (sessions[0] if sessions else 0.0),
            mean_input_audio_duration_ms=float(np.mean(in_dur)) if in_dur else 0.0,
            mean_output_audio_duration_ms=float(np.mean(out_dur)) if out_dur else 0.0,
            mean_input_audio_tokens=float(np.mean(in_tok)) if in_tok else 0.0,
            mean_output_audio_tokens=float(np.mean(out_tok)) if out_tok else 0.0,
            mean_tts_latency_ms=float(np.mean(tts_lat)) if tts_lat else 0.0,
            tts_cache_hit_rate=(tts_cached_count / len(rt_list) * 100) if rt_list else 0.0,
            audio_cost_per_request=total_audio_cost / len(rt_list) if rt_list else 0.0,
            total_audio_cost=total_audio_cost,
        )
