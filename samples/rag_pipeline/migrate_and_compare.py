"""A/B migration comparison for RAG pipelines.

Compares two model configurations side-by-side:
- Per-step analysis: which step regressed?
- End-to-end comparison: did overall quality change?
- Timing comparison: latency impact

Usage:
    python -m samples.rag_pipeline.migrate_and_compare
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.clients import create_client, call_model
from samples.rag_pipeline.pipeline import RAGPipeline, PipelineConfig, PipelineResult
from samples.rag_pipeline.knowledge_base import KnowledgeBase

DATA_DIR = Path(__file__).resolve().parent / "data"


# ---------------------------------------------------------------------------
# Comparison data structures
# ---------------------------------------------------------------------------

@dataclass
class StepComparison:
    """Comparison of a single step across two configs."""

    step: str
    metric: str
    value_a: float
    value_b: float
    delta: float
    delta_pct: float
    regression: bool  # True if B is worse than A


@dataclass
class CaseComparison:
    """Side-by-side results for a single test case."""

    query: str
    result_a: PipelineResult
    result_b: PipelineResult
    retrieval_overlap: float  # Jaccard similarity of retrieved doc IDs
    answer_changed: bool


@dataclass
class MigrationReport:
    """Full A/B comparison report."""

    config_a: PipelineConfig
    config_b: PipelineConfig
    cases: list[CaseComparison] = field(default_factory=list)
    retrieval_metrics: list[StepComparison] = field(default_factory=list)
    timing_metrics: list[StepComparison] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert report to a serializable dictionary."""
        return {
            "config_a": self.config_a.label,
            "config_b": self.config_b.label,
            "test_cases": len(self.cases),
            "cases": [
                {
                    "query": c.query,
                    "retrieval_overlap": c.retrieval_overlap,
                    "answer_changed": c.answer_changed,
                    "answer_a": c.result_a.answer[:200],
                    "answer_b": c.result_b.answer[:200],
                    "retrieved_ids_a": c.result_a.retrieved_ids,
                    "retrieved_ids_b": c.result_b.retrieved_ids,
                }
                for c in self.cases
            ],
            "timing": {
                step: {
                    "config_a_ms": _avg_timing(
                        [c.result_a for c in self.cases], step
                    ),
                    "config_b_ms": _avg_timing(
                        [c.result_b for c in self.cases], step
                    ),
                }
                for step in ["rephrase", "embed", "retrieve", "generate"]
            },
            "summary": {
                "retrieval_overlap_avg": sum(c.retrieval_overlap for c in self.cases) / len(self.cases) if self.cases else 0,
                "answers_changed": sum(1 for c in self.cases if c.answer_changed),
                "answers_total": len(self.cases),
            },
        }

    def save_json(self, path: str) -> None:
        """Save report to a JSON file for audit trail."""
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"  Report saved to {path}")

    def print_report(self) -> None:
        """Print a formatted comparison report to stdout."""
        n = len(self.cases)
        w = 55  # report width

        diff_docs = sum(1 for c in self.cases if c.retrieval_overlap < 1.0)
        avg_overlap = (
            sum(c.retrieval_overlap for c in self.cases) / n if n else 0.0
        )
        answers_changed = sum(1 for c in self.cases if c.answer_changed)

        print("═" * w)
        print("  RAG Pipeline A/B Migration Report")
        print("═" * w)
        print(f"  Config A: {self.config_a.label}")
        print(f"  Config B: {self.config_b.label}")
        print(f"  Test cases: {n}")
        print("─" * w)

        # -- Retrieval stability --
        print("  RETRIEVAL STABILITY")
        print(f"  Average doc overlap: {avg_overlap:.1%} (same docs retrieved)")
        print(f"  Cases with different docs: {diff_docs}/{n}")
        print("─" * w)

        # -- Answer changes --
        pct = (answers_changed / n * 100) if n else 0.0
        print("  ANSWER CHANGES")
        print(f"  Answers changed: {answers_changed}/{n} ({pct:.1f}%)")
        print("  (Different wording is expected; check quality scores)")
        print("─" * w)

        # -- Timing table --
        print("  TIMING (avg per query)")
        print(f"  {'Step':<12}{'Config A':>10}{'Config B':>12}{'Delta':>10}")

        total_a = 0.0
        total_b = 0.0
        for tm in self.timing_metrics:
            total_a += tm.value_a
            total_b += tm.value_b
            delta_str = _format_delta(tm.value_a, tm.value_b, tm.delta_pct)
            print(
                f"  {tm.step:<12}{tm.value_a:>9.0f}ms{tm.value_b:>11.0f}ms{delta_str:>10}"
            )

        total_delta_pct = (
            ((total_b - total_a) / total_a * 100) if total_a else 0.0
        )
        total_delta_str = _format_delta(total_a, total_b, total_delta_pct)
        print(
            f"  {'TOTAL':<12}{total_a:>9.0f}ms{total_b:>11.0f}ms{total_delta_str:>10}"
        )
        print("─" * w)

        # -- Verdict --
        print("  VERDICT:", end="")
        verdicts: list[str] = []
        if avg_overlap >= 0.95:
            verdicts.append("No retrieval regression detected.")
        elif avg_overlap >= 0.80:
            verdicts.append(
                f"Minor retrieval drift ({avg_overlap:.0%} overlap)."
            )
        else:
            verdicts.append(
                f"⚠ Significant retrieval change ({avg_overlap:.0%} overlap)."
            )

        gen_metric = next(
            (t for t in self.timing_metrics if t.step == "generate"), None
        )
        if gen_metric and gen_metric.delta_pct < -5:
            verdicts.append(
                f"Generation latency improved {abs(gen_metric.delta_pct):.0f}%."
            )
        elif gen_metric and gen_metric.delta_pct > 10:
            verdicts.append(
                f"⚠ Generation latency increased {gen_metric.delta_pct:.0f}%."
            )

        verdicts.append("Run evaluate_pipeline.py for quality scoring.")

        # Print first verdict on the same line, rest indented
        print(f" {verdicts[0]}")
        for v in verdicts[1:]:
            print(f"           {v}")

        print("═" * w)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_delta(val_a: float, val_b: float, delta_pct: float) -> str:
    """Format a timing delta for display."""
    if val_a == 0 and val_b == 0:
        return "-"
    if val_a == 0:
        return "new"
    sign = "+" if delta_pct >= 0 else ""
    return f"{sign}{delta_pct:.0f}%"


# ---------------------------------------------------------------------------
# Core comparison logic
# ---------------------------------------------------------------------------

def _retrieval_overlap(ids_a: list[str], ids_b: list[str]) -> float:
    """Jaccard similarity between two sets of retrieved doc IDs."""
    set_a = set(ids_a)
    set_b = set(ids_b)
    if not set_a and not set_b:
        return 1.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _avg_timing(results: list[PipelineResult], step: str) -> float:
    """Average timing in ms for a specific step across all results."""
    values = [
        t.duration_ms
        for r in results
        for t in r.timings
        if t.step == step
    ]
    return sum(values) / len(values) if values else 0.0


def compare_configs(
    kb: KnowledgeBase,
    config_a: PipelineConfig,
    config_b: PipelineConfig,
    queries: list[str],
    endpoint: str | None = None,
    api_key: str | None = None,
) -> MigrationReport:
    """Run the same queries through two pipeline configs and compare.

    Args:
        kb: Pre-loaded and embedded knowledge base
        config_a: Source model configuration (current/baseline)
        config_b: Target model configuration (new/candidate)
        queries: List of test queries to run
        endpoint: Azure OpenAI endpoint
        api_key: API key (None for Entra ID)

    Returns:
        MigrationReport with per-step and end-to-end comparison
    """
    pipeline_a = RAGPipeline(kb, config_a, endpoint, api_key)
    pipeline_b = RAGPipeline(kb, config_b, endpoint, api_key)

    cases: list[CaseComparison] = []
    results_a: list[PipelineResult] = []
    results_b: list[PipelineResult] = []

    for i, query in enumerate(queries, 1):
        print(f"  [{i}/{len(queries)}] {query[:60]}...")
        result_a = pipeline_a.run(query)
        result_b = pipeline_b.run(query)
        results_a.append(result_a)
        results_b.append(result_b)
        cases.append(CaseComparison(
            query=query,
            result_a=result_a,
            result_b=result_b,
            retrieval_overlap=_retrieval_overlap(
                result_a.retrieved_ids, result_b.retrieved_ids,
            ),
            answer_changed=(result_a.answer != result_b.answer),
        ))

    # Build per-step timing comparisons
    steps = ["rephrase", "embed", "retrieve", "generate"]
    timing_metrics: list[StepComparison] = []
    for step in steps:
        avg_a = _avg_timing(results_a, step)
        avg_b = _avg_timing(results_b, step)
        delta = avg_b - avg_a
        delta_pct = (delta / avg_a * 100) if avg_a else 0.0
        # For timing, higher is worse → B regressed if delta > 0
        timing_metrics.append(StepComparison(
            step=step,
            metric="avg_ms",
            value_a=avg_a,
            value_b=avg_b,
            delta=delta,
            delta_pct=delta_pct,
            regression=(delta_pct > 10),  # >10% slower = regression
        ))

    # Build retrieval comparison
    avg_overlap = (
        sum(c.retrieval_overlap for c in cases) / len(cases)
        if cases
        else 1.0
    )
    retrieval_metrics = [
        StepComparison(
            step="retrieve",
            metric="jaccard_overlap",
            value_a=1.0,
            value_b=avg_overlap,
            delta=avg_overlap - 1.0,
            delta_pct=(avg_overlap - 1.0) * 100,
            regression=(avg_overlap < 0.80),
        ),
    ]

    return MigrationReport(
        config_a=config_a,
        config_b=config_b,
        cases=cases,
        retrieval_metrics=retrieval_metrics,
        timing_metrics=timing_metrics,
    )


def compare_from_golden(
    kb: KnowledgeBase,
    config_a: PipelineConfig,
    config_b: PipelineConfig,
    golden_path: str,
    endpoint: str | None = None,
    api_key: str | None = None,
) -> MigrationReport:
    """Load queries from a golden test file and compare configs.

    Reads a JSONL file where each line has at least a ``"query"`` field.
    Additional fields (``expected_answer``, ``expected_doc_ids``) are
    ignored here — they are used by evaluate_pipeline.py for quality scoring.
    """
    queries: list[str] = []
    with open(golden_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            queries.append(record["query"])

    if not queries:
        raise ValueError(f"No queries found in {golden_path}")

    print(f"  Loaded {len(queries)} test queries from {golden_path}")
    return compare_configs(kb, config_a, config_b, queries, endpoint, api_key)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    print("=" * 70)
    print("RAG Pipeline A/B Migration Comparison")
    print("=" * 70)

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")  # None → Entra ID auth

    # -- Config A: baseline (current production model) --
    config_a = PipelineConfig(
        generator_model="gpt-4o",
        embedding_model="text-embedding-3-large",
        temperature=0.0,
        top_k=3,
    )

    # -- Config B: candidate (migration target) --
    config_b = PipelineConfig(
        generator_model="gpt-4.1",
        embedding_model="text-embedding-3-large",
        temperature=0.0,
        top_k=3,
    )

    # -- Load knowledge base --
    docs_path = DATA_DIR / "documents.json"
    if not docs_path.exists():
        print(f"ERROR: Documents not found at {docs_path}")
        print("Place your documents.json in samples/rag_pipeline/data/")
        sys.exit(1)

    print(f"\n  Loading knowledge base from {docs_path}...")
    kb = KnowledgeBase.from_json(str(docs_path))

    print(f"  Embedding {len(kb)} documents...")
    embed_client = create_client(
        config_a.embedding_model, endpoint, api_key,
    )
    kb.embed_documents(embed_client)
    print(f"  {kb}\n")

    # -- Run comparison --
    golden_path = DATA_DIR / "golden_tests.jsonl"
    if golden_path.exists():
        print(f"  Using golden test file: {golden_path}\n")
        report = compare_from_golden(
            kb, config_a, config_b, str(golden_path), endpoint, api_key,
        )
    else:
        # Fallback: a handful of representative queries
        print("  No golden_tests.jsonl found — using built-in sample queries.\n")
        sample_queries = [
            "What is the password expiration policy?",
            "How do I request access to a new system?",
            "What are the MFA requirements for privileged accounts?",
            "How should confidential data be encrypted?",
            "What is the incident response process?",
        ]
        report = compare_configs(
            kb, config_a, config_b, sample_queries, endpoint, api_key,
        )

    # -- Print results --
    print()
    report.print_report()
