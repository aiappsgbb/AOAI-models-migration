#!/usr/bin/env python3
"""
generate_gemini_prompts.py — Generate Gemini 3 Flash Preview prompts for all topics
====================================================================================

Reads GPT-4 prompts as the base and applies Gemini 3 Flash-specific adaptations:

1. Updates model identification headers (GPT-4.x → Gemini 3 Flash Preview)
2. Removes `seed` parameter references (not supported by Gemini)
3. Preserves ALL domain content exactly (taxonomies, entities, personas, rules,
   JSON examples) — these are coupled with the test data
4. Preserves explicit Chain-of-Thought policy (Gemini benefits from step-by-step)
5. Preserves structured output requirements (Gemini excels at JSON generation)

Gemini 3 Flash best practices applied:
  ✓ Explicit CoT reasoning instructions retained
  ✓ Clear section headings with Markdown structure
  ✓ Few-shot examples preserved (2–3 per prompt)
  ✓ Strict JSON output schemas maintained
  ✓ temperature=0.1 for reproducibility (seed removed — unsupported)
  ✓ Standard 'system' role (not 'developer')
  ✓ max_tokens (not max_completion_tokens)

Usage:
    python tools/generate_gemini_prompts.py
"""

from __future__ import annotations

import os
import re
import sys

# ── Paths ────────────────────────────────────────────────────────────────────

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
PROMPTS_DIR = os.path.join(PROJECT_ROOT, "prompts")

PROMPT_FILES = [
    "classification_agent_system.md",
    "dialog_agent_system.md",
    "rag_agent_system.md",
    "tool_calling_agent_system.md",
]

SOURCE_MODEL = "gpt4"
TARGET_MODEL = "gemini3_flash"

# All archived topics
ARCHIVED_TOPICS = [
    "agente_de_contact_center_que_responde_preguntas_sobre_el_catálogo_de_películas_de_netflix",
    "agente_telco",
    "ai_agent_to_answer_questions_about_ai",
    "a_warm_ai_assistant_that_responses_movistar_customers_questions_about_their_invoices_and_billings",
    "red_sea_diving_travel",
    "telco_customer_service",
]

# All locations: active topic + archived topics
LOCATIONS = [PROMPTS_DIR] + [
    os.path.join(PROMPTS_DIR, "topics", topic) for topic in ARCHIVED_TOPICS
]


# ── ANSI colours ─────────────────────────────────────────────────────────────

class C:
    _ok = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    GREEN = "\033[92m" if _ok else ""
    YELLOW = "\033[93m" if _ok else ""
    RED = "\033[91m" if _ok else ""
    CYAN = "\033[96m" if _ok else ""
    BOLD = "\033[1m" if _ok else ""
    DIM = "\033[2m" if _ok else ""
    R = "\033[0m" if _ok else ""


# ── Gemini adaptation logic ──────────────────────────────────────────────────

def adapt_to_gemini(content: str) -> str:
    """Transform a GPT-4 prompt to Gemini 3 Flash Preview style.

    The adaptations are intentionally conservative:
    - ALL domain content (taxonomies, entities, personas, rules, examples) is
      preserved byte-for-byte because it's coupled with the test data.
    - Only model-identification headers and inference parameter references change.

    Gemini 3 Flash specific changes:
    1. Model identification → "Gemini 3 Flash Preview"
    2. seed parameter → removed (not supported by Gemini)
    3. Everything else → preserved (CoT, structured output, few-shot examples)
    """
    result = content

    # ── 1. Header: model name ────────────────────────────────────────────

    # "# GPT-4.x Optimized <Type> Agent System Prompt"
    result = re.sub(
        r"# GPT-4(?:\.x|\.1)? Optimized",
        "# Gemini 3 Flash Preview Optimized",
        result,
    )

    # "# Target Model Family: GPT-4.x"
    result = re.sub(
        r"# Target Model Family: GPT-4(?:\.x|\.1)?",
        "# Target Model Family: Gemini",
        result,
    )

    # ── 2. Seed parameter removal ────────────────────────────────────────

    # Remove "#   - seed: 12345" line from recommended parameter blocks
    result = re.sub(
        r"^#\s*-\s*seed:\s*\d+\s*$\n?",
        "",
        result,
        flags=re.MULTILINE,
    )

    # Final instructions: "assuming temperature=0.1 and seed=12345"
    # → "assuming temperature=0.1"
    result = re.sub(
        r"(temperature=0\.1)\s+and\s+seed=\d+",
        r"\1",
        result,
    )

    # Variant: "temperature=0.1, seed=12345" → "temperature=0.1"
    result = re.sub(
        r"(temperature=0\.1),\s*seed=\d+",
        r"\1",
        result,
    )

    return result


# ── Validation ───────────────────────────────────────────────────────────────

def validate_output(content: str, rel_path: str) -> list[str]:
    """Check for residual patterns that should have been transformed."""
    warnings = []

    # Check for remaining GPT-4 header references
    if re.search(r"# GPT-4(?:\.x|\.1)? Optimized", content):
        warnings.append(f"{rel_path}: residual 'GPT-4.x Optimized' in header")
    if re.search(r"# Target Model Family: GPT-4", content):
        warnings.append(f"{rel_path}: residual 'Target Model Family: GPT-4'")

    # Check for remaining seed parameter references
    if re.search(r"^#\s*-\s*seed:\s*\d+", content, re.MULTILINE):
        warnings.append(f"{rel_path}: residual 'seed' in parameter block")
    if re.search(r"temperature=0\.1\s+and\s+seed=\d+", content):
        warnings.append(f"{rel_path}: residual 'and seed=...' in instructions")

    return warnings


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{C.BOLD}{C.CYAN}{'═' * 60}{C.R}")
    print(f"{C.BOLD}{C.CYAN}  Generate Gemini 3 Flash Preview Prompts{C.R}")
    print(f"{C.BOLD}{C.CYAN}{'═' * 60}{C.R}")
    print(f"\n  Source model: {C.DIM}{SOURCE_MODEL}{C.R}")
    print(f"  Target model: {C.BOLD}{TARGET_MODEL}{C.R}")
    print(f"  Locations:    {len(LOCATIONS)} (1 active + {len(ARCHIVED_TOPICS)} archived)\n")

    created: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    all_warnings: list[str] = []

    for location in LOCATIONS:
        src_dir = os.path.join(location, SOURCE_MODEL)
        dst_dir = os.path.join(location, TARGET_MODEL)

        # Friendly name for display
        if location == PROMPTS_DIR:
            loc_name = "active topic"
        else:
            loc_name = os.path.basename(location)[:50]

        if not os.path.isdir(src_dir):
            skipped.append(f"[{loc_name}] No {SOURCE_MODEL}/ directory")
            continue

        os.makedirs(dst_dir, exist_ok=True)

        for fname in PROMPT_FILES:
            src_file = os.path.join(src_dir, fname)
            dst_file = os.path.join(dst_dir, fname)

            if not os.path.isfile(src_file):
                skipped.append(f"[{loc_name}] {fname} not found in {SOURCE_MODEL}/")
                continue

            try:
                with open(src_file, "r", encoding="utf-8") as f:
                    content = f.read()

                adapted = adapt_to_gemini(content)

                # Validate
                rel_path = os.path.relpath(dst_file, PROJECT_ROOT)
                warnings = validate_output(adapted, rel_path)
                all_warnings.extend(warnings)

                with open(dst_file, "w", encoding="utf-8") as f:
                    f.write(adapted)

                created.append(rel_path)
                print(f"  {C.GREEN}✓{C.R} {rel_path}")

            except Exception as e:
                rel = os.path.relpath(dst_file, PROJECT_ROOT)
                errors.append(f"{rel}: {e}")
                print(f"  {C.RED}✗{C.R} {rel}: {e}")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{C.BOLD}{'─' * 60}{C.R}")
    print(f"  {C.GREEN}✅ Created:{C.R} {len(created)} files")
    if skipped:
        print(f"  {C.YELLOW}⚠️  Skipped:{C.R} {len(skipped)}")
        for s in skipped:
            print(f"     {C.DIM}{s}{C.R}")
    if errors:
        print(f"  {C.RED}❌ Errors:{C.R} {len(errors)}")
        for e in errors:
            print(f"     {e}")
    if all_warnings:
        print(f"\n  {C.YELLOW}⚠️  Validation warnings:{C.R}")
        for w in all_warnings:
            print(f"     {w}")
    else:
        print(f"  {C.GREEN}✅ Validation:{C.R} No residual GPT-4 or seed references found")

    print(f"\n{C.BOLD}{'─' * 60}{C.R}\n")


if __name__ == "__main__":
    main()
