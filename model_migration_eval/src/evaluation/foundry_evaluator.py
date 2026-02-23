"""
Microsoft Foundry Control Plane Evaluation Integration.

Complements the local evaluation metrics with LLM-as-judge evaluators
running on the Foundry Runtime (azure-ai-projects v2).  Results are
visible in the Foundry Control Plane dashboard.

Architecture:
    Local evaluation (fast, free)  ──►  metrics.py  ──►  Chart.js UI
                                           │
                                           ▼
    Foundry Runtime (LLM-as-judge)  ──►  Control Plane dashboard
"""

import json
import os
import re
import time
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Availability check — graceful degradation when SDK is not installed
# ---------------------------------------------------------------------------
_FOUNDRY_AVAILABLE = False
try:
    from azure.identity import DefaultAzureCredential
    from azure.ai.projects import AIProjectClient
    from openai.types.eval_create_params import DataSourceConfigCustom
    from openai.types.evals.create_eval_jsonl_run_data_source_param import (
        CreateEvalJSONLRunDataSourceParam,
        SourceFileID,
    )
    _FOUNDRY_AVAILABLE = True
except ImportError:
    logger.info(
        "azure-ai-projects SDK not installed — Foundry evaluation disabled.  "
        "Install with: pip install 'azure-ai-projects>=2.0.0b2'"
    )


def is_foundry_available() -> bool:
    """Return True when the Foundry SDK is importable."""
    return _FOUNDRY_AVAILABLE


# ---------------------------------------------------------------------------
# JSONL export helpers
# ---------------------------------------------------------------------------

def _ensure_string(value: Any) -> str:
    """Coerce a value to a plain string — serialise dicts/lists as JSON."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _classification_to_jsonl(raw_results: List[Dict]) -> List[Dict]:
    """Convert local classification raw_results to Foundry-compatible JSONL rows."""
    rows = []
    for r in raw_results:
        predicted = r.get("predicted", {})
        # Build a readable response string from the predicted JSON
        response_text = json.dumps(predicted, ensure_ascii=False) if isinstance(predicted, dict) else str(predicted)
        rows.append({
            "query": _ensure_string(r.get("input", "")),
            "response": _ensure_string(response_text),
            "ground_truth": _ensure_string(r.get("expected", {}).get("category", "")),
            "context": _ensure_string(r.get("input", "")),
        })
    return rows


def _dialog_to_jsonl(raw_results: List[Dict]) -> List[Dict]:
    """Convert local dialog raw_results to Foundry-compatible JSONL rows."""
    rows = []
    for r in raw_results:
        # Use the last user turn as query
        conversation = r.get("conversation", [])
        last_user = ""
        for msg in reversed(conversation):
            if msg.get("role") == "user":
                last_user = msg.get("content", "")
                break
        rows.append({
            "query": _ensure_string(last_user or r.get("scenario", "")),
            "response": _ensure_string(r.get("response", "")),
            "ground_truth": "",  # Dialog has no single ground truth
            "context": _ensure_string(", ".join(r.get("context_gaps", []))),
        })
    return rows


def _general_to_jsonl(raw_results: List[Dict]) -> List[Dict]:
    """Convert local general raw_results to Foundry-compatible JSONL rows."""
    rows = []
    for r in raw_results:
        responses = r.get("responses", [])
        response = responses[0] if responses else ""
        # expected_output can be a dict (e.g. expected JSON schema) —
        # always coerce to string so the JSONL schema stays uniform.
        ground_truth = r.get("expected_output") or r.get("expected_behavior") or ""
        rows.append({
            "query": _ensure_string(r.get("prompt", "")),
            "response": _ensure_string(response),
            "ground_truth": _ensure_string(ground_truth),
            "context": "",
        })
    return rows


def export_to_jsonl(
    raw_results: List[Dict],
    evaluation_type: str,
    output_path: Optional[str] = None,
) -> str:
    """
    Export evaluation raw_results to a JSONL file for Foundry upload.

    Args:
        raw_results: The raw_results list from EvaluationResult
        evaluation_type: 'classification', 'dialog', or 'general'
        output_path: Where to write the file (default: temp file)

    Returns:
        Absolute path to the written JSONL file
    """
    converters = {
        "classification": _classification_to_jsonl,
        "dialog": _dialog_to_jsonl,
        "general": _general_to_jsonl,
    }
    converter = converters.get(evaluation_type, _general_to_jsonl)
    rows = converter(raw_results)

    if output_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        output_path = str(
            Path(tempfile.gettempdir()) / f"foundry_eval_{evaluation_type}_{ts}.jsonl"
        )

    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    logger.info(f"Exported {len(rows)} rows to {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Built-in evaluator configuration
# ---------------------------------------------------------------------------

def _get_testing_criteria(
    evaluation_type: str,
    deployment_name: str,
) -> List[Dict[str, Any]]:
    """
    Build the testing_criteria list for the OpenAI Evals API.

    Uses ``score_model`` type — the only LLM-as-judge evaluator type
    natively supported by the Evals API exposed through
    ``project_client.get_openai_client()``.
    """
    # ----- helpers -----
    def _score(name: str, system: str, user_tpl: str) -> Dict[str, Any]:
        return {
            "type": "score_model",
            "name": name,
            "model": deployment_name,
            "input": [
                {"role": "developer", "content": system},
                {"role": "user", "content": user_tpl},
            ],
            "range": [1, 5],
            "pass_threshold": 3,
        }

    # ----- common evaluators (all types) -----
    common = [
        _score(
            "coherence",
            (
                "You are an expert evaluator. Rate the COHERENCE of the "
                "response: logical flow, organization and internal consistency.\n"
                "1 = Incoherent / contradictory\n"
                "2 = Mostly incoherent, hard to follow\n"
                "3 = Somewhat coherent but has logical gaps\n"
                "4 = Coherent with minor issues\n"
                "5 = Perfectly coherent and well-organized\n"
                "Return ONLY a single integer from 1 to 5."
            ),
            "Query: {{item.query}}\n\nResponse: {{item.response}}",
        ),
        _score(
            "fluency",
            (
                "You are an expert evaluator. Rate the FLUENCY of the "
                "response: grammatical correctness, natural language quality "
                "and readability.\n"
                "1 = Very poor grammar, unreadable\n"
                "2 = Poor grammar, hard to read\n"
                "3 = Acceptable grammar with some issues\n"
                "4 = Good grammar, reads naturally\n"
                "5 = Excellent grammar, perfectly fluent\n"
                "Return ONLY a single integer from 1 to 5."
            ),
            "Response: {{item.response}}",
        ),
        _score(
            "relevance",
            (
                "You are an expert evaluator. Rate the RELEVANCE of the "
                "response: how well it addresses and answers the original query.\n"
                "1 = Completely irrelevant\n"
                "2 = Mostly irrelevant, off-topic\n"
                "3 = Partially relevant\n"
                "4 = Mostly relevant with minor gaps\n"
                "5 = Perfectly relevant and on-topic\n"
                "Return ONLY a single integer from 1 to 5."
            ),
            "Query: {{item.query}}\n\nResponse: {{item.response}}",
        ),
    ]

    # ----- classification-specific -----
    classification_specific = [
        _score(
            "task_adherence",
            (
                "You are an expert evaluator. Rate TASK ADHERENCE: how well "
                "the response follows the classification task requirements, "
                "providing the requested format and fields.\n"
                "1 = Does not follow task requirements at all\n"
                "2 = Follows some requirements but misses key aspects\n"
                "3 = Partially follows requirements\n"
                "4 = Mostly follows requirements with minor gaps\n"
                "5 = Perfectly follows all task requirements\n"
                "Return ONLY a single integer from 1 to 5."
            ),
            "Query: {{item.query}}\n\nResponse: {{item.response}}",
        ),
        _score(
            "similarity",
            (
                "You are an expert evaluator. Rate the SIMILARITY between "
                "the response and the expected ground-truth answer.\n"
                "1 = Completely different meaning\n"
                "2 = Mostly different\n"
                "3 = Partially similar\n"
                "4 = Mostly similar with minor differences\n"
                "5 = Identical or equivalent meaning\n"
                "Return ONLY a single integer from 1 to 5."
            ),
            (
                "Query: {{item.query}}\n\n"
                "Response: {{item.response}}\n\n"
                "Expected Answer: {{item.ground_truth}}"
            ),
        ),
    ]

    # ----- dialog-specific -----
    dialog_specific = [
        _score(
            "intent_resolution",
            (
                "You are an expert evaluator. Rate INTENT RESOLUTION: "
                "whether the response properly identifies the user's underlying "
                "intent and addresses it effectively.\n"
                "1 = Completely misses user intent\n"
                "2 = Partially identifies intent but fails to address it\n"
                "3 = Identifies intent but addresses it only partially\n"
                "4 = Identifies and addresses intent with minor gaps\n"
                "5 = Perfectly identifies and resolves user intent\n"
                "Return ONLY a single integer from 1 to 5."
            ),
            (
                "Query: {{item.query}}\n\n"
                "Response: {{item.response}}\n\n"
                "Context: {{item.context}}"
            ),
        ),
        _score(
            "task_adherence",
            (
                "You are an expert evaluator. Rate TASK ADHERENCE: how well "
                "the response follows the dialog task requirements including "
                "empathy, follow-up questions and resolution approach.\n"
                "1 = Does not follow task requirements at all\n"
                "2 = Follows some requirements but misses key aspects\n"
                "3 = Partially follows requirements\n"
                "4 = Mostly follows requirements with minor gaps\n"
                "5 = Perfectly follows all task requirements\n"
                "Return ONLY a single integer from 1 to 5."
            ),
            "Query: {{item.query}}\n\nResponse: {{item.response}}",
        ),
    ]

    # ----- general-specific -----
    general_specific = [
        _score(
            "response_completeness",
            (
                "You are an expert evaluator. Rate RESPONSE COMPLETENESS: "
                "whether the response fully addresses all aspects of the query "
                "compared to the expected output.\n"
                "1 = Extremely incomplete, misses all key points\n"
                "2 = Mostly incomplete\n"
                "3 = Partially complete\n"
                "4 = Mostly complete with minor omissions\n"
                "5 = Fully complete, addresses everything\n"
                "Return ONLY a single integer from 1 to 5."
            ),
            (
                "Query: {{item.query}}\n\n"
                "Response: {{item.response}}\n\n"
                "Expected Output: {{item.ground_truth}}"
            ),
        ),
    ]

    type_specific = {
        "classification": classification_specific,
        "dialog": dialog_specific,
        "general": general_specific,
    }

    return common + type_specific.get(evaluation_type, [])


# ---------------------------------------------------------------------------
# Main Foundry evaluation runner
# ---------------------------------------------------------------------------

class FoundryEvaluator:
    """
    Submits evaluation data to the Microsoft Foundry Control Plane.

    Usage:
        fe = FoundryEvaluator(
            project_endpoint="https://<name>.services.ai.azure.com/api/projects/<proj>",
            deployment_name="gpt-4.1",
        )
        result = fe.submit_evaluation(raw_results, "classification", "gpt4")
    """

    def __init__(
        self,
        project_endpoint: str,
        deployment_name: str,
        grader_model: Optional[str] = None,
    ):
        if not _FOUNDRY_AVAILABLE:
            raise RuntimeError(
                "Foundry SDK not installed. "
                "Run: pip install 'azure-ai-projects>=2.0.0b2'"
            )
        self.project_endpoint = project_endpoint
        self.deployment_name = deployment_name
        # The model used inside score_model graders.
        # The Evals API only supports certain models for grading:
        # gpt-4.1-2025-04-14, gpt-4o-2024-08-06, o3-mini-2025-01-31, etc.
        # Default to gpt-4.1 which maps to a widely supported grader model.
        self.grader_model = grader_model or "gpt-4.1"

    def submit_evaluation(
        self,
        raw_results: List[Dict],
        evaluation_type: str,
        model_name: str,
        poll: bool = True,
        poll_interval: float = 5.0,
        timeout: float = 300.0,
    ) -> Dict[str, Any]:
        """
        Export results to JSONL, upload to Foundry, create an evaluation,
        and optionally wait for completion.

        Args:
            raw_results: The raw_results from EvaluationResult
            evaluation_type: 'classification', 'dialog', or 'general'
            model_name: Local model key (e.g. 'gpt4') — used for naming
            poll: Whether to wait for the run to complete
            poll_interval: Seconds between status checks
            timeout: Maximum seconds to wait

        Returns:
            Dict with keys:
                - eval_id: Foundry evaluation ID
                - run_id: Foundry run ID
                - status: 'completed' | 'failed' | 'running'
                - report_url: URL to view results in Foundry (if completed)
                - evaluators: List of evaluator names used
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        eval_name = f"{model_name}-{evaluation_type}-{ts}"

        # 1. Export to JSONL — include model_name to avoid race conditions
        #    when two models are evaluated in parallel (same timestamp).
        unique_jsonl = str(
            Path(tempfile.gettempdir())
            / f"foundry_eval_{evaluation_type}_{model_name}_{ts}.jsonl"
        )
        jsonl_path = export_to_jsonl(raw_results, evaluation_type, output_path=unique_jsonl)
        logger.info(f"[Foundry] Exported {len(raw_results)} results to {jsonl_path}")

        credential = DefaultAzureCredential()
        try:
            with (
                AIProjectClient(
                    endpoint=self.project_endpoint,
                    credential=credential,
                ) as project_client,
                project_client.get_openai_client() as openai_client,
            ):
                # 2. Upload dataset
                logger.info("[Foundry] Uploading dataset...")
                dataset = project_client.datasets.upload_file(
                    name=f"{eval_name}-data",
                    version="1",
                    file_path=jsonl_path,
                )
                logger.info(f"[Foundry] Dataset uploaded: {dataset.name} (ID: {dataset.id})")

                # 3. Build data source config
                data_source_config = DataSourceConfigCustom(
                    {
                        "type": "custom",
                        "item_schema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "response": {"type": "string"},
                                "context": {"type": "string"},
                                "ground_truth": {"type": "string"},
                            },
                            "required": ["query", "response"],
                        },
                        "include_sample_schema": False,
                    }
                )

                # 4. Build testing criteria (use grader_model for score_model graders)
                testing_criteria = _get_testing_criteria(
                    evaluation_type, self.grader_model
                )
                evaluator_names = [tc["name"] for tc in testing_criteria]
                logger.info(
                    f"[Foundry] Evaluators: {evaluator_names}"
                )

                # 5. Create evaluation
                logger.info("[Foundry] Creating evaluation...")
                evaluation = openai_client.evals.create(
                    name=eval_name,
                    data_source_config=data_source_config,
                    testing_criteria=testing_criteria,
                )
                logger.info(f"[Foundry] Evaluation created: {evaluation.id}")

                # 6. Create evaluation run
                logger.info("[Foundry] Starting evaluation run...")
                run = openai_client.evals.runs.create(
                    eval_id=evaluation.id,
                    name=f"{eval_name}-run",
                    data_source=CreateEvalJSONLRunDataSourceParam(
                        type="jsonl",
                        source=SourceFileID(type="file_id", id=dataset.id),
                    ),
                )
                logger.info(f"[Foundry] Run created: {run.id}")

                # 7. Poll for completion
                status = run.status
                report_url = getattr(run, "report_url", None) or ""

                if poll:
                    elapsed = 0.0
                    while status not in ("completed", "failed") and elapsed < timeout:
                        time.sleep(poll_interval)
                        elapsed += poll_interval
                        run = openai_client.evals.runs.retrieve(
                            run_id=run.id, eval_id=evaluation.id
                        )
                        status = run.status
                        report_url = getattr(run, "report_url", None) or ""
                        logger.info(f"[Foundry] Status: {status} ({elapsed:.0f}s)")

                    if status == "completed":
                        logger.info(f"[Foundry] Evaluation completed -> {report_url}")
                    elif status == "failed":
                        run_error = getattr(run, "error", None)
                        logger.error(
                            f"[Foundry] Evaluation run failed.  "
                            f"error={run_error}  "
                            f"run_id={run.id}  eval_id={evaluation.id}"
                        )
                    else:
                        logger.warning(f"[Foundry] Timed out after {timeout}s -- status: {status}")

                # 8. Retrieve per-row scores if completed
                foundry_scores = None
                if status == "completed":
                    try:
                        foundry_scores = self._retrieve_results_with_client(
                            openai_client, evaluation.id, run.id
                        )
                    except Exception as e:
                        logger.warning(f"[Foundry] Failed to retrieve detailed scores: {e}")

                return {
                    "eval_id": evaluation.id,
                    "run_id": run.id,
                    "status": status,
                    "report_url": report_url,
                    "evaluators": evaluator_names,
                    "dataset_id": dataset.id,
                    "eval_name": eval_name,
                    "foundry_scores": foundry_scores,
                }

        except Exception as e:
            logger.error(f"[Foundry] Error submitting evaluation: {e}")
            raise

    # ------------------------------------------------------------------
    # Score retrieval
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_reason_from_sample(rd: Dict) -> str:
        """
        Extract evaluator reasoning from the grader model's raw response.

        For score_model graders the API sets the top-level ``reason`` to
        *null* but the actual chain-of-thought lives inside::

            rd["sample"]["output"][0]["content"]   ->  JSON string
            {"steps": [{"description": ..., "conclusion": ...}, ...],
             "result": <float>}

        Returns a formatted string or "" if nothing is found.
        """
        try:
            sample = rd.get("sample")
            if not sample or not isinstance(sample, dict):
                return ""

            outputs = sample.get("output")
            if not outputs or not isinstance(outputs, list):
                return ""

            # The assistant message from the grader model
            content = None
            for msg in outputs:
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    break
            if not content:
                # Fall back to first entry if role doesn't match
                first = outputs[0]
                if isinstance(first, dict):
                    content = first.get("content", "")
            if not content:
                return ""

            # Parse the JSON content
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return ""

            steps = parsed.get("steps")
            if not isinstance(steps, list) or not steps:
                return ""

            parts = []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                desc = step.get("description", "")
                conc = step.get("conclusion", "")
                if desc:
                    parts.append(f"• {desc}")
                if conc:
                    parts.append(f"  → {conc}")

            return "\n".join(parts)
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            return ""

    def _retrieve_results_with_client(
        self,
        openai_client,
        eval_id: str,
        run_id: str,
    ) -> Dict[str, Any]:
        """
        Retrieve per-row LLM-as-judge scores from a completed
        Foundry evaluation run using an already-open client.

        Returns:
            Dict with keys:
                - aggregated: { evaluator_name: avg_score, ... }
                - per_row: [ { row_index, scores: { evaluator: { score, reason } } } ]
                - total_rows: int
        """
        logger.info(
            f"[Foundry] Retrieving detailed scores for eval={eval_id}, run={run_id}"
        )

        output_items_page = openai_client.evals.runs.output_items.list(
            eval_id=eval_id,
            run_id=run_id,
        )

        per_row: List[Dict] = []
        score_totals: Dict[str, float] = {}
        score_counts: Dict[str, int] = {}

        for idx, item in enumerate(output_items_page):
            row_scores: Dict[str, Any] = {}

            # Debug: log raw structure of the first output item
            if idx == 0:
                try:
                    raw_dump = item.model_dump() if hasattr(item, "model_dump") else str(item)
                    logger.info(f"[Foundry] First output_item structure: {json.dumps(raw_dump, default=str)[:1000]}")
                except Exception:
                    logger.info(f"[Foundry] First output_item type: {type(item)}")

            results = getattr(item, "results", None) or []
            if isinstance(results, dict):
                results = [results]

            for r_idx, result in enumerate(results):
                # Normalise to dict
                if hasattr(result, "model_dump"):
                    rd = result.model_dump()
                elif hasattr(result, "__dict__"):
                    rd = vars(result)
                elif isinstance(result, dict):
                    rd = result
                else:
                    continue

                # Debug: log keys & steps of first result in first item
                if idx == 0 and r_idx == 0:
                    logger.info(
                        f"[Foundry] First result dict keys: {list(rd.keys())}"
                    )
                    if "steps" in rd:
                        logger.info(
                            f"[Foundry] steps sample: "
                            f"{json.dumps(rd['steps'][:2], default=str)[:500]}"
                        )

                name = rd.get("name", "") or rd.get("evaluator_name", "")
                if not name:
                    continue

                # Extract score — check top-level, then 'result' (float for
                # score_model graders), then nested 'result' dict
                score = rd.get("score")
                if score is None:
                    score = rd.get("rating")
                if score is None:
                    result_val = rd.get("result")
                    if isinstance(result_val, (int, float)):
                        score = result_val

                # Extract reasoning — standard keys first
                reason = (
                    rd.get("reason", "")
                    or rd.get("explanation", "")
                    or rd.get("rationale", "")
                )

                # score_model graders: the real reasoning lives inside
                # sample.output[0].content as a JSON string with
                # {"steps": [{"description": ..., "conclusion": ...}], "result": float}
                if not reason:
                    reason = self._extract_reason_from_sample(rd)

                # Fallback: nested 'result' dict (older API shapes)
                if score is None and isinstance(rd.get("result"), dict):
                    nested = rd["result"]
                    score = nested.get("score") or nested.get("rating")
                    reason = reason or nested.get("reason", "") or nested.get("explanation", "")

                if score is None:
                    continue

                try:
                    score_val = float(score)
                except (ValueError, TypeError):
                    continue

                row_scores[name] = {
                    "score": score_val,
                    "reason": str(reason)[:500] if reason else "",
                }

                score_totals[name] = score_totals.get(name, 0.0) + score_val
                score_counts[name] = score_counts.get(name, 0) + 1

            per_row.append({"row_index": idx, "scores": row_scores})

        # Compute averages
        aggregated: Dict[str, float] = {}
        for name in score_totals:
            if score_counts[name] > 0:
                aggregated[name] = round(
                    score_totals[name] / score_counts[name], 2
                )

        logger.info(
            f"[Foundry] Retrieved scores for {len(per_row)} rows.  "
            f"Aggregated: {aggregated}"
        )

        return {
            "aggregated": aggregated,
            "per_row": per_row,
            "total_rows": len(per_row),
        }

    def retrieve_results(
        self,
        eval_id: str,
        run_id: str,
    ) -> Dict[str, Any]:
        """
        Public API: retrieve per-row scores from a completed Foundry run.

        Opens its own AIProjectClient context so it can be called
        independently of submit_evaluation.
        """
        credential = DefaultAzureCredential()
        with (
            AIProjectClient(
                endpoint=self.project_endpoint,
                credential=credential,
            ) as project_client,
            project_client.get_openai_client() as openai_client,
        ):
            return self._retrieve_results_with_client(
                openai_client, eval_id, run_id
            )


# ---------------------------------------------------------------------------
# Factory helper used by routes.py
# ---------------------------------------------------------------------------

def _resolve_env_var(value: str) -> str:
    """Resolve ${VAR_NAME} references in config values."""
    if not value:
        return value
    # Full-value pattern: "${VAR}"
    if value.startswith('${') and value.endswith('}'):
        env_var = value[2:-1]
        return os.getenv(env_var, value)
    # Inline pattern: "prefix ${VAR} suffix"
    def _repl(m):
        return os.getenv(m.group(1), m.group(0))
    return re.sub(r'\$\{(\w+)\}', _repl, value)


def create_foundry_evaluator_from_config(
    settings: Dict[str, Any],
) -> Optional["FoundryEvaluator"]:
    """
    Create a FoundryEvaluator from the app's settings dict.
    Returns None if Foundry is not configured or SDK is missing.
    """
    if not _FOUNDRY_AVAILABLE:
        return None

    foundry_cfg = settings.get("foundry", {})
    endpoint = _resolve_env_var(foundry_cfg.get("project_endpoint", ""))
    deployment = _resolve_env_var(foundry_cfg.get("judge_deployment", ""))
    grader_model = _resolve_env_var(foundry_cfg.get("grader_model", "")) or None

    if not endpoint or not deployment:
        logger.info("[Foundry] Not configured — skipping (set foundry.project_endpoint and foundry.judge_deployment in settings.yaml)")
        return None

    try:
        return FoundryEvaluator(
            project_endpoint=endpoint,
            deployment_name=deployment,
            grader_model=grader_model,
        )
    except Exception as e:
        logger.warning(f"[Foundry] Failed to initialise: {e}")
        return None
