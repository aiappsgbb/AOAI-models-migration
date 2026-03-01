#!/usr/bin/env python
"""
Generate CSV sample files from the existing JSON test data.

For every JSON data file found under ``data/synthetic/`` (including
topic-specific subdirectories), this script:

1. Reads the JSON file.
2. Normalises each item to the **flat** schema defined in
   ``src.utils.data_loader`` (the same normalisation used at runtime).
3. Writes a corresponding CSV file into
   ``data/synthetic_samples_csv/`` preserving the same folder hierarchy.

The JSON originals are **not** modified.

Usage:
    python tools/generate_csv_samples.py          # from project root
"""

import csv
import json
import sys
from pathlib import Path

# ── Make sure project root is on sys.path ────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.utils.data_loader import (          # noqa: E402
    _normalise_classification,
    _normalise_dialog,
    _normalise_general,
    _normalise_rag,
    _normalise_tool_calling,
    _needs_normalisation,
)

# ═══════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════

SRC_DIR = ROOT / "data" / "synthetic"
DST_DIR = ROOT / "data" / "synthetic_samples_csv"

# Mapping: eval type → (JSON filename, normaliser, column order)
EVAL_TYPES = {
    "classification": {
        "filename": "classification_scenarios.json",
        "normaliser": _normalise_classification,
        "columns": [
            "id", "customer_input", "expected_category",
            "expected_subcategory", "expected_priority",
            "expected_sentiment", "context",
        ],
    },
    "dialog": {
        "filename": "follow_up_scenarios.json",
        "normaliser": _normalise_dialog,
        "columns": [
            "id", "conversation", "context_gaps",
            "optimal_follow_up", "follow_up_rules",
            "expected_resolution_turns",
        ],
    },
    "general": {
        "filename": "capability_tests.json",
        "normaliser": _normalise_general,
        "columns": [
            "id", "test_type", "prompt", "complexity",
            "expected_behavior", "conversation", "run_count",
        ],
    },
    "rag": {
        "filename": "rag_scenarios.json",
        "normaliser": _normalise_rag,
        "columns": [
            "id", "query", "context", "ground_truth",
        ],
    },
    "tool_calling": {
        "filename": "tool_calling_scenarios.json",
        "normaliser": _normalise_tool_calling,
        "columns": [
            "id", "query", "available_tools",
            "expected_tool_calls", "expected_parameters",
        ],
    },
}


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _load_json(path: Path) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_csv(items: list, columns: list, out_path: Path) -> int:
    """Write *items* (list of dicts) as a CSV file.  Returns row count."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            writer.writerow(item)
    return len(items)


def _normalise_all(items: list, eval_type: str, normaliser) -> list:
    """Always apply the normaliser so every row has all columns with defaults."""
    return [normaliser(item) for item in items]


# ═══════════════════════════════════════════════════════════════════════
# Discovery: find all JSON data files
# ═══════════════════════════════════════════════════════════════════════

def _discover_json_files():
    """
    Yield tuples ``(eval_type, json_path, relative_csv_path)`` for every
    JSON data file under ``SRC_DIR``.

    Handles both:
    - Root-level:   data/synthetic/<type>/<filename>.json
    - Topic-level:  data/synthetic/topics/<topic>/<type>/<filename>.json
    """
    for eval_type, cfg in EVAL_TYPES.items():
        fname = cfg["filename"]

        # Root-level
        root_json = SRC_DIR / eval_type / fname
        if root_json.exists():
            csv_name = Path(fname).with_suffix(".csv")
            yield eval_type, root_json, Path(eval_type) / csv_name

        # Topic-level
        topics_dir = SRC_DIR / "topics"
        if topics_dir.is_dir():
            for topic_dir in sorted(topics_dir.iterdir()):
                if not topic_dir.is_dir():
                    continue
                topic_json = topic_dir / eval_type / fname
                if topic_json.exists():
                    csv_name = Path(fname).with_suffix(".csv")
                    yield (
                        eval_type,
                        topic_json,
                        Path("topics") / topic_dir.name / eval_type / csv_name,
                    )


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    total_files = 0
    total_rows = 0

    print("=" * 65)
    print("  CSV Sample Generator — flat schema")
    print(f"  Source : {SRC_DIR}")
    print(f"  Output : {DST_DIR}")
    print("=" * 65)

    for eval_type, json_path, rel_csv in _discover_json_files():
        cfg = EVAL_TYPES[eval_type]
        items = _load_json(json_path)
        normalised = _normalise_all(items, eval_type, cfg["normaliser"])
        out_path = DST_DIR / rel_csv
        n = _write_csv(normalised, cfg["columns"], out_path)
        total_files += 1
        total_rows += n
        print(f"  ✓ {rel_csv}  ({n} rows)")

    print("-" * 65)
    print(f"  Done: {total_files} CSV files, {total_rows} total rows")
    print("=" * 65)


if __name__ == "__main__":
    main()
