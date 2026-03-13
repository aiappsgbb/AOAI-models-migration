"""
Regenerate a topic's prompts + data using the corrected pipeline.

Usage:
    python tools/regenerate_topic.py "Red Sea Diving Travel" [--scope all|prompts_only|data_only] [--generator gpt5]

Requires AZURE_OPENAI_ENDPOINT (and az login or AZURE_OPENAI_API_KEY).
"""
import sys
import os
import argparse
import logging

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.clients.azure_openai import AzureOpenAIClient
from src.utils.prompt_manager import PromptManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("regenerate_topic")


def main():
    parser = argparse.ArgumentParser(description="Regenerate a topic with the corrected pipeline")
    parser.add_argument("topic", help="Topic name (e.g. 'Red Sea Diving Travel')")
    parser.add_argument("--scope", default="all", choices=["all", "prompts_only", "data_only"],
                        help="What to regenerate (default: all)")
    parser.add_argument("--generator", default="gpt5",
                        help="Model to use as generator (default: gpt5)")
    parser.add_argument("--config", default="config/settings.yaml",
                        help="Path to settings.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without calling the API")
    args = parser.parse_args()

    logger.info(f"Topic: {args.topic}")
    logger.info(f"Scope: {args.scope}")
    logger.info(f"Generator model: {args.generator}")

    # Load config
    import yaml
    with open(args.config, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # Initialize client
    if args.dry_run:
        logger.info("[DRY RUN] Would initialize client and call generate_prompts()")
        logger.info(f"  topic={args.topic!r}, scope={args.scope!r}, generator_model={args.generator!r}")
        return

    logger.info("Initializing Azure OpenAI client...")
    client = AzureOpenAIClient(config_path=args.config)
    client.register_models_from_config(args.config)
    logger.info(f"Client ready (auth: {client._auth_method}, endpoint: {client.endpoint[:50]}...)")

    # Initialize PromptManager with the full config dict
    pm = PromptManager(config=config)
    logger.info(f"PromptManager ready (prompts_dir: {pm.prompts_dir})")

    # Show current state
    current_meta = pm.get_topic_metadata()
    logger.info(f"Current active topic: {current_meta.get('topic', '(none)')}")

    models = pm._get_model_dirs()
    logger.info(f"Model directories: {models}")

    # Run generation
    logger.info("=" * 60)
    logger.info(f"Starting generation: topic={args.topic!r}, scope={args.scope}")
    logger.info("=" * 60)

    results = pm.generate_prompts(
        topic=args.topic,
        client=client,
        generator_model=args.generator,
        scope=args.scope,
    )

    # Report results
    logger.info("=" * 60)
    logger.info("GENERATION COMPLETE")
    logger.info("=" * 60)

    if "prompts" in results:
        for model, prompts in results["prompts"].items():
            for ptype, content in prompts.items():
                status = "OK" if not str(content).startswith("[Error") else "FAILED"
                length = len(str(content))
                logger.info(f"  Prompt: {model}/{ptype} [{status}, {length} chars]")

    if "data" in results:
        for dtype, info in results["data"].items():
            if isinstance(info, dict):
                count = info.get("count", "?")
                error = info.get("error")
                if error:
                    logger.info(f"  Data: {dtype} [FAILED: {error}]")
                else:
                    logger.info(f"  Data: {dtype} [{count} scenarios]")

    logger.info("Done!")


if __name__ == "__main__":
    main()
