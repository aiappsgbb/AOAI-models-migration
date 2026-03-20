#!/usr/bin/env python3
"""
generate_phi4_prompts.py — Generate Phi-4 prompts for all topics
================================================================

Uses generate_target_prompt() from the import_topic module to generate
Phi-4 system prompts from GPT-4 source prompts for all 4 tasks, across
the active prompts and all archived topics.

Usage:
    python tools/generate_phi4_prompts.py
"""

from __future__ import annotations

import json
import os
import sys
import time

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
sys.path.insert(0, PROJECT_ROOT)

from src.clients.azure_openai import AzureOpenAIClient  # noqa: F401 — needed for module init
from src.clients import create_client_from_config
from tools.import_topic import generate_target_prompt

PROMPTS_DIR = os.path.join(PROJECT_ROOT, "prompts")
SOURCE_MODEL = "gpt4"
TARGET_MODEL = "phi4"
TARGET_FAMILY = "phi"
DEPLOYMENT_NAME = "Phi-4"
GENERATOR_MODEL = "gpt5"

TASKS = [
    "classification_agent_system",
    "dialog_agent_system",
    "rag_agent_system",
    "tool_calling_agent_system",
]

# All archived topic directories
TOPIC_DIRS = [
    os.path.join(PROMPTS_DIR, "topics", d)
    for d in os.listdir(os.path.join(PROMPTS_DIR, "topics"))
    if os.path.isdir(os.path.join(PROMPTS_DIR, "topics", d))
]


class C:
    _ok = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    GREEN = "\033[92m" if _ok else ""
    YELLOW = "\033[93m" if _ok else ""
    CYAN = "\033[96m" if _ok else ""
    BOLD = "\033[1m" if _ok else ""
    R = "\033[0m" if _ok else ""


def generate_for_location(client: AzureOpenAIClient, base_dir: str, topic_name: str) -> int:
    """Generate Phi-4 prompts for a single location (base or topic dir)."""
    source_dir = os.path.join(base_dir, SOURCE_MODEL)
    target_dir = os.path.join(base_dir, TARGET_MODEL)

    if not os.path.isdir(source_dir):
        print(f"  {C.YELLOW}⚠ Source dir not found: {source_dir} — skipping{C.R}")
        return 0

    os.makedirs(target_dir, exist_ok=True)
    count = 0

    for task_file in TASKS:
        src_path = os.path.join(source_dir, f"{task_file}.md")
        dst_path = os.path.join(target_dir, f"{task_file}.md")

        if not os.path.isfile(src_path):
            print(f"  {C.YELLOW}⚠ Missing: {src_path}{C.R}")
            continue

        if os.path.isfile(dst_path):
            print(f"  {C.CYAN}⏭ Already exists: {dst_path}{C.R}")
            continue

        task = task_file.replace("_agent_system", "")
        print(f"  Generating {task_file}...", end=" ", flush=True)

        with open(src_path, "r", encoding="utf-8") as f:
            source_prompt = f.read()

        # Retry up to 3 times on network errors
        max_retries = 3
        generated = None
        for attempt in range(1, max_retries + 1):
            try:
                t0 = time.time()
                generated = generate_target_prompt(
                    client=client,
                    topic=topic_name,
                    task=task,
                    source_prompt=source_prompt,
                    generator_model=GENERATOR_MODEL,
                    target_model=TARGET_MODEL,
                    model_family=TARGET_FAMILY,
                    deployment_name=DEPLOYMENT_NAME,
                )
                break  # success
            except (KeyboardInterrupt, Exception) as exc:
                elapsed = time.time() - t0
                if attempt < max_retries:
                    wait = 10 * attempt
                    print(f"\n    {C.YELLOW}⚠ Attempt {attempt} failed after {elapsed:.0f}s: {type(exc).__name__}. Retrying in {wait}s...{C.R}", flush=True)
                    time.sleep(wait)
                else:
                    print(f"\n    {C.YELLOW}✗ All {max_retries} attempts failed for {task_file}. Skipping.{C.R}", flush=True)
                    break

        if generated is None:
            continue

        with open(dst_path, "w", encoding="utf-8") as f:
            f.write(generated)

        elapsed = time.time() - t0
        print(f"{C.GREEN}✓{C.R} ({len(generated)} chars, {elapsed:.1f}s)")
        count += 1

    return count


def main():
    print(f"\n{C.BOLD}=== Generate Phi-4 Prompts ==={C.R}\n")
    client = create_client_from_config()
    total = 0

    # 1. Base (active) prompts
    print(f"{C.BOLD}📂 Active prompts (prompts/){C.R}")
    total += generate_for_location(client, PROMPTS_DIR, "telco_customer_service")

    # 2. Archived topics
    for topic_dir in sorted(TOPIC_DIRS):
        topic_name = os.path.basename(topic_dir)
        # Try to read topic from topic.json
        topic_json = os.path.join(topic_dir, "topic.json")
        if os.path.isfile(topic_json):
            with open(topic_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            display_topic = data.get("topic", topic_name)
        else:
            display_topic = topic_name.replace("_", " ")

        print(f"\n{C.BOLD}📂 Topic: {display_topic}{C.R}")
        total += generate_for_location(client, topic_dir, display_topic)

    print(f"\n{C.BOLD}=== Done: {total} prompts generated ==={C.R}\n")


if __name__ == "__main__":
    main()
