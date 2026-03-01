#!/usr/bin/env python3
"""
Migrate Test Data — Old Format → Simplified CSV-Compatible Format
================================================================

Converts all existing JSON test data files from the legacy nested format
to the new flat, CSV-compatible format.

Schema changes per type:
  Classification:  remove scenario, follow_up_questions_expected; context Dict→JSON string
  Dialog:          remove scenario, category; conversation→JSON string, lists→pipe-separated
  General:         remove expected_output; conversation→JSON string
  RAG:             remove scenario, expected_behavior, complexity (keep 4 text fields)
  Tool Calling:    remove scenario, complexity; lists/dicts→JSON or pipe-separated strings

Usage:
    python tools/migrate_test_data.py              # dry-run
    python tools/migrate_test_data.py --apply      # write changes
    python tools/migrate_test_data.py --apply --no-backup   # skip backup
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "synthetic"


# ─── Conversion helpers ──────────────────────────────────────────────

def _dict_to_json_str(val) -> str:
    """Convert dict/list to JSON string; passthrough strings."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


def _list_to_pipe(val) -> str:
    """Convert list of strings to pipe-separated string."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        return " | ".join(str(v) for v in val)
    return str(val)


def _is_legacy(item: dict, task: str) -> bool:
    """Detect whether an item uses the old schema."""
    if task == "classification":
        return ("scenario" in item
                or "follow_up_questions_expected" in item
                or isinstance(item.get("context"), dict))
    elif task == "dialog":
        return ("scenario" in item
                or "category" in item
                or isinstance(item.get("conversation"), list))
    elif task == "general":
        return ("expected_output" in item
                or isinstance(item.get("conversation"), list))
    elif task == "rag":
        return ("scenario" in item
                or "expected_behavior" in item
                or "complexity" in item)
    elif task == "tool_calling":
        return ("scenario" in item
                or "complexity" in item
                or isinstance(item.get("available_tools"), list))
    return False


# ─── Per-type converters ─────────────────────────────────────────────

def migrate_classification(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "customer_input": item.get("customer_input", ""),
        "expected_category": item.get("expected_category", ""),
        "expected_subcategory": item.get("expected_subcategory", ""),
        "expected_priority": item.get("expected_priority", ""),
        "expected_sentiment": item.get("expected_sentiment", ""),
        "context": _dict_to_json_str(item.get("context", {})),
    }


def migrate_dialog(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "conversation": _dict_to_json_str(item.get("conversation", [])),
        "context_gaps": _list_to_pipe(item.get("context_gaps", [])),
        "optimal_follow_up": item.get("optimal_follow_up", ""),
        "follow_up_rules": _list_to_pipe(item.get("follow_up_rules", [])),
        "expected_resolution_turns": item.get("expected_resolution_turns", 2),
    }


def migrate_general(item: dict) -> dict:
    conv = item.get("conversation")
    return {
        "id": item.get("id", ""),
        "test_type": item.get("test_type", ""),
        "prompt": item.get("prompt", ""),
        "complexity": item.get("complexity", "medium"),
        "expected_behavior": item.get("expected_behavior", ""),
        "conversation": _dict_to_json_str(conv) if conv else "",
        "run_count": item.get("run_count", 1),
    }


def migrate_rag(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "query": item.get("query", ""),
        "context": item.get("context", ""),
        "ground_truth": item.get("ground_truth", ""),
    }


def migrate_tool_calling(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "query": item.get("query", ""),
        "available_tools": _dict_to_json_str(item.get("available_tools", [])),
        "expected_tool_calls": _list_to_pipe(item.get("expected_tool_calls", [])),
        "expected_parameters": _dict_to_json_str(item.get("expected_parameters", {})),
    }


CONVERTERS = {
    "classification": migrate_classification,
    "dialog": migrate_dialog,
    "general": migrate_general,
    "rag": migrate_rag,
    "tool_calling": migrate_tool_calling,
}

# Map task → expected filename
FILENAMES = {
    "classification": "classification_scenarios.json",
    "dialog": "follow_up_scenarios.json",
    "general": "capability_tests.json",
    "rag": "rag_scenarios.json",
    "tool_calling": "tool_calling_scenarios.json",
}


# ─── Main logic ──────────────────────────────────────────────────────

def find_all_data_files() -> list:
    """Discover all test data JSON files grouped by task type."""
    found = []
    for task, filename in FILENAMES.items():
        # Main data dir
        main_file = DATA_DIR / task / filename
        if main_file.exists():
            found.append((task, main_file))
        # Topic sub-dirs
        topics_dir = DATA_DIR / "topics"
        if topics_dir.exists():
            for topic_dir in sorted(topics_dir.iterdir()):
                if topic_dir.is_dir():
                    topic_file = topic_dir / task / filename
                    if topic_file.exists():
                        found.append((task, topic_file))
    return found


def migrate_file(task: str, file_path: Path, *, apply: bool, backup: bool) -> dict:
    """Migrate a single file. Returns stats dict."""
    stats = {"file": str(file_path.relative_to(ROOT)), "task": task,
             "total": 0, "migrated": 0, "already_new": 0, "errors": []}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        stats["errors"].append(f"Failed to read: {e}")
        return stats

    if not isinstance(data, list):
        stats["errors"].append("Top-level is not a JSON array")
        return stats

    stats["total"] = len(data)
    converter = CONVERTERS[task]
    new_data = []

    for i, item in enumerate(data):
        try:
            if _is_legacy(item, task):
                new_data.append(converter(item))
                stats["migrated"] += 1
            else:
                new_data.append(item)
                stats["already_new"] += 1
        except Exception as e:
            stats["errors"].append(f"Item {i}: {e}")
            new_data.append(item)  # keep original on error

    if apply and stats["migrated"] > 0:
        if backup:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = file_path.with_suffix(f".bak_{ts}.json")
            shutil.copy2(file_path, bak)
            stats["backup"] = str(bak.relative_to(ROOT))

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(new_data, f, indent=2, ensure_ascii=False)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Migrate test data to simplified format")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    parser.add_argument("--no-backup", action="store_true", help="Skip backup creation")
    args = parser.parse_args()

    files = find_all_data_files()
    if not files:
        print("No test data files found.")
        sys.exit(0)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n{'='*60}")
    print(f"  Test Data Migration — {mode}")
    print(f"{'='*60}\n")

    total_files = 0
    total_migrated = 0
    total_errors = 0

    for task, file_path in files:
        stats = migrate_file(task, file_path, apply=args.apply, backup=not args.no_backup)
        total_files += 1
        total_migrated += stats["migrated"]
        total_errors += len(stats["errors"])

        icon = "✅" if stats["migrated"] > 0 else "⏭️" if stats["already_new"] == stats["total"] else "⚠️"
        print(f"  {icon} {stats['file']}")
        print(f"     {stats['task']}: {stats['total']} items — "
              f"{stats['migrated']} migrated, {stats['already_new']} already new")
        if stats.get("backup"):
            print(f"     💾 Backup: {stats['backup']}")
        for err in stats["errors"]:
            print(f"     ❌ {err}")

    print(f"\n{'─'*60}")
    print(f"  Files scanned: {total_files}")
    print(f"  Items migrated: {total_migrated}")
    print(f"  Errors: {total_errors}")
    if not args.apply and total_migrated > 0:
        print(f"\n  ℹ️  Run with --apply to write changes")
    print()


if __name__ == "__main__":
    main()
