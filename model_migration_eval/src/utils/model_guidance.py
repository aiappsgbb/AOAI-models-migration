"""
model_guidance.py — Centralised, two-tier prompt-engineering guidance
=====================================================================

Tier 1 — **FAMILY_GUIDANCE**
    Base best practices shared by every model in a family (``gpt4`` or ``gpt5``).

Tier 2 — **MODEL_GUIDANCE**
    Deployment-specific addenda keyed by Azure OpenAI deployment / model name
    (e.g. ``gpt-4.1``, ``gpt-4o``, ``gpt-5.1``).

The public helper :func:`get_guidance` merges both tiers into a single text
block ready to be inserted into the meta-prompt that generates system prompts.

Both ``src/utils/prompt_manager.py`` and ``tools/import_topic.py`` import from
here so there is a **single source of truth**.
"""

from __future__ import annotations

from typing import Optional


# =====================================================================
# Tier 1 — Family-level best practices
# =====================================================================

FAMILY_GUIDANCE: dict[str, str] = {
    "gpt4": (
        "GPT-4.x family — base best practices:\n"
        "- Use explicit Chain-of-Thought (CoT) instructions\n"
        "- Provide detailed formatting rules and examples\n"
        "- Use Markdown tables for taxonomies\n"
        "- Be verbose with edge-case handling\n"
        "- Include concrete JSON output examples\n"
        "- Specify temperature=0.1 and seed for reproducibility\n"
        "- Use max_tokens for output length control"
    ),
    "gpt5": (
        "GPT-5.x family — base best practices:\n"
        "- Leverage native reasoning (no explicit CoT needed)\n"
        "- Use YAML-based schema definitions for structure\n"
        "- Streamlined, concise instructions — focus on WHAT not HOW\n"
        "- Use <system_configuration> blocks for model params\n"
        "- Use max_completion_tokens instead of max_tokens"
    ),
}


# =====================================================================
# Tier 2 — Deployment-specific addenda
# =====================================================================

MODEL_GUIDANCE: dict[str, str] = {
    # ── GPT-4 family ──────────────────────────────────────────────────
    "gpt-4.1": (
        "\nModel-specific guidance (GPT-4.1):\n"
        "- Strongest instruction follower in the GPT-4 family — system-prompt\n"
        "  rules take absolute precedence over user-message overrides\n"
        "- Excellent agentic capabilities: multi-step planning, tool chaining\n"
        "- Supports up to 1 M-token context — you can embed large reference\n"
        "  documents directly in the prompt\n"
        "- Use the #inner_thoughts pattern for structured internal reasoning\n"
        "  before producing the final answer\n"
        "- Ideal for complex classification with many categories and nuanced\n"
        "  decision rules\n"
        "- Strong structured output (JSON mode) — be very explicit about\n"
        "  the expected schema and field types"
    ),
    "gpt-4o": (
        "\nModel-specific guidance (GPT-4o):\n"
        "- Optimised for speed and low latency — ideal for real-time and\n"
        "  user-facing applications\n"
        "- Multimodal: can process images and audio alongside text — mention\n"
        "  when relevant to the task\n"
        "- Strong creative writing and natural conversational tone\n"
        "- Shorter context window than GPT-4.1 — keep prompts focused and\n"
        "  avoid unnecessary verbosity\n"
        "- Excellent few-shot learner — include 2-3 high-quality examples\n"
        "  rather than exhaustive instructions\n"
        "- Balanced performance/cost — great default for most tasks"
    ),
    "gpt-4o-mini": (
        "\nModel-specific guidance (GPT-4o-mini):\n"
        "- Most cost-effective in the GPT-4 family — ideal for high-volume\n"
        "  pipelines and batch processing\n"
        "- Excellent at classification, extraction, and simple structured\n"
        "  output tasks\n"
        "- Shorter, more concise prompts yield better results — cut\n"
        "  any non-essential instructions\n"
        "- Fewer few-shot examples (1-2) work better than many\n"
        "- Avoid very long system prompts — prioritise the most critical\n"
        "  rules and constraints"
    ),
    "gpt-4.1-mini": (
        "\nModel-specific guidance (GPT-4.1-mini):\n"
        "- Most cost-effective in the GPT-4.1 generation — built for\n"
        "  high-throughput and batch pipelines\n"
        "- Excellent at classification, extraction, and structured output\n"
        "- Supports the same 1 M-token context window as GPT-4.1 but\n"
        "  produces shorter outputs — keep expected responses concise\n"
        "- Shorter, focused prompts yield the best results — eliminate\n"
        "  any non-essential instructions\n"
        "- Fewer few-shot examples (1-2) are better than many\n"
        "- Excellent for latency-sensitive applications and cost-aware\n"
        "  production workloads"
    ),
    "gpt-4.1-nano": (
        "\nModel-specific guidance (GPT-4.1-nano):\n"
        "- Fastest and cheapest model in the GPT-4.1 family\n"
        "- Best for ultra-simple tasks: tagging, routing, binary\n"
        "  classification, and short extractive answers\n"
        "- Keep prompts extremely short and direct — one clear task\n"
        "- Avoid multi-step reasoning or complex output schemas\n"
        "- Use as a pre-filter / router before more capable models"
    ),

    # ── GPT-5 family ──────────────────────────────────────────────────
    "gpt-5": (
        "\nModel-specific guidance (GPT-5 flagship):\n"
        "- Most capable model overall — excels at complex multi-step tasks,\n"
        "  nuanced analysis, and creative synthesis\n"
        "- Superior instruction following and ambiguity resolution\n"
        "- Native deep reasoning without requiring reasoning_effort param\n"
        "- Excellent at synthesising information from very large contexts\n"
        "- Can handle open-ended, under-specified tasks effectively\n"
        "- For cost/latency control, optionally set reasoning_effort"
    ),
    "gpt-5.1": (
        "\nModel-specific guidance (GPT-5.1 — reasoning model):\n"
        "- Dedicated reasoning model — uses internal chain-of-thought\n"
        "  before producing its answer\n"
        "- Set reasoning_effort ('low', 'medium', 'high') to balance\n"
        "  quality vs latency vs cost\n"
        "- Do NOT include explicit CoT or step-by-step instructions —\n"
        "  the model already reasons internally\n"
        "- Do NOT set temperature — reasoning models manage their own\n"
        "  sampling strategy\n"
        "- Use max_completion_tokens (not max_tokens)\n"
        "- Prompts should be DECLARATIVE: state the goal and constraints,\n"
        "  not the reasoning steps\n"
        "- Best for complex analytical tasks, math, code, and formal logic"
    ),
    "gpt-5.2": (
        "\nModel-specific guidance (GPT-5.2 — latest flagship):\n"
        "- Latest and most capable flagship model in the GPT-5 family\n"
        "- Superior performance on complex multi-step reasoning, analysis,\n"
        "  and creative generation tasks\n"
        "- Excellent instruction following — detailed system prompts are\n"
        "  respected precisely\n"
        "- Native reasoning without needing reasoning_effort parameter,\n"
        "  but you can set it to control cost/latency trade-off\n"
        "- Handles ambiguity and nuance better than any previous model\n"
        "- Optimal for production workloads needing top-tier quality"
    ),

    # ── o-series reasoning models ─────────────────────────────────────
    "o1": (
        "\nModel-specific guidance (o1 — reasoning model):\n"
        "- Reasoning model — internal chain-of-thought before answering\n"
        "- Do NOT include CoT instructions — model reasons internally\n"
        "- Do NOT set temperature\n"
        "- Use max_completion_tokens instead of max_tokens\n"
        "- Set reasoning_effort to control depth\n"
        "- Declarative prompting: state the goal, not the steps"
    ),
    "o3": (
        "\nModel-specific guidance (o3 — reasoning model):\n"
        "- Advanced reasoning model with deeper internal reasoning\n"
        "- Same guidelines as o1: no CoT, no temperature, use\n"
        "  max_completion_tokens and reasoning_effort\n"
        "- Declarative prompts yield the best results"
    ),
    "o4-mini": (
        "\nModel-specific guidance (o4-mini — reasoning model):\n"
        "- Cost-effective reasoning model — good for analytical tasks\n"
        "  that need reasoning but not maximum capability\n"
        "- Same guidelines: no CoT, no temperature, use\n"
        "  max_completion_tokens and reasoning_effort\n"
        "- Concise, goal-focused prompts"
    ),
}

# Ordered patterns for fallback matching (longest / most specific first)
_MATCH_ORDER: list[str] = sorted(MODEL_GUIDANCE.keys(), key=len, reverse=True)


# =====================================================================
# Public API
# =====================================================================

def resolve_model_family(
    model_key: str,
    model_family: Optional[str] = None,
) -> str:
    """Return ``'gpt4'`` or ``'gpt5'`` family string for a model key."""
    if model_family:
        return model_family
    if any(x in model_key.lower() for x in ("gpt5", "o1", "o3", "o4", "reasoning")):
        return "gpt5"
    return "gpt4"


def _find_model_addendum(deployment_name: Optional[str]) -> str:
    """Return the best-matching model-specific addendum for *deployment_name*.

    Matching logic:
      1. Exact match.
      2. Longest prefix match  (e.g. ``gpt-4.1-mini`` matches before ``gpt-4.1``).
      3. Empty string if nothing matches (family guidance still applies).
    """
    if not deployment_name:
        return ""
    dn = deployment_name.strip().lower()
    # 1. Exact
    for key in MODEL_GUIDANCE:
        if dn == key.lower():
            return MODEL_GUIDANCE[key]
    # 2. Longest prefix
    for key in _MATCH_ORDER:
        if dn.startswith(key.lower()):
            return MODEL_GUIDANCE[key]
    return ""


def get_guidance(
    model_key: str,
    deployment_name: Optional[str] = None,
    model_family: Optional[str] = None,
) -> str:
    """Return combined family + model-specific guidance text.

    Parameters
    ----------
    model_key:
        Internal key (e.g. ``"gpt4"``, ``"gpt41_mini"``).
    deployment_name:
        Azure OpenAI deployment / model name (e.g. ``"gpt-4.1-mini"``).
        Used to look up the per-model addendum.
    model_family:
        Explicit family override (``"gpt4"`` or ``"gpt5"``).
    """
    family = resolve_model_family(model_key, model_family)
    base = FAMILY_GUIDANCE.get(family, FAMILY_GUIDANCE["gpt4"])
    addendum = _find_model_addendum(deployment_name)
    return base + addendum
