#!/usr/bin/env python3
"""End-to-end test of the RAG pipeline migration workflow.

Runs the FULL pipeline with real Azure OpenAI models:
1. Embeds 20 knowledge base documents
2. Runs 15 golden test cases through gpt-4o (baseline)
3. Runs same tests through gpt-4.1 (migration target)
4. Compares retrieval stability, answer changes, and timing
5. Runs dual-layer evaluation with LLM-as-judge

This script proves the approach works end-to-end with real API calls.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Setup paths
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(_REPO_ROOT / ".env")

from src.clients import create_client, call_model
from src.config import load_config
from samples.rag_pipeline.knowledge_base import KnowledgeBase
from samples.rag_pipeline.pipeline import RAGPipeline, PipelineConfig, PipelineResult
from samples.rag_pipeline.evaluate_pipeline import (
    load_golden_tests, evaluate_retrieval, evaluate_end_to_end,
    evaluate_generation_isolated, DualLayerReport,
)
from samples.rag_pipeline.migrate_and_compare import compare_from_golden

# Paths
DATA_DIR = _REPO_ROOT / "samples" / "rag_pipeline" / "data"
DOCS_PATH = DATA_DIR / "documents.json"
GOLDEN_PATH = DATA_DIR / "golden_tests.jsonl"

# ── Config ────────────────────────────────────────────────────────────────
ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
# Using Entra ID auth — no API key needed

SOURCE_MODEL = "gpt-4o"
TARGET_MODEL = "gpt-4.1"
EMBEDDING_MODEL = "text-embedding-3-large"
JUDGE_MODEL = "gpt-4.1"  # LLM-as-judge for evaluation


def separator(title: str) -> None:
    print(f"\n{'═' * 70}")
    print(f"  {title}")
    print(f"{'═' * 70}\n")


def main():
    separator("RAG Pipeline E2E Test — Real Azure OpenAI Models")
    print(f"  Endpoint:    {ENDPOINT}")
    print(f"  Source:      {SOURCE_MODEL}")
    print(f"  Target:      {TARGET_MODEL}")
    print(f"  Embedding:   {EMBEDDING_MODEL}")
    print(f"  Judge:       {JUDGE_MODEL}")
    print()

    # ── Step 1: Load & Embed Knowledge Base ───────────────────────────────
    separator("Step 1: Loading & Embedding Knowledge Base")
    t0 = time.perf_counter()
    kb = KnowledgeBase.from_json(str(DOCS_PATH), embedding_model=EMBEDDING_MODEL)
    print(f"  Loaded {len(kb)} documents")

    embed_client = create_client(EMBEDDING_MODEL, ENDPOINT)
    kb.embed_documents(embed_client)
    embed_time = time.perf_counter() - t0
    print(f"  Embedded all documents in {embed_time:.1f}s")
    print(f"  Embedding dimension: {len(kb.documents[0].embedding)}")

    # ── Step 2: Run Pipeline — Source Model (gpt-4o) ─────────────────────
    separator(f"Step 2: Running Pipeline with {SOURCE_MODEL}")
    config_a = PipelineConfig(
        generator_model=SOURCE_MODEL,
        generator_deployment=SOURCE_MODEL,
        embedding_model=EMBEDDING_MODEL,
        top_k=3,
        max_tokens=300,
        temperature=0.0,
    )
    pipeline_a = RAGPipeline(kb, config_a, endpoint=ENDPOINT)

    golden_tests = load_golden_tests(str(GOLDEN_PATH))
    results_a: list[PipelineResult] = []
    for i, tc in enumerate(golden_tests):
        t0 = time.perf_counter()
        result = pipeline_a.run(tc.query)
        elapsed = (time.perf_counter() - t0) * 1000
        results_a.append(result)
        retrieved_str = ", ".join(result.retrieved_ids)
        print(f"  [{i+1:2d}/{len(golden_tests)}] {tc.query[:60]:<60s} → [{retrieved_str}] ({elapsed:.0f}ms)")

    avg_a = sum(r.total_ms for r in results_a) / len(results_a)
    print(f"\n  Average latency ({SOURCE_MODEL}): {avg_a:.0f}ms per query")

    # ── Step 3: Run Pipeline — Target Model (gpt-4.1) ────────────────────
    separator(f"Step 3: Running Pipeline with {TARGET_MODEL}")
    config_b = PipelineConfig(
        generator_model=TARGET_MODEL,
        generator_deployment=TARGET_MODEL,
        embedding_model=EMBEDDING_MODEL,
        top_k=3,
        max_tokens=300,
        temperature=0.0,
    )
    pipeline_b = RAGPipeline(kb, config_b, endpoint=ENDPOINT)

    results_b: list[PipelineResult] = []
    for i, tc in enumerate(golden_tests):
        t0 = time.perf_counter()
        result = pipeline_b.run(tc.query)
        elapsed = (time.perf_counter() - t0) * 1000
        results_b.append(result)
        retrieved_str = ", ".join(result.retrieved_ids)
        print(f"  [{i+1:2d}/{len(golden_tests)}] {tc.query[:60]:<60s} → [{retrieved_str}] ({elapsed:.0f}ms)")

    avg_b = sum(r.total_ms for r in results_b) / len(results_b)
    print(f"\n  Average latency ({TARGET_MODEL}): {avg_b:.0f}ms per query")

    # ── Step 4: Retrieval Comparison ──────────────────────────────────────
    separator("Step 4: Retrieval Stability Analysis")
    same_retrieval = 0
    for i, (ra, rb) in enumerate(zip(results_a, results_b)):
        overlap = len(set(ra.retrieved_ids) & set(rb.retrieved_ids)) / max(len(set(ra.retrieved_ids) | set(rb.retrieved_ids)), 1)
        if set(ra.retrieved_ids) == set(rb.retrieved_ids):
            same_retrieval += 1
        else:
            print(f"  Case {i+1}: {golden_tests[i].query[:50]}")
            print(f"    {SOURCE_MODEL}: {ra.retrieved_ids}")
            print(f"    {TARGET_MODEL}: {rb.retrieved_ids}")
            print(f"    Overlap: {overlap:.0%}")

    print(f"\n  Identical retrieval: {same_retrieval}/{len(golden_tests)} ({same_retrieval/len(golden_tests):.0%})")
    print(f"  (Same embedding model → retrieval should be 100% identical)")

    # ── Step 5: Task-Level Retrieval Evaluation ───────────────────────────
    separator("Step 5: Task-Level Retrieval Evaluation (vs Golden Expected)")
    retrieval_results_a = evaluate_retrieval(pipeline_a, golden_tests)
    retrieval_results_b = evaluate_retrieval(pipeline_b, golden_tests)

    avg_prec_a = sum(r.precision for r in retrieval_results_a) / len(retrieval_results_a)
    avg_rec_a = sum(r.recall for r in retrieval_results_a) / len(retrieval_results_a)
    avg_mrr_a = sum(r.mrr for r in retrieval_results_a) / len(retrieval_results_a)

    avg_prec_b = sum(r.precision for r in retrieval_results_b) / len(retrieval_results_b)
    avg_rec_b = sum(r.recall for r in retrieval_results_b) / len(retrieval_results_b)
    avg_mrr_b = sum(r.mrr for r in retrieval_results_b) / len(retrieval_results_b)

    print(f"  {'Metric':<20s} {SOURCE_MODEL:<15s} {TARGET_MODEL:<15s}")
    print(f"  {'─'*50}")
    print(f"  {'Precision@3':<20s} {avg_prec_a:<15.2%} {avg_prec_b:<15.2%}")
    print(f"  {'Recall@3':<20s} {avg_rec_a:<15.2%} {avg_rec_b:<15.2%}")
    print(f"  {'MRR':<20s} {avg_mrr_a:<15.3f} {avg_mrr_b:<15.3f}")

    # Show per-case retrieval details
    print(f"\n  Per-case retrieval (showing misses):")
    for i, (ra, rb) in enumerate(zip(retrieval_results_a, retrieval_results_b)):
        tc = golden_tests[i]
        if ra.recall < 1.0:
            print(f"    [{i+1}] {tc.query[:50]}")
            print(f"      Expected: {tc.expected_doc_ids}, Got: {ra.retrieved_ids}, Recall={ra.recall:.0%}")

    # ── Step 6: LLM-as-Judge End-to-End Evaluation ────────────────────────
    separator("Step 6: End-to-End LLM-as-Judge Evaluation")
    print(f"  Scoring answers with {JUDGE_MODEL}...")
    print(f"  (This makes {len(golden_tests) * 2} judge calls — may take a minute)")

    e2e_a = evaluate_end_to_end(pipeline_a, golden_tests, judge_model=JUDGE_MODEL)
    e2e_b = evaluate_end_to_end(pipeline_b, golden_tests, judge_model=JUDGE_MODEL)

    def avg_score(results, field):
        scores = [getattr(r, field) for r in results if getattr(r, field) is not None]
        return sum(scores) / len(scores) if scores else 0.0

    print(f"\n  {'Metric':<20s} {SOURCE_MODEL:<15s} {TARGET_MODEL:<15s} {'Delta':<10s}")
    print(f"  {'─'*55}")
    for metric in ["groundedness", "relevance", "correctness"]:
        a_val = avg_score(e2e_a, metric)
        b_val = avg_score(e2e_b, metric)
        delta = b_val - a_val
        arrow = "↑" if delta > 0 else "↓" if delta < 0 else "="
        print(f"  {metric:<20s} {a_val:<15.2f} {b_val:<15.2f} {delta:+.2f} {arrow}")

    # ── Step 7: Timing Comparison ─────────────────────────────────────────
    separator("Step 7: Timing Comparison")

    def avg_step_time(results, step):
        times = [t.duration_ms for r in results for t in r.timings if t.step == step]
        return sum(times) / len(times) if times else 0

    print(f"  {'Step':<15s} {SOURCE_MODEL:<15s} {TARGET_MODEL:<15s} {'Delta':<10s}")
    print(f"  {'─'*55}")
    for step in ["rephrase", "embed", "retrieve", "generate"]:
        a_ms = avg_step_time(results_a, step)
        b_ms = avg_step_time(results_b, step)
        delta_pct = ((b_ms - a_ms) / a_ms * 100) if a_ms > 0 else 0
        print(f"  {step:<15s} {a_ms:>8.0f}ms     {b_ms:>8.0f}ms     {delta_pct:+.0f}%")
    print(f"  {'TOTAL':<15s} {avg_a:>8.0f}ms     {avg_b:>8.0f}ms     {((avg_b-avg_a)/avg_a*100):+.0f}%")

    # ── Step 8: A/B Comparison Report ─────────────────────────────────────
    separator("Step 8: Full A/B Migration Report")
    report = compare_from_golden(
        kb=kb,
        config_a=config_a,
        config_b=config_b,
        golden_path=str(GOLDEN_PATH),
        endpoint=ENDPOINT,
    )
    report.print_report()

    # ── Summary ───────────────────────────────────────────────────────────
    separator("SUMMARY")
    print(f"  ✓ Embedded {len(kb)} documents with {EMBEDDING_MODEL}")
    print(f"  ✓ Ran {len(golden_tests)} tests × 2 models ({SOURCE_MODEL} → {TARGET_MODEL})")
    print(f"  ✓ Retrieval stability: {same_retrieval}/{len(golden_tests)} identical")
    print(f"  ✓ Precision@3: {avg_prec_a:.0%} → {avg_prec_b:.0%}")
    print(f"  ✓ Recall@3: {avg_rec_a:.0%} → {avg_rec_b:.0%}")
    gnd_a = avg_score(e2e_a, "groundedness")
    gnd_b = avg_score(e2e_b, "groundedness")
    rel_a = avg_score(e2e_a, "relevance")
    rel_b = avg_score(e2e_b, "relevance")
    cor_a = avg_score(e2e_a, "correctness")
    cor_b = avg_score(e2e_b, "correctness")
    print(f"  ✓ Groundedness: {gnd_a:.1f} → {gnd_b:.1f}")
    print(f"  ✓ Relevance: {rel_a:.1f} → {rel_b:.1f}")
    print(f"  ✓ Correctness: {cor_a:.1f} → {cor_b:.1f}")
    print(f"  ✓ Latency: {avg_a:.0f}ms → {avg_b:.0f}ms ({((avg_b-avg_a)/avg_a*100):+.0f}%)")
    print()

    # API cost estimate
    # Each pipeline run: 1 embedding + 1 generation ≈ ~500 tokens
    # Judge calls: ~800 tokens each
    total_calls = len(golden_tests) * 2  # 2 models
    judge_calls = len(golden_tests) * 2  # 2 models judged
    print(f"  Cost estimate:")
    print(f"    Pipeline calls: {total_calls} ({total_calls} generations + {total_calls} embeddings)")
    print(f"    Judge calls: {judge_calls}")
    print(f"    Estimated total: ~$0.50-1.00")
    print()
    print(f"  Total API calls: {total_calls + judge_calls + len(golden_tests)*2}")
    print(f"  This is what it takes to validate a model migration. 🚀")
    print()


if __name__ == "__main__":
    main()
