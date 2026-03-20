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
    "mistral": (
        "Mistral family — base best practices:\n"
        "- Use explicit, detailed system prompts — Mistral models follow\n"
        "  well-structured instructions closely\n"
        "- Provide clear Chain-of-Thought (CoT) instructions when multi-step\n"
        "  reasoning is needed — Mistral benefits from explicit reasoning guidance\n"
        "- Use standard 'system' role and 'max_tokens' (NOT developer role,\n"
        "  NOT max_completion_tokens)\n"
        "- Include concrete examples (few-shot) — 2-3 high-quality examples\n"
        "  significantly improve output consistency\n"
        "- Be explicit about output format (JSON schema, Markdown structure)\n"
        "  — include a complete example of the expected output\n"
        "- Keep instructions focused and well-organized with clear section\n"
        "  headings — avoid burying critical rules in long paragraphs\n"
        "- Use temperature=0.1 and seed for reproducibility\n"
        "- Mistral excels at multilingual tasks — you can instruct in one\n"
        "  language and request output in another"
    ),
    "gemini": (
        "Gemini family — base best practices:\n"
        "- Use explicit Chain-of-Thought (CoT) instructions for complex\n"
        "  multi-step reasoning — Gemini benefits from step-by-step guidance\n"
        "- Provide detailed, well-structured system prompts with clear\n"
        "  section headings and formatting rules\n"
        "- Include 2-3 concrete few-shot examples for best consistency\n"
        "- Use standard 'system' role and 'max_tokens' (NOT developer role,\n"
        "  NOT max_completion_tokens)\n"
        "- Gemini supports reasoning_effort (none/low/medium/high) for\n"
        "  controlling reasoning depth — use when applicable\n"
        "- Be explicit about output format (JSON schema, Markdown) —\n"
        "  include a complete example of the expected output\n"
        "- Do NOT rely on 'seed' parameter — not supported by Gemini\n"
        "- Gemini excels at multilingual and multimodal tasks\n"
        "- Use temperature=0.1 for reproducibility"
    ),
    "phi": (
        "Phi family (SLM) — base best practices:\n"
        "- Small Language Model (14B parameters) — compensate with explicit,\n"
        "  well-structured instructions and concrete examples\n"
        "- Use explicit Chain-of-Thought (CoT) reasoning instructions —\n"
        "  Phi does NOT have native reasoning; spell out step-by-step logic\n"
        "- Include 2-3 high-quality few-shot examples for consistency\n"
        "- Keep prompts focused and well-organized with clear Markdown\n"
        "  section headings — context window is 16K tokens (much smaller\n"
        "  than GPT-4.1's 1M), so every token counts\n"
        "- Be very explicit about JSON output schema with complete examples\n"
        "  — include exact field names, types, and constraints\n"
        "- Use standard 'system' role and 'max_tokens' (NOT developer role,\n"
        "  NOT max_completion_tokens)\n"
        "- Use temperature=0.1 for reproducibility; seed is NOT supported\n"
        "- Primarily optimised for English; limited multilingual capability\n"
        "- Excels at mathematical reasoning and complex logic\n"
        "- Uses chat format with <|im_start|> / <|im_end|> tokens internally\n"
        "  — structure prompts as clear system/user/assistant turns"
    ),
    "realtime": (
        "Realtime (speech-to-speech) family — base best practices:\n"
        "- These are VOICE models — prompts are session 'instructions' sent\n"
        "  via the Realtime API session.update event\n"
        "- Use clear, labeled sections with # headers so the model can\n"
        "  find and follow rules: Role & Objective, Personality & Tone,\n"
        "  Language, Unclear Audio, Instructions, Safety & Escalation\n"
        "- Use short bullet points — NEVER long paragraphs (bullets > paragraphs)\n"
        "- Keep spoken responses to 2-3 sentences per turn\n"
        "- Include explicit pacing instructions: 'natural brisk pace, not slow'\n"
        "- Add a Variety section: 'Do not repeat the same phrase twice'\n"
        "- Add Unclear Audio handling with sample clarification phrases\n"
        "- Add Language section: mirror user's language, default to English\n"
        "- For tool-calling: include preamble instructions (e.g. 'Let me check\n"
        "  that for you') before every tool call\n"
        "- For classification: output TEXT-ONLY JSON, never spoken audio\n"
        "- Use CAPITALIZED text for critical rules (NEVER, FORBIDDEN, etc.)\n"
        "- Use temperature=0.8 for natural speech variation\n"
        "- Supported voices: alloy, ash, ballad, coral, echo, sage, shimmer,\n"
        "  verse, marin, cedar (recommend marin or cedar for best quality)"
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

    # ── Mistral family ─────────────────────────────────────────────────
    "Mistral-Large-3": (
        "\nModel-specific guidance (Mistral-Large-3):\n"
        "- Flagship model of the Mistral family — 128 K-token context window\n"
        "- Excellent instruction following, strong at classification, extraction,\n"
        "  and structured output tasks (JSON mode)\n"
        "- Strong multilingual capabilities across European and Asian languages\n"
        "  — ideal for multilingual classification and dialog\n"
        "- Function/tool calling support with parallel tool invocation\n"
        "- Very good at code generation and analytical reasoning\n"
        "- Explicit Chain-of-Thought prompting improves complex tasks —\n"
        "  unlike GPT-5 models, do NOT rely on implicit reasoning\n"
        "- Include 2-3 concrete few-shot examples for best results —\n"
        "  Mistral is an excellent few-shot learner\n"
        "- Structured outputs: always specify the complete JSON schema\n"
        "  with field types and constraints in the system prompt\n"
        "- Use clear section headers (##) to organize long prompts —\n"
        "  Mistral respects Markdown structure well\n"
        "- Competitive cost-performance ratio — good alternative to GPT-4.1\n"
        "  for high-volume production workloads"
    ),

    # ── Gemini family ───────────────────────────────────────────────────
    "gemini-3-flash-preview": (
        "\nModel-specific guidance (Gemini 3 Flash Preview):\n"
        "- Fast, cost-effective model from Google — optimised for low-latency\n"
        "  inference with strong quality\n"
        "- 1 M-token context window — can process very large inputs\n"
        "- Multimodal capable (text, images, audio, video) — mention when\n"
        "  relevant to the task\n"
        "- Strong structured output support — use response_format with\n"
        "  json_object mode for reliable JSON generation\n"
        "- Function/tool calling support with parallel tool invocation\n"
        "- Supports reasoning_effort (none/low/medium/high) for controlling\n"
        "  inference depth — useful for complex analytical tasks\n"
        "- Explicit CoT prompting improves complex tasks — do NOT rely on\n"
        "  implicit reasoning like GPT-5 models\n"
        "- Include concrete few-shot examples (2-3) for best results\n"
        "- Competitive cost-performance ratio — good alternative for\n"
        "  high-volume production workloads"
    ),

    # ── Phi family (SLMs) ──────────────────────────────────────────────
    "Phi-4": (
        "\nModel-specific guidance (Phi-4 — 14B SLM):\n"
        "- State-of-the-art SLM specialising in complex reasoning, math,\n"
        "  and structured output — outperforms many larger models on MATH\n"
        "  and GPQA benchmarks\n"
        "- 16K context window — keep prompts concise and prioritise the\n"
        "  most critical rules; avoid embedding large reference documents\n"
        "- Dense decoder-only Transformer (14B params) — strong quality\n"
        "  for its size but needs more explicit guidance than frontier models\n"
        "- Trained on high-quality synthetic data, academic books, and Q&A\n"
        "  — particularly strong at logical reasoning and code (Python)\n"
        "- Include complete JSON schema examples in the prompt — Phi-4\n"
        "  follows schemas reliably when they are fully specified\n"
        "- Explicit CoT prompting significantly improves multi-step tasks\n"
        "- Few-shot examples (2-3) dramatically improve output consistency\n"
        "- Cost-effective alternative for classification, extraction, and\n"
        "  structured output tasks where frontier models are overkill\n"
        "- MIT licensed — flexible for commercial use"
    ),

    # ── Realtime (speech-to-speech) models ─────────────────────────────
    "gpt-realtime": (
        "\nModel-specific guidance (gpt-realtime — speech-to-speech):\n"
        "- First-generation GA realtime model for speech-to-speech\n"
        "- Prompts should be explicit and detailed — include sample phrases\n"
        "  for greetings, clarifications, and tool preambles\n"
        "- Supports server_vad, semantic_vad, and manual turn detection\n"
        "- Supports function calling and image input\n"
        "- Session instructions via session.update — voice cannot change\n"
        "  after first audio output in a session\n"
        "- Maximum session duration: 30 minutes\n"
        "- Best with alloy voice for general use\n"
        "- Include 3-5 varied sample phrases per conversation phase\n"
        "  to avoid robotic repetition"
    ),
    "gpt-realtime-1.5": (
        "\nModel-specific guidance (gpt-realtime-1.5 — latest speech-to-speech):\n"
        "- Latest and most capable realtime model — improved instruction\n"
        "  following, more natural and expressive speech output\n"
        "- Follows complex instructions more reliably than gpt-realtime —\n"
        "  prompts can be MORE CONCISE while achieving better results\n"
        "- Better at tool calling and multi-step conversations\n"
        "- Supports semantic_vad for more natural turn-taking\n"
        "- Supports MCP server integration for external tools\n"
        "- Recommended voices: marin or cedar for best quality\n"
        "- Session instructions can be shorter and more declarative\n"
        "  compared to gpt-realtime — focus on WHAT not HOW"
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
    """Return ``'gpt4'``, ``'gpt5'``, ``'mistral'``, ``'gemini'``, or ``'realtime'`` family string for a model key."""
    if model_family:
        return model_family
    if any(x in model_key.lower() for x in ("realtime",)):
        return "realtime"
    if any(x in model_key.lower() for x in ("gpt5", "o1", "o3", "o4", "reasoning")):
        return "gpt5"
    if any(x in model_key.lower() for x in ("phi",)):
        return "phi"
    if any(x in model_key.lower() for x in ("mistral",)):
        return "mistral"
    if any(x in model_key.lower() for x in ("gemini",)):
        return "gemini"
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
