"""
Regenerate ALL prompts + test data for the 3 archived topics.
Uses the most capable configured model as the generator so it is at least as
capable as the most advanced target model.

This script:
  1. For each topic slug -> activate the topic, then call generate_prompts()
     which overwrites active prompts + data AND re-archives the result.
  2. The last topic processed becomes the active one.

NOTE: Topics are processed sequentially because they share the same
active file space (prompts/<model>/, data/synthetic/*).  However,
each topic now benefits from **internal parallelism** in
generate_prompts() — LLM calls for prompts and data are batched
concurrently, giving ~3-4x speedup per topic.

Usage:
    python tools/regenerate_all_topics.py
"""

import sys, os, time, json, logging

# Force unbuffered output for real-time logging
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
os.environ["PYTHONUNBUFFERED"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.clients.azure_openai import create_client_from_config
from src.utils.prompt_manager import PromptManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
)
log = logging.getLogger(__name__)

GENERATOR_MODEL = "gpt5"            # must be >= target models in capability
CONFIG_PATH     = "config/settings.yaml"
PROMPTS_DIR     = "prompts"
DATA_DIR        = "data/synthetic"

TOPICS = [
    # (slug, human-readable topic string for generation)
    ("red_sea_diving_travel",
     "Red Sea Diving Travel"),

    ("specialized_agent_that_answers_questions_about_aeronautics_and_applied_aerodynamics",
     "Specialized agent that answers questions about aeronautics and applied aerodynamics"),

    ("telco_customer_service",
     "TELCO Customer Service"),
]


def main():
    log.info("Creating Azure OpenAI client...")
    client = create_client_from_config(CONFIG_PATH)
    log.info(f"Generator model: {GENERATOR_MODEL}")

    manager = PromptManager(prompts_dir=PROMPTS_DIR, data_dir=DATA_DIR)

    overall_ok = True

    for i, (slug, topic_name) in enumerate(TOPICS, 1):
        separator = "=" * 60
        log.info(separator)
        log.info(f"[{i}/{len(TOPICS)}]  Topic: {topic_name}")
        log.info(f"         Slug:  {slug}")
        log.info(separator)

        # Activate the archived topic so its prompts/data become active
        try:
            manager.activate_topic(slug)
            log.info(f"Activated topic '{slug}'")
        except FileNotFoundError:
            log.warning(f"Topic archive '{slug}' not found — will create fresh")

        # Generate prompts + data (this also archives the result)
        t0 = time.time()
        try:
            result = manager.generate_prompts(
                topic=topic_name,
                client=client,
                generator_model=GENERATOR_MODEL,
                data_dir=DATA_DIR,
            )
        except Exception as exc:
            log.error(f"FATAL error generating topic '{topic_name}': {exc}")
            overall_ok = False
            continue

        elapsed = time.time() - t0

        # Report
        log.info(f"Completed in {elapsed:.1f}s")

        for model, prompts in result.get("prompts", {}).items():
            for ptype, content in prompts.items():
                status = "OK" if not content.startswith("[Error") else "FAILED"
                if status == "FAILED":
                    overall_ok = False
                log.info(f"  Prompt {model}/{ptype}: {status} ({len(content)} chars)")

        for dtype, info in result.get("data", {}).items():
            if info.get("error"):
                log.error(f"  Data {dtype}: FAILED — {info['error']}")
                overall_ok = False
            else:
                log.info(f"  Data {dtype}: {info['count']} scenarios -> {info['file']}")

        log.info("")

    # ── Summary ──
    log.info("=" * 60)
    if overall_ok:
        log.info("ALL TOPICS REGENERATED SUCCESSFULLY")
    else:
        log.warning("REGENERATION COMPLETED WITH ERRORS — see above")
    log.info("=" * 60)

    # Show final state
    for slug, topic_name in TOPICS:
        topic_meta_path = os.path.join(PROMPTS_DIR, "topics", slug, "topic.json")
        if os.path.exists(topic_meta_path):
            with open(topic_meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            log.info(f"  {slug}:")
            log.info(f"    prompts_updated_at: {meta.get('prompts_updated_at', '?')}")
            log.info(f"    data_generated_at:  {meta.get('data_generated_at', '?')}")

        # Check classification categories
        cls_file = os.path.join(DATA_DIR, "topics", slug,
                                "classification", "classification_scenarios.json")
        if os.path.exists(cls_file):
            with open(cls_file, encoding="utf-8-sig") as f:
                data = json.load(f)
            cats = sorted(set(d.get("expected_category", "") for d in data))
            log.info(f"    categories: {cats}")

    active_meta = manager.get_topic_metadata()
    log.info(f"\nActive topic: {active_meta.get('topic', '(none)')}")


if __name__ == "__main__":
    main()
