#!/usr/bin/env python3
"""update_prices.py — Fetch live Azure OpenAI token prices and update settings.yaml.

Queries the **Azure Retail Prices API** (no authentication required) for all
Azure OpenAI token meters, maps them to the model keys defined in
``config/settings.yaml → cost_rates``, and writes the updated prices back.

For models NOT available through the Azure Retail Prices API (e.g. Gemini,
Mistral), hardcoded prices with provider-sourced URLs are used instead.
These are maintained in the ``_EXTERNAL_PRICES`` dict near the top of this
file and can be updated manually when providers publish new rates.

Usage:
    # Preview changes without writing (default)
    python tools/update_prices.py

    # Apply changes to settings.yaml
    python tools/update_prices.py --apply

    # Show all fetched meters (for debugging / new-model mapping)
    python tools/update_prices.py --verbose

    # Prefer DataZone tier pricing instead of Global
    python tools/update_prices.py --tier datazone --apply

    # Use a specific currency
    python tools/update_prices.py --currency EUR

    # Skip external (non-Azure) model prices
    python tools/update_prices.py --skip-external

How it works:
    1. Fetches *all* Azure OpenAI primary-region token meters from
       ``https://prices.azure.com/api/retail/prices`` (paginated).
    2. Filters out batch, fine-tuning, provisioned, grader, and codex meters.
    3. Keeps only **Global** (default) or **DataZone** tier meters.
    4. For each meter, classifies:
       - The **model** (gpt4, gpt4o, gpt41_mini, gpt5, gpt54_mini, …)
       - The **token type** (input, output, cached_input, audio_input, …)
    5. Normalises all prices to **USD per 1K tokens**.
    6. Merges the fetched prices into the existing ``cost_rates`` section of
       ``config/settings.yaml``, preserving any keys the API doesn't cover
       (e.g. ``reasoning``).
    7. Writes the file (or prints the diff in dry-run mode).

Scheduling:
    - **Windows Task Scheduler**: ``schtasks /create /tn "UpdatePrices" /tr "python tools/update_prices.py --apply" /sc weekly /d MON /st 08:00``
    - **cron** (Linux/macOS): ``0 8 * * 1 cd /path/to/model_migration_eval && python tools/update_prices.py --apply``
    - **GitHub Actions**: see the example workflow at the bottom of this file.

Requirements:
    - ``requests`` (already in requirements.txt)
    - ``pyyaml``   (already in requirements.txt)
"""

from __future__ import annotations

import argparse
import copy
import io
import logging
import re
import sys
import textwrap
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

# ── Constants ────────────────────────────────────────────────────────────────

RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"

ODATA_FILTER = (
    "contains(productName, 'OpenAI')"
    " and isPrimaryMeterRegion eq true"
    " and (unitOfMeasure eq '1K' or unitOfMeasure eq '1M')"
)

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"

LOG = logging.getLogger("update_prices")

# ── Aliases ──────────────────────────────────────────────────────────────────
# Models that share the same pricing.  After building the price table from the
# API, any alias that doesn't have its own prices inherits from its source.
_ALIASES: Dict[str, str] = {
    "gpt5_reasoning": "gpt51",   # same deployment (gpt-5.1), same rates
}

# ── External model prices ───────────────────────────────────────────────────
# Models deployed through Azure Marketplace / Azure AI Foundry that are NOT
# available in the Azure Retail Prices API.  Prices are per 1 K tokens (USD).
#
# Each entry carries a ``source`` URL and an optional ``note`` for auditability.
# Update these values when the provider publishes new pricing.
#
# To add a new external model:
#   1. Add an entry below with "rates", "source", and (optionally) "note".
#   2. Make sure the key matches a model in settings.yaml → cost_rates.
#   3. Run ``python tools/update_prices.py`` to preview the merge.

_EXTERNAL_PRICES: Dict[str, Dict[str, Any]] = {
    "gemini3_flash": {
        "source": "https://ai.google.dev/pricing",
        "note": "Gemini 3 Flash Preview — Azure AI Foundry deployment rates",
        "rates": {
            "input": 0.00015,           # $0.15 / 1M tokens
            "output": 0.0006,           # $0.60 / 1M tokens
            "cached_input": 0.0000375,  # $0.0375 / 1M tokens
        },
    },
    "mistral_large_3": {
        "source": "https://docs.mistral.ai/models/mistral-large-3-25-12",
        "note": "Mistral Large 3 — Azure Marketplace pay-as-you-go",
        "rates": {
            "input": 0.002,             # $2.0 / 1M tokens
            "output": 0.006,            # $6.0 / 1M tokens
            "cached_input": 0.001,      # $1.0 / 1M tokens
        },
    },
}

# ── Meter‑name patterns ─────────────────────────────────────────────────────
# Each rule is tried **in order** — first match wins.  The patterns operate on
# the lower-cased ``meterName`` from the API response.

# fmt: off
_SKIP_PATTERNS = re.compile(
    r"batch|"
    r"\bft\b|finetuned|fine-tune|"
    r"\brft\b|grader|training|"
    r"codex|deep research|"
    r"transcri|trscb|"
    r"\bnano\b|"
    r"\bpp\b|"
    r"\bimg\b|sora|embed|"
    r"\bdev\b"
)

# (regex, settings_key)  — order matters (most specific first)
_MODEL_RULES: List[Tuple[re.Pattern, str]] = [
    # Realtime 1.5 (must come before generic realtime)
    (re.compile(r"rt\s*1\.5|aud\s*1\.5"), "gpt_realtime_15"),
    # Realtime v1 (gpt-4o based, 0828)
    (re.compile(r"rt\s*txt|rt\s*aud|rt-aud|rt-txt|aud\s*0828|realtime"), "gpt_realtime_1"),
    # TTS (gpt-4o-mini-tts)
    (re.compile(r"mn\s*tts|mini.tts"), "tts"),
    # GPT-4.1-mini  (before 4.1)
    (re.compile(r"4\.1[\s-]*mini|4\.1[\s-]*mn|41[\s-]*mn"), "gpt41_mini"),
    # GPT-4.1
    (re.compile(r"4\.1\b"), "gpt4"),
    # GPT-4o-mini  (before 4o)
    (re.compile(r"4o[\s-]*mini|4o[\s-]*mn"), "gpt4o_mini"),
    # GPT-4o
    (re.compile(r"4o\b"), "gpt4o"),
    # GPT-5.4-mini  (before 5.4)
    (re.compile(r"5\.4[\s-]*mini|5\.4[\s-]*mn"), "gpt54_mini"),
    # GPT-5.4
    (re.compile(r"5\.4\b"), "gpt5"),
    # GPT-5.1
    (re.compile(r"5\.1\b"), "gpt51"),
    # GPT-5.2
    (re.compile(r"5\.2\b"), "gpt52"),
    # o4-mini
    (re.compile(r"o4[\s-]*mini"), "o4_mini"),
    # o3-mini
    (re.compile(r"o3[\s-]*mini"), "o3_mini"),
    # Phi-4  (OSS 120B in the API)
    (re.compile(r"oss.120b"), "phi4"),
]
# fmt: on


# ── Token‑type classification ───────────────────────────────────────────────

def _classify_token_type(meter: str) -> str:
    """Return one of: input, output, cached_input, audio_input, audio_output, audio_cached, unknown."""
    m = meter.lower()
    is_audio = "aud" in m
    is_cached = any(x in m for x in ("cchd", " cd ", "cached"))
    is_input = any(x in m for x in ("inp", " in "))
    is_output = any(x in m for x in ("out", "opt"))

    if is_audio:
        if is_cached:
            return "audio_cached"
        if is_input:
            return "audio_input"
        if is_output:
            return "audio_output"
        return "unknown"
    if is_cached:
        return "cached_input"
    if is_input:
        return "input"
    if is_output:
        return "output"
    return "unknown"


# ── Tier (Global vs DataZone) ───────────────────────────────────────────────

_RE_GLOBAL = re.compile(r"\b(Gl|glbl|global)\b", re.IGNORECASE)
_RE_DZONE  = re.compile(r"\b(Dz|DZone|dzone|DZ|dz|datazone)\b", re.IGNORECASE)


def _meter_tier(meter: str) -> Optional[str]:
    """Return 'global', 'datazone', or None (regional / unrecognised)."""
    if _RE_GLOBAL.search(meter):
        return "global"
    if _RE_DZONE.search(meter):
        return "datazone"
    return None


# ── API fetcher ──────────────────────────────────────────────────────────────

def fetch_all_meters(currency: str = "USD") -> List[Dict[str, Any]]:
    """Page through the Azure Retail Prices API and return all matching items."""
    all_items: List[Dict[str, Any]] = []
    next_url: Optional[str] = RETAIL_PRICES_URL
    params: Dict[str, str] = {"$filter": ODATA_FILTER}
    if currency != "USD":
        params["currencyCode"] = f"'{currency}'"

    while next_url:
        resp = requests.get(
            next_url,
            params=params if next_url == RETAIL_PRICES_URL else None,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        all_items.extend(data.get("Items", []))
        next_url = data.get("NextPageLink")
        LOG.debug("  fetched %d items so far …", len(all_items))

    LOG.info("Fetched %d primary token meters from Azure Retail Prices API", len(all_items))
    return all_items


# ── Build pricing table ─────────────────────────────────────────────────────

def build_price_table(
    items: List[Dict[str, Any]],
    preferred_tier: str = "global",
) -> Dict[str, Dict[str, float]]:
    """Classify meters and build ``{model_key: {token_type: price_per_1k}}``."""

    # Bucket: model → token_type → tier → [prices]
    raw: Dict[str, Dict[str, Dict[str, List[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for item in items:
        meter = item["meterName"]
        ml = meter.lower()

        # Skip non-standard meters
        if _SKIP_PATTERNS.search(ml):
            continue

        # Determine tier
        tier = _meter_tier(meter)
        if tier is None:
            continue

        # Classify model
        model_key: Optional[str] = None
        for pat, key in _MODEL_RULES:
            if pat.search(ml):
                model_key = key
                break
        if model_key is None:
            LOG.debug("Unclassified meter: %s", meter)
            continue

        # Classify token type
        tt = _classify_token_type(meter)
        if tt == "unknown":
            LOG.debug("Unknown token type: %s → model=%s", meter, model_key)
            continue

        # Normalise to per-1K
        price = item["retailPrice"]
        if item["unitOfMeasure"] == "1M":
            price = price / 1000.0

        raw[model_key][tt][tier].append(price)

    # Collapse: pick preferred_tier, fallback to other, take min price
    alt_tier = "datazone" if preferred_tier == "global" else "global"
    result: Dict[str, Dict[str, float]] = {}
    for model, tt_dict in raw.items():
        result[model] = {}
        for tt, tier_dict in tt_dict.items():
            candidates = tier_dict.get(preferred_tier) or tier_dict.get(alt_tier, [])
            if candidates:
                result[model][tt] = min(candidates)

    # Propagate aliases
    for alias, source in _ALIASES.items():
        if alias not in result and source in result:
            result[alias] = dict(result[source])
            LOG.debug("Alias: %s ← %s", alias, source)

    return result


# ── YAML updater (preserves comments) ───────────────────────────────────────

def _format_rate(value: float) -> str:
    """Format a rate removing unnecessary trailing zeros but keeping precision."""
    # Use enough decimals to distinguish e.g. 0.0000375 from 0.00004
    s = f"{value:.8f}".rstrip("0")
    if s.endswith("."):
        s += "0"
    return s


def _update_cost_rates_in_yaml(
    yaml_text: str,
    new_prices: Dict[str, Dict[str, float]],
) -> Tuple[str, List[str]]:
    """Update the ``cost_rates:`` block in *yaml_text* with *new_prices*.

    Returns ``(updated_text, list_of_change_descriptions)``.
    Preserves YAML comments and structure outside the cost_rates block.
    """

    # Parse the YAML to get existing cost_rates
    parsed = yaml.safe_load(yaml_text)
    old_rates: Dict[str, Dict[str, Any]] = parsed.get("cost_rates", {})

    changes: List[str] = []

    # Merge new prices into old rates
    merged = copy.deepcopy(old_rates)
    for model, rates in new_prices.items():
        if model not in merged:
            LOG.debug("Model %r found in API but not in cost_rates — skipped", model)
            continue
        for token_type, price in rates.items():
            old_val = merged[model].get(token_type)
            if old_val is not None and abs(old_val - price) < 1e-10:
                continue  # no change
            if old_val is None:
                # New token type — add it
                merged[model][token_type] = price
                changes.append(f"  {model}.{token_type}: NEW → {_format_rate(price)}")
            else:
                merged[model][token_type] = price
                changes.append(
                    f"  {model}.{token_type}: {_format_rate(old_val)} → {_format_rate(price)}"
                )

    if not changes:
        return yaml_text, changes

    # Rebuild the cost_rates block as text (to preserve file structure)
    lines = yaml_text.splitlines(keepends=True)
    # Find the cost_rates: line
    cr_start = None
    cr_end = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "cost_rates:" or stripped.startswith("cost_rates:"):
            if not stripped.startswith("#"):
                cr_start = i
                continue
        if cr_start is not None and cr_end is None:
            # The block ends at the next top-level key (not indented, not blank, not comment)
            if stripped and not stripped.startswith("#") and not line[0].isspace():
                cr_end = i
                break
    if cr_start is None:
        LOG.warning("Could not find 'cost_rates:' in settings.yaml — cannot update")
        return yaml_text, []
    if cr_end is None:
        cr_end = len(lines)

    # Build new cost_rates block
    new_block = "cost_rates:\n"
    # Determine key order: keep existing order, add new keys at end
    ordered_keys = list(old_rates.keys())
    for k in merged:
        if k not in ordered_keys:
            ordered_keys.append(k)

    for model_key in ordered_keys:
        rates = merged.get(model_key, {})
        new_block += f"  {model_key}:\n"
        # Deterministic order for token types
        type_order = ["input", "output", "cached_input", "reasoning", "audio_input", "audio_output", "audio_cached"]
        written = set()
        for tt in type_order:
            if tt in rates:
                new_block += f"    {tt}: {_format_rate(rates[tt])}\n"
                written.add(tt)
        # Any remaining keys not in type_order
        for tt in sorted(rates):
            if tt not in written:
                new_block += f"    {tt}: {_format_rate(rates[tt])}\n"

    # Replace the old block
    updated = "".join(lines[:cr_start]) + new_block + "".join(lines[cr_end:])
    return updated, changes


# ── Main ─────────────────────────────────────────────────────────────────────

def get_external_prices() -> Dict[str, Dict[str, float]]:
    """Return hardcoded prices for models not in the Azure Retail Prices API.

    Returns the same shape as :func:`build_price_table`:
    ``{model_key: {token_type: price_per_1k}}``.
    """
    return {key: dict(entry["rates"]) for key, entry in _EXTERNAL_PRICES.items()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Azure OpenAI token prices and update settings.yaml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python tools/update_prices.py              # dry-run (preview only)
              python tools/update_prices.py --apply      # write to settings.yaml
              python tools/update_prices.py --verbose    # show all classified meters
              python tools/update_prices.py --tier datazone --apply
              python tools/update_prices.py --skip-external   # Azure API only
        """),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the updated prices to settings.yaml (default: dry-run)",
    )
    parser.add_argument(
        "--tier",
        choices=["global", "datazone"],
        default="global",
        help="Preferred pricing tier (default: global)",
    )
    parser.add_argument(
        "--currency",
        default="USD",
        help="Currency code (default: USD)",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=SETTINGS_PATH,
        help=f"Path to settings.yaml (default: {SETTINGS_PATH})",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show all classified meters",
    )
    parser.add_argument(
        "--skip-external",
        action="store_true",
        help="Skip hardcoded external model prices (Gemini, Mistral, …)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    # 1. Fetch meters
    LOG.info("⏳ Fetching Azure OpenAI token prices from Retail Prices API …")
    items = fetch_all_meters(currency=args.currency)

    # 2. Build price table
    prices = build_price_table(items, preferred_tier=args.tier)

    # 3. Merge external model prices (Gemini, Mistral, …)
    if not args.skip_external:
        ext = get_external_prices()
        for model, rates in ext.items():
            if model not in prices:
                prices[model] = rates
                LOG.debug("External: added %s", model)
            else:
                LOG.debug("External: %s already has API prices — skipping", model)
        LOG.info("📦 Added %d external model price(s) (Gemini, Mistral, …)",
                 sum(1 for m in ext if m in prices or m in ext))

    LOG.info("📊 Classified prices for %d models:", len(prices))
    for model in sorted(prices):
        rates_str = ", ".join(
            f"{k}={_format_rate(v)}" for k, v in sorted(prices[model].items())
        )
        LOG.info("   %s: %s", model, rates_str)

    # 4. Read existing settings.yaml
    settings_file = args.settings
    if not settings_file.exists():
        LOG.error("❌ Settings file not found: %s", settings_file)
        return 1

    yaml_text = settings_file.read_text(encoding="utf-8")

    # 5. Merge
    updated_text, changes = _update_cost_rates_in_yaml(yaml_text, prices)

    if not changes:
        LOG.info("\n✅ All prices are already up to date — no changes needed.")
        return 0

    LOG.info("\n📝 Price changes detected (%d):", len(changes))
    for c in changes:
        LOG.info(c)

    # 6. Write or preview
    if args.apply:
        settings_file.write_text(updated_text, encoding="utf-8")
        LOG.info("\n✅ Updated %s", settings_file)
    else:
        LOG.info("\n🔍 Dry-run mode — no changes written. Use --apply to update.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
