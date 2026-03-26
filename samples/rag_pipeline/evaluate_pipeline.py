"""Dual-layer evaluation for the RAG pipeline.

Layer 1 — End-to-End: Run full pipeline, score final answer quality.
Layer 2 — Task-Level: Evaluate retrieval and generation in isolation.

Together they implement the hybrid evaluation methodology described in
docs/migrating-multi-step-apps.md.

Why two layers?
- End-to-end catches regressions quickly (cheap, automatable).
- Task-level pinpoints *which step* regressed (retrieval vs generation).
  If end-to-end quality drops but isolated generation is fine, the
  problem is retrieval.  If isolated generation also drops, the model
  itself is the issue.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Resolve paths so imports work from anywhere
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.clients import create_client, call_model
from samples.rag_pipeline.pipeline import RAGPipeline, PipelineConfig
from samples.rag_pipeline.knowledge_base import KnowledgeBase


# ---------------------------------------------------------------------------
# Test case loading
# ---------------------------------------------------------------------------

@dataclass
class RAGTestCase:
    """A single golden test case for RAG evaluation."""

    query: str
    expected_doc_ids: list[str]
    expected_answer: str
    category: str


def load_golden_tests(path: str) -> list[RAGTestCase]:
    """Load golden test cases from a JSONL file.

    Each line must be a JSON object with keys:
    ``query``, ``expected_doc_ids``, ``expected_answer``, ``category``.
    """
    tests: list[RAGTestCase] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            tests.append(
                RAGTestCase(
                    query=obj["query"],
                    expected_doc_ids=obj["expected_doc_ids"],
                    expected_answer=obj["expected_answer"],
                    category=obj.get("category", "general"),
                )
            )
    return tests


# ---------------------------------------------------------------------------
# LLM-as-judge helper
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM_PROMPT = """\
You are an expert evaluator. Given a question, context documents, an answer, \
and an expected answer, rate the answer on three dimensions (1-5 scale):

1. GROUNDEDNESS: Is the answer supported by the context? \
(1=hallucinated, 5=fully grounded)
2. RELEVANCE: Does the answer address the question? \
(1=off-topic, 5=directly answers)
3. CORRECTNESS: Is the answer factually aligned with the expected answer? \
(1=wrong, 5=matches expected)

Return your ratings as JSON: {"groundedness": X, "relevance": X, "correctness": X}
Do NOT include any other text — only the JSON object."""

_JUDGE_USER_TEMPLATE = """\
Question: {query}

Context documents:
{context}

Answer to evaluate:
{answer}

Expected answer:
{expected}"""


def judge_answer(
    client: Any,
    judge_model: str,
    query: str,
    context: str,
    answer: str,
    expected: str,
) -> dict[str, float | None]:
    """Use LLM-as-judge to score an answer on groundedness, relevance, correctness.

    Returns a dict with scores 1-5 for each metric.
    On parsing failure every score is ``None``.
    """
    user_content = _JUDGE_USER_TEMPLATE.format(
        query=query, context=context, answer=answer, expected=expected,
    )
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    try:
        response = call_model(
            client,
            judge_model,
            messages,
            temperature=0.0,
            max_tokens=128,
        )
        raw = response.choices[0].message.content.strip()
        # Extract the first JSON object from the response
        match = re.search(r"\{[^}]+\}", raw)
        if not match:
            return {"groundedness": None, "relevance": None, "correctness": None}
        scores = json.loads(match.group())
        return {
            "groundedness": float(scores.get("groundedness")) if scores.get("groundedness") is not None else None,
            "relevance": float(scores.get("relevance")) if scores.get("relevance") is not None else None,
            "correctness": float(scores.get("correctness")) if scores.get("correctness") is not None else None,
        }
    except Exception:
        return {"groundedness": None, "relevance": None, "correctness": None}


# ---------------------------------------------------------------------------
# Layer 1: End-to-End Evaluation
# ---------------------------------------------------------------------------

@dataclass
class EndToEndResult:
    """Result from a single end-to-end evaluation run."""

    query: str
    answer: str
    groundedness: float | None = None
    relevance: float | None = None
    correctness: float | None = None
    retrieved_ids: list[str] = field(default_factory=list)


def evaluate_end_to_end(
    pipeline: RAGPipeline,
    test_cases: list[RAGTestCase],
    judge_model: str = "gpt-4o",
) -> list[EndToEndResult]:
    """Run the full pipeline on all test cases, score with LLM-as-judge.

    This is the *monitoring layer* — cheap, automatable, answers
    "did quality change after a model swap?"
    """
    judge_client = create_client(judge_model)
    results: list[EndToEndResult] = []

    for tc in test_cases:
        pr = pipeline.run(tc.query)
        scores = judge_answer(
            judge_client,
            judge_model,
            query=tc.query,
            context=pr.context_text,
            answer=pr.answer,
            expected=tc.expected_answer,
        )
        results.append(
            EndToEndResult(
                query=tc.query,
                answer=pr.answer,
                groundedness=scores["groundedness"],
                relevance=scores["relevance"],
                correctness=scores["correctness"],
                retrieved_ids=pr.retrieved_ids,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Layer 2a: Task-Level Retrieval Evaluation
# ---------------------------------------------------------------------------

@dataclass
class RetrievalResult:
    """Deterministic retrieval metrics for a single query."""

    query: str
    retrieved_ids: list[str]
    expected_ids: list[str]
    precision: float  # |retrieved ∩ expected| / k
    recall: float     # |retrieved ∩ expected| / |expected|
    mrr: float        # 1 / rank of first relevant doc (0 if none)


def _compute_retrieval_metrics(
    retrieved_ids: list[str],
    expected_ids: list[str],
) -> tuple[float, float, float]:
    """Return (precision@k, recall@k, MRR)."""
    expected_set = set(expected_ids)
    k = len(retrieved_ids)

    overlap = sum(1 for rid in retrieved_ids if rid in expected_set)
    precision = overlap / k if k > 0 else 0.0
    recall = overlap / len(expected_set) if expected_set else 0.0

    # MRR: 1/rank of the first relevant hit
    mrr = 0.0
    for rank, rid in enumerate(retrieved_ids, start=1):
        if rid in expected_set:
            mrr = 1.0 / rank
            break

    return precision, recall, mrr


def evaluate_retrieval(
    pipeline: RAGPipeline,
    test_cases: list[RAGTestCase],
) -> list[RetrievalResult]:
    """Evaluate retrieval quality WITHOUT running generation.

    No LLM calls needed for scoring — pure computation.
    Answers: "Is the retrieval step finding the right documents?"

    Steps per test case:
    1. Optionally rephrase the query (uses the pipeline's rephraser)
    2. Embed the (rephrased) query
    3. Retrieve top-k documents
    4. Compare retrieved IDs against expected IDs
    5. Compute precision@k, recall@k, MRR
    """
    results: list[RetrievalResult] = []

    for tc in test_cases:
        rephrased = pipeline.rephrase_query(tc.query)
        search_query = rephrased or tc.query
        query_embedding = pipeline.embed_query(search_query)
        hits = pipeline.retrieve(query_embedding)
        retrieved_ids = [h.document.id for h in hits]

        precision, recall, mrr = _compute_retrieval_metrics(
            retrieved_ids, tc.expected_doc_ids,
        )
        results.append(
            RetrievalResult(
                query=tc.query,
                retrieved_ids=retrieved_ids,
                expected_ids=tc.expected_doc_ids,
                precision=precision,
                recall=recall,
                mrr=mrr,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Layer 2b: Task-Level Generation Evaluation
# ---------------------------------------------------------------------------

@dataclass
class GenerationResult:
    """LLM-as-judge scores for generation given *correct* context."""

    query: str
    answer: str
    groundedness: float | None = None
    relevance: float | None = None
    correctness: float | None = None
    used_correct_context: bool = True  # Always True for isolated gen eval


def evaluate_generation_isolated(
    pipeline: RAGPipeline,
    test_cases: list[RAGTestCase],
    kb: KnowledgeBase,
    judge_model: str = "gpt-4o",
) -> list[GenerationResult]:
    """Evaluate generation quality with CORRECT context fed in.

    Isolates generation from retrieval:
    - Instead of using retrieved docs, feed the expected (correct) context.
    - This tells you: "If retrieval were perfect, how good is the answer?"

    Answers: "Is the generation model the problem, or is it the retrieval?"

    Steps per test case:
    1. Look up expected_doc_ids from the knowledge base
    2. Build context string from those docs (bypass retrieval)
    3. Call ``generate_answer()`` with the correct context
    4. Score the answer with LLM-as-judge
    """
    judge_client = create_client(judge_model)
    doc_index = {doc.id: doc for doc in kb.documents}
    results: list[GenerationResult] = []

    for tc in test_cases:
        # Build context from expected docs, skipping any missing IDs
        context_parts: list[str] = []
        for doc_id in tc.expected_doc_ids:
            doc = doc_index.get(doc_id)
            if doc:
                context_parts.append(f"[{doc.id}] {doc.title}\n{doc.content}")
        context_text = "\n\n---\n\n".join(context_parts)

        answer = pipeline.generate_answer(tc.query, context_text)

        scores = judge_answer(
            judge_client,
            judge_model,
            query=tc.query,
            context=context_text,
            answer=answer,
            expected=tc.expected_answer,
        )
        results.append(
            GenerationResult(
                query=tc.query,
                answer=answer,
                groundedness=scores["groundedness"],
                relevance=scores["relevance"],
                correctness=scores["correctness"],
            )
        )
    return results


# ---------------------------------------------------------------------------
# Combined Report
# ---------------------------------------------------------------------------

def _avg(values: list[float | None]) -> float | None:
    """Return the average of non-None values, or None if all are None."""
    nums = [v for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


def _fmt(score: float | None) -> str:
    """Format a score for display."""
    return f"{score:.2f}" if score is not None else "N/A"


@dataclass
class DualLayerReport:
    """Combined report from both evaluation layers."""

    end_to_end: list[EndToEndResult]
    retrieval: list[RetrievalResult]
    generation: list[GenerationResult]

    def print_summary(self) -> None:
        """Print a concise summary of both evaluation layers."""
        n = len(self.end_to_end)
        print(f"\n{'=' * 60}")
        print(f"  Dual-Layer RAG Evaluation Report  ({n} test cases)")
        print(f"{'=' * 60}")

        # --- End-to-end ---
        e2e_g = _avg([r.groundedness for r in self.end_to_end])
        e2e_r = _avg([r.relevance for r in self.end_to_end])
        e2e_c = _avg([r.correctness for r in self.end_to_end])
        print(f"\n  Layer 1: End-to-End (full pipeline)")
        print(f"    Groundedness : {_fmt(e2e_g)}")
        print(f"    Relevance    : {_fmt(e2e_r)}")
        print(f"    Correctness  : {_fmt(e2e_c)}")

        # --- Retrieval ---
        avg_p = _avg([r.precision for r in self.retrieval])
        avg_r = _avg([r.recall for r in self.retrieval])
        avg_mrr = _avg([r.mrr for r in self.retrieval])
        print(f"\n  Layer 2a: Retrieval (deterministic)")
        print(f"    Precision@k  : {_fmt(avg_p)}")
        print(f"    Recall@k     : {_fmt(avg_r)}")
        print(f"    MRR          : {_fmt(avg_mrr)}")

        # --- Isolated generation ---
        gen_g = _avg([r.groundedness for r in self.generation])
        gen_r = _avg([r.relevance for r in self.generation])
        gen_c = _avg([r.correctness for r in self.generation])
        print(f"\n  Layer 2b: Generation (isolated, correct context)")
        print(f"    Groundedness : {_fmt(gen_g)}")
        print(f"    Relevance    : {_fmt(gen_r)}")
        print(f"    Correctness  : {_fmt(gen_c)}")

        # --- Diagnostic highlights ---
        print(f"\n  Diagnostics")
        print(f"  {'-' * 40}")

        # Cases where end-to-end failed but generation passed → retrieval problem
        retrieval_issues: list[str] = []
        for e2e, gen in zip(self.end_to_end, self.generation):
            e2e_ok = e2e.correctness is not None and e2e.correctness >= 4
            gen_ok = gen.correctness is not None and gen.correctness >= 4
            if gen_ok and not e2e_ok:
                retrieval_issues.append(e2e.query)

        if retrieval_issues:
            print(f"    Retrieval problems (e2e failed, gen OK): {len(retrieval_issues)}")
            for q in retrieval_issues[:5]:
                print(f"      - {q[:70]}")
        else:
            print(f"    No retrieval-specific problems detected.")

        # Cases where generation also failed → model problem
        model_issues: list[str] = []
        for gen in self.generation:
            gen_ok = gen.correctness is not None and gen.correctness >= 4
            if not gen_ok:
                model_issues.append(gen.query)

        if model_issues:
            print(f"    Model problems (gen failed with correct context): {len(model_issues)}")
            for q in model_issues[:5]:
                print(f"      - {q[:70]}")
        else:
            print(f"    No generation-model problems detected.")

        print(f"\n{'=' * 60}\n")


def evaluate_dual_layer(
    pipeline: RAGPipeline,
    golden_path: str,
    kb: KnowledgeBase,
    judge_model: str = "gpt-4o",
) -> DualLayerReport:
    """Run both evaluation layers and return a combined report.

    This is the main entry point for the hybrid evaluation methodology:
    1. Load golden test cases from *golden_path* (JSONL).
    2. Run end-to-end evaluation (full pipeline + LLM judge).
    3. Run retrieval evaluation (deterministic metrics).
    4. Run isolated generation evaluation (correct context + LLM judge).
    5. Return a ``DualLayerReport`` with all results.
    """
    test_cases = load_golden_tests(golden_path)
    print(f"Loaded {len(test_cases)} golden test cases from {golden_path}")

    print("Running Layer 1: End-to-End evaluation ...")
    e2e = evaluate_end_to_end(pipeline, test_cases, judge_model=judge_model)

    print("Running Layer 2a: Retrieval evaluation ...")
    retrieval = evaluate_retrieval(pipeline, test_cases)

    print("Running Layer 2b: Isolated generation evaluation ...")
    generation = evaluate_generation_isolated(
        pipeline, test_cases, kb, judge_model=judge_model,
    )

    return DualLayerReport(
        end_to_end=e2e,
        retrieval=retrieval,
        generation=generation,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Dual-layer evaluation for the RAG pipeline.",
    )
    parser.add_argument(
        "--kb-path",
        default="data/knowledge_base.json",
        help="Path to knowledge base JSON file (default: data/knowledge_base.json)",
    )
    parser.add_argument(
        "--golden-path",
        default="data/golden_tests.jsonl",
        help="Path to golden test cases JSONL file (default: data/golden_tests.jsonl)",
    )
    parser.add_argument(
        "--judge-model",
        default="gpt-4o",
        help="Model to use as LLM judge (default: gpt-4o)",
    )
    parser.add_argument(
        "--generator-model",
        default="gpt-4o",
        help="Generator model for the pipeline (default: gpt-4o)",
    )
    parser.add_argument(
        "--embedding-model",
        default="text-embedding-3-large",
        help="Embedding model (default: text-embedding-3-large)",
    )
    args = parser.parse_args()

    # Load knowledge base
    print(f"Loading knowledge base from {args.kb_path} ...")
    kb = KnowledgeBase.from_json(args.kb_path, embedding_model=args.embedding_model)

    # Embed documents
    embedding_client = create_client(args.embedding_model)
    print("Embedding documents ...")
    kb.embed_documents(embedding_client)

    # Create pipeline
    config = PipelineConfig(
        generator_model=args.generator_model,
        embedding_model=args.embedding_model,
    )
    pipeline = RAGPipeline(kb, config)
    print(f"Pipeline: {pipeline}")

    # Run dual-layer evaluation
    report = evaluate_dual_layer(
        pipeline, args.golden_path, kb, judge_model=args.judge_model,
    )
    report.print_summary()
