"""
Custom evaluator helpers for domain-specific migration evaluation.

Provides three patterns:
1. create_judge_evaluator() — LLM-as-Judge with custom criteria (5 min setup)
2. load_prompty_evaluator() — Prompty-based structured judge (10 min setup)
3. CodeEvaluator — Decorator for code-based evaluators (15 min setup)
"""

import os
import json
import re
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Pattern 1: LLM-as-Judge with custom criteria
# ---------------------------------------------------------------------------

def create_judge_evaluator(
    name: str,
    criteria: str,
    model: str | None = None,
    endpoint: str | None = None,
    api_key: str | None = None,
) -> dict:
    """
    Create an LLM-as-Judge evaluator from plain-text criteria.

    The judge receives each model response along with your criteria
    description and returns a score (1-5) with reasoning.

    Args:
        name: Metric name (e.g., "citation_compliance", "brand_tone").
        criteria: Plain-text description of scoring criteria.
            Include a 1-5 rubric for best results.
        model: Judge model name (default: EVAL_MODEL_DEPLOYMENT env var or "gpt-4.1").
        endpoint: Azure OpenAI endpoint (default: from env).
        api_key: API key (default: from env or Entra ID).

    Returns:
        Evaluator dict compatible with MigrationEvaluator's custom_evaluators.

    Example:
        judge = create_judge_evaluator(
            name="technical_accuracy",
            criteria="Score 1-5 on technical accuracy. 5=all facts correct, 1=major errors."
        )
    """
    judge_prompt = f"""You are an expert evaluator. Score the following response based on these criteria:

{criteria}

Response to evaluate:
{{{{response}}}}

{{{{#if context}}}}
Context provided:
{{{{context}}}}
{{{{/if}}}}

{{{{#if query}}}}
Original query:
{{{{query}}}}
{{{{/if}}}}

Return ONLY a JSON object with exactly these fields:
{{"score": <number 1-5>, "reason": "<brief explanation>"}}"""

    return {
        "name": name,
        "type": "llm_judge",
        "prompt": judge_prompt,
        "criteria": criteria,
        "model": model or os.getenv("EVAL_MODEL_DEPLOYMENT", "gpt-4.1"),
    }


# ---------------------------------------------------------------------------
# Pattern 2: Prompty-based evaluator
# ---------------------------------------------------------------------------

def load_prompty_evaluator(
    prompty_path: str,
    **static_inputs: Any,
) -> dict:
    """
    Load a .prompty file as a custom evaluator.

    The .prompty file defines the judge prompt template with YAML front matter.
    Static inputs (e.g., brand_guidelines) are bound at load time.

    Args:
        prompty_path: Path to .prompty file.
        **static_inputs: Key-value pairs to bind into the prompt template.

    Returns:
        Evaluator dict compatible with MigrationEvaluator's custom_evaluators.

    Example:
        eval = load_prompty_evaluator(
            "src/evaluate/prompts/brand_tone.prompty",
            brand_guidelines="Professional, warm, no jargon."
        )
    """
    with open(prompty_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split YAML front matter from prompt body
    parts = content.split("---", 2)
    if len(parts) >= 3:
        import yaml
        metadata = yaml.safe_load(parts[1])
        prompt_body = parts[2].strip()
    else:
        metadata = {}
        prompt_body = content

    name = metadata.get("name", os.path.basename(prompty_path).replace(".prompty", ""))

    return {
        "name": name,
        "type": "prompty",
        "path": prompty_path,
        "prompt": prompt_body,
        "metadata": metadata,
        "static_inputs": static_inputs,
    }


# ---------------------------------------------------------------------------
# Pattern 3: Code-based evaluator (decorator)
# ---------------------------------------------------------------------------

class CodeEvaluator:
    """
    Decorator to create a code-based evaluator from a function.

    The decorated function receives `response` (str) and optional kwargs
    (query, context, expected_output, etc.) and must return a dict with
    at least {"score": float, "reason": str}.

    Example:
        @CodeEvaluator(name="json_check")
        def check_json(response: str, **kwargs) -> dict:
            try:
                json.loads(response)
                return {"score": 1.0, "reason": "Valid JSON"}
            except:
                return {"score": 0.0, "reason": "Invalid JSON"}
    """

    def __init__(self, name: str):
        self.name = name

    def __call__(self, func: Callable) -> dict:
        return {
            "name": self.name,
            "type": "code",
            "function": func,
            "doc": func.__doc__ or "",
        }


# ---------------------------------------------------------------------------
# Helper: Run custom evaluators on a single response
# ---------------------------------------------------------------------------

def evaluate_custom(
    response: str,
    evaluators: list[dict],
    query: str = "",
    context: str = "",
    expected_output: str = "",
    judge_client=None,
) -> dict[str, dict]:
    """
    Run a list of custom evaluators on a single response.

    Args:
        response: Model response to evaluate.
        evaluators: List of evaluator dicts from create_judge_evaluator,
            load_prompty_evaluator, or @CodeEvaluator.
        query: Original user query (optional).
        context: Retrieved context (optional, for RAG).
        expected_output: Reference answer (optional).
        judge_client: OpenAI client for LLM-based evaluators.

    Returns:
        Dict mapping evaluator name to {"score": float, "reason": str}.
    """
    results = {}

    for ev in evaluators:
        name = ev["name"]
        ev_type = ev["type"]

        if ev_type == "code":
            fn = ev["function"]
            result = fn(
                response=response,
                query=query,
                context=context,
                expected_output=expected_output,
            )
            results[name] = result

        elif ev_type in ("llm_judge", "prompty"):
            if judge_client is None:
                from src.clients import create_client
                judge_client = create_client(ev.get("model", "gpt-4.1"))

            prompt = ev["prompt"]
            # Simple template substitution
            prompt = prompt.replace("{{response}}", response)
            prompt = prompt.replace("{{query}}", query)
            prompt = prompt.replace("{{context}}", context)
            # Handle static inputs for prompty evaluators
            for k, v in ev.get("static_inputs", {}).items():
                prompt = prompt.replace(f"{{{{{k}}}}}", str(v))

            # Remove unresolved Handlebars conditionals
            prompt = re.sub(
                r"\{\{#if.*?\}\}.*?\{\{/if\}\}", "", prompt, flags=re.DOTALL
            )

            try:
                resp = judge_client.chat.completions.create(
                    model=ev.get("model", "gpt-4.1"),
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=256,
                )
                raw = resp.choices[0].message.content.strip()
                parsed = json.loads(raw)
                results[name] = {
                    "score": float(parsed.get("score", 0)),
                    "reason": parsed.get("reason", raw),
                }
            except (json.JSONDecodeError, Exception) as e:
                results[name] = {"score": 0.0, "reason": f"Judge error: {e}"}

    return results
