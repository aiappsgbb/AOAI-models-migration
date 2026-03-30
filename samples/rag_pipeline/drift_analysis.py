"""Drift analysis across historical evaluation runs.

Reads all JSON result files from data/results/ and builds a temporal
view of quality metrics, identifying which categories and metrics
are drifting.

Usage:
    python samples/rag_pipeline/drift_analysis.py [--results-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def _parse_result_file(path: Path) -> dict | None:
    """Parse a dual_layer JSON result file, extracting model + timestamp."""
    name = path.stem  # e.g. dual_layer_gpt-4o_20260326T152109Z
    match = re.match(r"dual_layer_(.+)_(\d{8}T\d{6}Z)", name)
    if not match:
        return None
    model = match.group(1)
    ts_str = match.group(2)
    ts = datetime.strptime(ts_str, "%Y%m%dT%H%M%SZ")
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"model": model, "timestamp": ts, "data": data, "path": str(path)}


def _per_category_scores(cases: list[dict], metrics: list[str]) -> dict[str, dict[str, float]]:
    """Aggregate scores by category."""
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        by_cat[c.get("category", "unknown")].append(c)

    result = {}
    for cat, items in sorted(by_cat.items()):
        scores = {}
        for m in metrics:
            vals = [it[m] for it in items if it.get(m) is not None]
            scores[m] = sum(vals) / len(vals) if vals else 0.0
        scores["count"] = len(items)
        result[cat] = scores
    return result


def analyze_drift(results_dir: str, threshold: float = 0.3) -> None:
    """Analyze drift across all result files in directory."""
    rdir = Path(results_dir)
    if not rdir.exists():
        print(f"  ✗ Results directory not found: {rdir}")
        sys.exit(1)

    files = sorted(rdir.glob("dual_layer_*.json"))
    if not files:
        print("  ✗ No dual_layer result files found.")
        sys.exit(1)

    # Parse all results
    runs = []
    for f in files:
        parsed = _parse_result_file(f)
        if parsed:
            runs.append(parsed)

    runs.sort(key=lambda r: r["timestamp"])

    print(f"\n{'=' * 70}")
    print(f"  Drift Analysis — {len(runs)} evaluation runs")
    print(f"{'=' * 70}\n")

    # ── Timeline ────────────────────────────────────────────────────────
    print("  Timeline:")
    for r in runs:
        s = r["data"].get("summary", {})
        gnd = s.get("e2e_groundedness", 0)
        rel = s.get("e2e_relevance", 0)
        cor = s.get("e2e_correctness", 0)
        print(
            f"    {r['timestamp'].strftime('%Y-%m-%d %H:%M')}  "
            f"{r['model']:<20s}  "
            f"gnd={gnd:.2f}  rel={rel:.2f}  cor={cor:.2f}"
        )
    print()

    # ── Per-model trend ─────────────────────────────────────────────────
    by_model: dict[str, list] = defaultdict(list)
    for r in runs:
        by_model[r["model"]].append(r)

    e2e_metrics = ["groundedness", "relevance", "correctness"]
    ret_metrics = ["precision", "recall", "mrr"]

    regressions_found = []

    for model, model_runs in sorted(by_model.items()):
        if len(model_runs) < 2:
            continue

        print(f"  Model: {model} ({len(model_runs)} runs)")
        first = model_runs[0]["data"]["summary"]
        last = model_runs[-1]["data"]["summary"]

        for m in e2e_metrics:
            k = f"e2e_{m}"
            v_first = first.get(k, 0)
            v_last = last.get(k, 0)
            delta = v_last - v_first
            if abs(delta) > threshold:
                arrow = "↓ REGRESSION" if delta < 0 else "↑ IMPROVEMENT"
                regressions_found.append((model, m, v_first, v_last, delta))
                print(f"    ⚠ {m}: {v_first:.2f} → {v_last:.2f} ({delta:+.2f}) {arrow}")
            else:
                print(f"    ✓ {m}: {v_first:.2f} → {v_last:.2f} ({delta:+.2f}) stable")
        print()

    # ── Per-category cluster analysis ───────────────────────────────────
    print(f"  {'─' * 66}")
    print(f"  Per-Category Cluster Analysis")
    print(f"  {'─' * 66}\n")

    # Compare first run vs last run (regardless of model)
    if len(runs) >= 2:
        first_run = runs[0]
        last_run = runs[-1]

        first_cats = _per_category_scores(
            first_run["data"].get("end_to_end", []), e2e_metrics
        )
        last_cats = _per_category_scores(
            last_run["data"].get("end_to_end", []), e2e_metrics
        )

        print(
            f"  Comparing: {first_run['model']} ({first_run['timestamp'].strftime('%m/%d')}) "
            f"→ {last_run['model']} ({last_run['timestamp'].strftime('%m/%d')})\n"
        )

        all_cats = sorted(set(list(first_cats.keys()) + list(last_cats.keys())))
        header = f"  {'Category':<22s}"
        for m in e2e_metrics:
            header += f"  {m[:5]:>5s}→{m[:5]:<5s}"
        print(header)
        print(f"  {'─' * 58}")

        cat_regressions = []
        for cat in all_cats:
            f_scores = first_cats.get(cat, {})
            l_scores = last_cats.get(cat, {})
            row = f"  {cat:<22s}"
            cat_regressed = False
            for m in e2e_metrics:
                fv = f_scores.get(m, 0)
                lv = l_scores.get(m, 0)
                delta = lv - fv
                if delta < -threshold:
                    row += f"  {fv:.1f}→{lv:.1f} ⚠"
                    cat_regressed = True
                elif delta > threshold:
                    row += f"  {fv:.1f}→{lv:.1f} ↑"
                else:
                    row += f"  {fv:.1f}→{lv:.1f}  "
            if cat_regressed:
                cat_regressions.append(cat)
            print(row)

        print()
        if cat_regressions:
            print(f"  ⚠ Categories with regressions: {', '.join(cat_regressions)}")
            print(f"  → Run task-level evaluation on these categories to identify root cause.")
            print(f"  → See remediation guide: docs/migrating-multi-step-apps.md#remediation")
        else:
            print(f"  ✅ No per-category regressions detected (threshold: >{threshold:.1f})")
    else:
        print("  Need at least 2 runs for cluster analysis.")

    # ── Retrieval drift ─────────────────────────────────────────────────
    if len(runs) >= 2:
        print(f"\n  {'─' * 66}")
        print(f"  Retrieval Stability Over Time")
        print(f"  {'─' * 66}\n")

        for r in runs:
            ret = r["data"].get("retrieval", [])
            if ret:
                avg_prec = sum(c.get("precision", 0) or 0 for c in ret) / len(ret)
                avg_rec = sum(c.get("recall", 0) or 0 for c in ret) / len(ret)
                avg_mrr = sum(c.get("mrr", 0) or 0 for c in ret) / len(ret)
                print(
                    f"    {r['timestamp'].strftime('%Y-%m-%d')}  {r['model']:<20s}  "
                    f"prec={avg_prec:.2f}  rec={avg_rec:.2f}  mrr={avg_mrr:.2f}"
                )
        print()

    # ── Verdict ─────────────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    if regressions_found:
        print(f"  ⚠  {len(regressions_found)} metric drift(s) detected above threshold ({threshold})")
        print(f"  Recommended: investigate per-category breakdown and run task-level diagnostics")
    else:
        print(f"  ✅ No significant drift detected across {len(runs)} runs")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Drift analysis across evaluation runs")
    parser.add_argument(
        "--results-dir",
        default="samples/rag_pipeline/data/results",
        help="Directory containing dual_layer_*.json files",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.3,
        help="Score drop threshold to flag as regression (default: 0.3)",
    )
    args = parser.parse_args()
    analyze_drift(args.results_dir, args.threshold)
