#!/usr/bin/env python3
"""
add_model.py — Add a new model to the evaluation framework
============================================================

Interactive CLI tool that:
  1. Asks for model details (key, deployment name, family, parameters).
  2. Validates there are no conflicts with existing model keys.
  3. Adds the model entry to config/settings.yaml.
  4. Copies prompt files from the most similar existing model.
  5. Prints a summary of what was created.

Can also run non-interactively with command-line arguments.

Usage (interactive)
-------------------
    python tools/add_model.py

Usage (non-interactive)
-----------------------
    python tools/add_model.py ^
        --key gpt51 ^
        --deployment "gpt-5.1" ^
        --family gpt5 ^
        --max-tokens 16384 ^
        --temperature 0.1

    # Reasoning model (omits temperature, adds reasoning_effort)
    python tools/add_model.py ^
        --key o4_mini ^
        --deployment "o4-mini" ^
        --family gpt5 ^
        --max-tokens 16384 ^
        --reasoning-effort medium

    # Specify which model to copy prompts from
    python tools/add_model.py ^
        --key gpt45 ^
        --deployment "gpt-4.5" ^
        --family gpt4 ^
        --copy-prompts-from gpt4o

    # Skip prompt copy entirely
    python tools/add_model.py ^
        --key gpt45 ^
        --deployment "gpt-4.5" ^
        --family gpt4 ^
        --no-prompts
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

# ---------------------------------------------------------------------------
# Resolve project root (one level up from tools/)
# ---------------------------------------------------------------------------
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
SETTINGS_PATH = os.path.join(PROJECT_ROOT, "config", "settings.yaml")
PROMPTS_DIR = os.path.join(PROJECT_ROOT, "prompts")

# Prompt file names expected in each model directory
PROMPT_FILES = [
    "classification_agent_system.md",
    "dialog_agent_system.md",
    "rag_agent_system.md",
    "tool_calling_agent_system.md",
]

VALID_FAMILIES = ["gpt4", "gpt5"]
VALID_REASONING_EFFORTS = ["low", "medium", "high"]


# ── Colours ──────────────────────────────────────────────────────────────────
class C:
    """ANSI colour helpers (auto-disabled when stdout is not a TTY)."""
    _enabled = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    BLUE = "\033[94m" if _enabled else ""
    GREEN = "\033[92m" if _enabled else ""
    YELLOW = "\033[93m" if _enabled else ""
    RED = "\033[91m" if _enabled else ""
    CYAN = "\033[96m" if _enabled else ""
    BOLD = "\033[1m" if _enabled else ""
    DIM = "\033[2m" if _enabled else ""
    RESET = "\033[0m" if _enabled else ""


def info(msg: str) -> None:
    print(f"  {C.GREEN}✓{C.RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {C.YELLOW}⚠{C.RESET} {msg}")


def error(msg: str) -> None:
    print(f"  {C.RED}✗{C.RESET} {msg}")


def heading(msg: str) -> None:
    print(f"\n{C.BOLD}{C.BLUE}{msg}{C.RESET}")


def dim(msg: str) -> str:
    return f"{C.DIM}{msg}{C.RESET}"


# ── Settings helpers ─────────────────────────────────────────────────────────

def read_settings() -> str:
    """Read settings.yaml as raw text."""
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return f.read()


def parse_existing_models(text: str) -> dict[str, dict]:
    """
    Lightweight YAML-like parser that extracts model keys and their properties
    from the models: block.  Does NOT use the yaml library to avoid rewriting
    comments and formatting.
    """
    models: dict[str, dict] = {}
    in_models = False
    current_key = None
    indent_model = 0

    for line in text.splitlines():
        stripped = line.lstrip()

        # Detect start of models: block
        if stripped.startswith("models:"):
            in_models = True
            continue

        if not in_models:
            continue

        # Blank or comment-only lines inside the block
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(stripped)

        # A new top-level section (same or less indent than 'models:') ends the block
        if indent <= 2 and not stripped.startswith("-") and ":" in stripped:
            # Check if this is a model key (indent == 4) or a section break
            if indent < 4:
                in_models = False
                continue

        # Model key line (indent == 4, no value after colon or empty value)
        if indent == 4 and ":" in stripped:
            key_part = stripped.split(":")[0].strip()
            if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", key_part):
                current_key = key_part
                indent_model = indent
                models[current_key] = {}
                continue

        # Property line (indent == 6+, belongs to current model)
        if current_key and indent > indent_model and ":" in stripped:
            prop_key, _, prop_val = stripped.partition(":")
            prop_key = prop_key.strip()
            prop_val = prop_val.strip()
            # Strip inline comments
            if "  #" in prop_val:
                prop_val = prop_val[: prop_val.index("  #")].strip()
            # Remove quotes
            prop_val = prop_val.strip('"').strip("'")
            models[current_key][prop_key] = prop_val

    return models


def find_best_prompt_source(family: str, models: dict[str, dict], is_reasoning: bool) -> str | None:
    """
    Pick the best existing model to copy prompts from, based on family match
    and prompt directory existence.

    Priority:
      1. Same family, non-reasoning, has prompt dir with files
      2. Same family, any, has prompt dir with files
      3. Any model with prompt dir with files
    """
    def has_prompts(key: str) -> bool:
        d = os.path.join(PROMPTS_DIR, key)
        return os.path.isdir(d) and any(
            f.endswith(".md") for f in os.listdir(d)
        )

    # Candidates with prompts, grouped by priority
    same_family_normal = []
    same_family_any = []
    other = []

    for key, props in models.items():
        if not has_prompts(key):
            continue
        mf = props.get("model_family", "")
        is_reas = "reasoning_effort" in props
        if mf == family:
            if not is_reas:
                same_family_normal.append(key)
            same_family_any.append(key)
        else:
            other.append(key)

    if same_family_normal:
        return same_family_normal[0]
    if same_family_any:
        return same_family_any[0]
    if other:
        return other[0]
    return None


def build_yaml_block(
    key: str,
    deployment_name: str,
    family: str,
    model_version: str,
    max_tokens: int,
    temperature: float | None,
    reasoning_effort: str | None,
) -> str:
    """Build the YAML text block for a new model entry (4-space key indent)."""
    lines = [
        f"    {key}:",
        f"      deployment_name: \"{deployment_name}\"",
        f"      model_family: \"{family}\"",
    ]
    if model_version:
        lines.append(f"      model_version: \"{model_version}\"")
    lines.append(f"      max_tokens: {max_tokens}")

    if reasoning_effort:
        lines.append(f"      reasoning_effort: \"{reasoning_effort}\"  # low, medium, high")
    elif temperature is not None:
        lines.append(f"      temperature: {temperature}")

    return "\n".join(lines)


def insert_model_in_settings(text: str, yaml_block: str, family: str, models: dict[str, dict]) -> str:
    """
    Insert the new model YAML block into settings.yaml at the right position:
    after the last model of the same family, or at the end of the models block.
    """
    lines = text.splitlines()
    insert_after = -1
    last_model_line = -1
    in_models = False
    current_key = None

    # Find the line index of the last property of the last same-family model
    same_family_last_line = -1
    any_model_last_line = -1

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if stripped.startswith("models:"):
            in_models = True
            continue

        if not in_models:
            continue

        if not stripped or stripped.startswith("#"):
            continue

        # End of models block
        if indent <= 2 and indent < 4 and ":" in stripped:
            in_models = False
            continue

        # Model key
        if indent == 4 and ":" in stripped:
            key_part = stripped.split(":")[0].strip()
            if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", key_part):
                current_key = key_part
                any_model_last_line = i
                if models.get(current_key, {}).get("model_family") == family:
                    same_family_last_line = i
                continue

        # Property of current model
        if current_key and indent >= 6:
            any_model_last_line = i
            if models.get(current_key, {}).get("model_family") == family:
                same_family_last_line = i

    # Choose insertion point
    if same_family_last_line >= 0:
        insert_after = same_family_last_line
    elif any_model_last_line >= 0:
        insert_after = any_model_last_line
    else:
        # Fallback: after the models: line
        for i, line in enumerate(lines):
            if line.lstrip().startswith("models:"):
                insert_after = i
                break

    if insert_after < 0:
        raise RuntimeError("Could not find the models: block in settings.yaml")

    # Insert with a blank line separator
    new_lines = lines[: insert_after + 1] + ["", yaml_block] + lines[insert_after + 1:]
    return "\n".join(new_lines)


def copy_prompts(source_key: str, target_key: str) -> list[str]:
    """Copy prompt files from source model dir to target model dir. Returns list of copied files."""
    src = os.path.join(PROMPTS_DIR, source_key)
    dst = os.path.join(PROMPTS_DIR, target_key)
    os.makedirs(dst, exist_ok=True)

    copied = []
    for fname in PROMPT_FILES:
        src_file = os.path.join(src, fname)
        dst_file = os.path.join(dst, fname)
        if os.path.isfile(src_file):
            shutil.copy2(src_file, dst_file)
            copied.append(fname)

    return copied


# ── Interactive prompts ──────────────────────────────────────────────────────

def ask(prompt: str, default: str = "") -> str:
    """Ask user for input with optional default."""
    suffix = f" [{C.CYAN}{default}{C.RESET}]" if default else ""
    result = input(f"  {prompt}{suffix}: ").strip()
    return result if result else default


def ask_choice(prompt: str, choices: list[str], default: str = "") -> str:
    """Ask user to pick from a list."""
    choices_str = " / ".join(
        f"{C.BOLD}{c}{C.RESET}" if c == default else c for c in choices
    )
    suffix = f" ({choices_str})"
    while True:
        result = ask(f"{prompt}{suffix}", default)
        if result in choices:
            return result
        error(f"Invalid choice. Pick one of: {', '.join(choices)}")


def ask_float(prompt: str, default: float) -> float:
    """Ask user for a float value."""
    while True:
        result = ask(prompt, str(default))
        try:
            return float(result)
        except ValueError:
            error("Please enter a valid number.")


def ask_int(prompt: str, default: int) -> int:
    """Ask user for an integer value."""
    while True:
        result = ask(prompt, str(default))
        try:
            return int(result)
        except ValueError:
            error("Please enter a valid integer.")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    """Ask user a yes/no question."""
    suffix = "[Y/n]" if default else "[y/N]"
    result = ask(f"{prompt} {suffix}", "y" if default else "n")
    return result.lower() in ("y", "yes", "s", "si", "sí")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add a new model to the evaluation framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode
  python tools/add_model.py

  # Non-interactive: standard model
  python tools/add_model.py --key gpt45 --deployment "gpt-4.5" --family gpt4

  # Non-interactive: reasoning model
  python tools/add_model.py --key o4_mini --deployment "o4-mini" --family gpt5 \\
      --reasoning-effort medium --max-tokens 16384

  # Specify prompt source explicitly
  python tools/add_model.py --key gpt45 --deployment "gpt-4.5" --family gpt4 \\
      --copy-prompts-from gpt4o

  # Skip prompt copy
  python tools/add_model.py --key gpt45 --deployment "gpt-4.5" --family gpt4 \\
      --no-prompts
        """,
    )
    parser.add_argument("--key", help="Model key (e.g. gpt45, o4_mini)")
    parser.add_argument("--deployment", help="Azure deployment name (e.g. gpt-4.5)")
    parser.add_argument("--family", choices=VALID_FAMILIES, help="Model family (gpt4 or gpt5)")
    parser.add_argument("--model-version", default="", help="Model version string")
    parser.add_argument("--max-tokens", type=int, help="Max response tokens")
    parser.add_argument("--temperature", type=float, help="Temperature (omit for reasoning models)")
    parser.add_argument("--reasoning-effort", choices=VALID_REASONING_EFFORTS,
                        help="Reasoning effort level (makes it a reasoning model)")
    parser.add_argument("--copy-prompts-from", help="Model key to copy prompts from")
    parser.add_argument("--no-prompts", action="store_true",
                        help="Skip prompt directory creation")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing model key without asking")

    args = parser.parse_args()

    # Determine if we're in interactive mode
    interactive = args.key is None

    # ── Banner ───────────────────────────────────────────────────────────
    heading("╔══════════════════════════════════════════════╗")
    print(f"{C.BOLD}{C.BLUE}║   Add Model — Evaluation Framework Setup     ║{C.RESET}")
    heading("╚══════════════════════════════════════════════╝")

    # ── Load existing config ─────────────────────────────────────────────
    if not os.path.isfile(SETTINGS_PATH):
        error(f"settings.yaml not found at: {SETTINGS_PATH}")
        sys.exit(1)

    settings_text = read_settings()
    existing_models = parse_existing_models(settings_text)

    if existing_models:
        heading("Current models:")
        for k, props in existing_models.items():
            dep = props.get("deployment_name", "?")
            fam = props.get("model_family", "?")
            reas = props.get("reasoning_effort", "")
            label = f" (reasoning: {reas})" if reas else ""
            print(f"    {C.CYAN}{k}{C.RESET}  →  {dep}  [{fam}]{label}")

    # ── Collect parameters ───────────────────────────────────────────────
    heading("Step 1 — Model identity")

    # Key
    if interactive:
        print(f"\n  {dim('The key is the internal identifier (e.g. gpt45, o4_mini, my_model).')}")
        print(f"  {dim('It becomes the API name, CLI name, and prompt directory name.')}")
        while True:
            key = ask("Model key")
            if not key:
                error("Key is required.")
                continue
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", key):
                error("Key must be alphanumeric + underscores (e.g. gpt45, o4_mini).")
                continue
            if key in existing_models and not args.force:
                error(f"Key '{key}' already exists. Use --force to overwrite.")
                continue
            break
    else:
        key = args.key
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", key):
            error(f"Invalid key: '{key}'. Must be alphanumeric + underscores.")
            sys.exit(1)
        if key in existing_models and not args.force:
            error(f"Key '{key}' already exists. Use --force to overwrite.")
            sys.exit(1)

    # Deployment name
    if interactive:
        deployment = ask("Azure deployment name (as in AI Foundry portal)")
        if not deployment:
            error("Deployment name is required.")
            sys.exit(1)
    else:
        deployment = args.deployment
        if not deployment:
            error("--deployment is required.")
            sys.exit(1)

    # Family
    if interactive:
        print(f"\n  {dim('gpt4 → uses max_tokens + system role')}")
        print(f"  {dim('gpt5 → uses max_completion_tokens + developer role')}")
        family = ask_choice("Model family", VALID_FAMILIES,
                            default="gpt5" if "5" in deployment or "o1" in deployment or "o3" in deployment or "o4" in deployment else "gpt4")
    else:
        family = args.family
        if not family:
            error("--family is required.")
            sys.exit(1)

    # ── Parameters ───────────────────────────────────────────────────────
    heading("Step 2 — Parameters")

    # Reasoning?
    if interactive:
        is_reasoning = ask_yes_no("Is this a reasoning model? (o-series, gpt-5 with reasoning)", default=False)
        if is_reasoning:
            reasoning_effort = ask_choice("Reasoning effort", VALID_REASONING_EFFORTS, default="medium")
            temperature = None
        else:
            reasoning_effort = None
            temperature = ask_float("Temperature (0.0–2.0)", default=0.1)
    else:
        reasoning_effort = args.reasoning_effort
        is_reasoning = reasoning_effort is not None
        temperature = None if is_reasoning else (args.temperature if args.temperature is not None else 0.1)

    # Max tokens
    default_max = 16384 if family == "gpt5" else 4096
    if interactive:
        max_tokens = ask_int("Max tokens", default=default_max)
    else:
        max_tokens = args.max_tokens if args.max_tokens is not None else default_max

    # Model version
    if interactive:
        model_version = ask("Model version (optional, from deployment details)")
    else:
        model_version = args.model_version or ""

    # ── Prompt copy ──────────────────────────────────────────────────────
    heading("Step 3 — Prompt templates")

    skip_prompts = args.no_prompts
    prompt_source = None

    if not skip_prompts:
        # Determine best source
        if args.copy_prompts_from:
            if args.copy_prompts_from not in existing_models:
                warn(f"Model '{args.copy_prompts_from}' not found in config. Available: {', '.join(existing_models.keys())}")
                if interactive:
                    skip_prompts = not ask_yes_no("Pick a different source?", default=True)
                else:
                    skip_prompts = True
            else:
                prompt_source = args.copy_prompts_from
        else:
            best = find_best_prompt_source(family, existing_models, is_reasoning)
            if best:
                if interactive:
                    # Show options
                    models_with_prompts = [
                        k for k in existing_models
                        if os.path.isdir(os.path.join(PROMPTS_DIR, k))
                        and any(f.endswith(".md") for f in os.listdir(os.path.join(PROMPTS_DIR, k)))
                    ]
                    print(f"\n  {dim('Available models with prompts:')}")
                    for m in models_with_prompts:
                        fam = existing_models[m].get("model_family", "?")
                        marker = f" {C.GREEN}← recommended{C.RESET}" if m == best else ""
                        print(f"    {C.CYAN}{m}{C.RESET}  [{fam}]{marker}")

                    use_best = ask_yes_no(f"Copy prompts from '{best}'?", default=True)
                    if use_best:
                        prompt_source = best
                    else:
                        alt = ask(f"Model key to copy from (or 'skip')")
                        if alt.lower() == "skip" or not alt:
                            skip_prompts = True
                        elif alt in models_with_prompts:
                            prompt_source = alt
                        else:
                            warn(f"'{alt}' not found, skipping prompt copy.")
                            skip_prompts = True
                else:
                    prompt_source = best
            else:
                warn("No existing models with prompt files found.")
                skip_prompts = True

    # Check if target dir already exists
    target_prompt_dir = os.path.join(PROMPTS_DIR, key)
    if not skip_prompts and os.path.isdir(target_prompt_dir) and os.listdir(target_prompt_dir):
        if interactive:
            warn(f"Prompt directory prompts/{key}/ already exists with files.")
            if not ask_yes_no("Overwrite existing prompts?", default=False):
                skip_prompts = True
                info("Keeping existing prompts.")
        elif not args.force:
            warn(f"Prompt directory prompts/{key}/ already exists. Use --force to overwrite.")
            skip_prompts = True

    # ── Confirmation ─────────────────────────────────────────────────────
    heading("Summary")
    print(f"    Key:              {C.BOLD}{key}{C.RESET}")
    print(f"    Deployment:       {deployment}")
    print(f"    Family:           {family}")
    if model_version:
        print(f"    Version:          {model_version}")
    print(f"    Max tokens:       {max_tokens}")
    if reasoning_effort:
        print(f"    Reasoning effort: {reasoning_effort}")
    else:
        print(f"    Temperature:      {temperature}")
    if skip_prompts:
        print(f"    Prompts:          {C.DIM}skip (fallback chain will be used){C.RESET}")
    else:
        print(f"    Prompts:          copy from {C.CYAN}{prompt_source}{C.RESET} → prompts/{key}/")

    if interactive:
        print()
        if not ask_yes_no("Proceed?", default=True):
            print("\n  Cancelled.")
            sys.exit(0)

    # ── Execute ──────────────────────────────────────────────────────────
    heading("Applying changes…")

    # 1. Build YAML block
    yaml_block = build_yaml_block(
        key=key,
        deployment_name=deployment,
        family=family,
        model_version=model_version,
        max_tokens=max_tokens,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
    )

    # 2. Insert into settings.yaml
    new_text = insert_model_in_settings(settings_text, yaml_block, family, existing_models)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        f.write(new_text)
    info(f"Added '{key}' to config/settings.yaml")

    # 3. Copy prompts
    if not skip_prompts and prompt_source:
        copied = copy_prompts(prompt_source, key)
        if copied:
            info(f"Created prompts/{key}/ with {len(copied)} files (from {prompt_source}):")
            for fname in copied:
                print(f"      {C.DIM}└─{C.RESET} {fname}")
        else:
            warn(f"No prompt files found in prompts/{prompt_source}/")
    elif skip_prompts:
        info("Prompt copy skipped — the prompt fallback chain will be used at runtime.")

    # ── Done ─────────────────────────────────────────────────────────────
    heading("✅ Done!")
    print(f"\n  Restart the server to pick up the new model:")
    print(f"    {C.CYAN}python app.py{C.RESET}")
    print(f"\n  The model '{C.BOLD}{key}{C.RESET}' will then appear in all UI dropdowns,")
    print(f"  the /api/models endpoint, and CLI commands.\n")


if __name__ == "__main__":
    main()
