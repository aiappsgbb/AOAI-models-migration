"""RAG pipeline with explicit, swappable steps for migration testing.

Each step (rephrase → embed → retrieve → generate) is isolated so you can:
1. Swap individual models without touching the rest of the pipeline
2. Evaluate each step independently (task-level evaluation)
3. Evaluate the full pipeline end-to-end
4. Compare old vs new model configs with per-step regression analysis

Uses src/clients.py for model calls — inherits error handling, API adaptation,
and role conversion automatically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import AzureOpenAI, OpenAI

# Resolve paths so imports work from anywhere
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
import sys
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.clients import create_client, call_model
from samples.rag_pipeline.knowledge_base import (
    KnowledgeBase,
    SearchResult,
)


# Data classes for pipeline results
@dataclass
class StepTiming:
    """Timing for a single pipeline step."""
    step: str
    duration_ms: float


@dataclass
class PipelineResult:
    """Full result from a single RAG pipeline run, with all intermediates."""

    query: str
    rephrased_query: str | None
    retrieved_docs: list[SearchResult]
    retrieved_ids: list[str]
    context_text: str
    answer: str
    timings: list[StepTiming] = field(default_factory=list)

    @property
    def total_ms(self) -> float:
        return sum(t.duration_ms for t in self.timings)

    def __repr__(self) -> str:
        n_docs = len(self.retrieved_docs)
        total = f"{self.total_ms:.0f}ms"
        return f"PipelineResult(docs={n_docs}, total={total}, answer={self.answer[:80]!r}...)"


# Pipeline configuration
@dataclass
class PipelineConfig:
    """Model configuration for the RAG pipeline.

    Swap models here to test migration impact.
    """
    generator_model: str = "gpt-4o"
    generator_deployment: str | None = None
    rephraser_model: str | None = None  # None = skip rephrasing
    rephraser_deployment: str | None = None
    embedding_model: str = "text-embedding-3-large"
    top_k: int = 3
    max_tokens: int = 512
    temperature: float = 0.0

    @property
    def label(self) -> str:
        """Short human-readable label for this config."""
        parts = [f"gen={self.generator_model}"]
        if self.rephraser_model:
            parts.append(f"reph={self.rephraser_model}")
        parts.append(f"emb={self.embedding_model}")
        return ", ".join(parts)


# RAG Pipeline
REPHRASE_SYSTEM_PROMPT = (
    "You are a query rewriter. Given a user question, rewrite it to be more "
    "specific and effective for searching a knowledge base. Return ONLY the "
    "rewritten query, nothing else."
)

GENERATE_SYSTEM_PROMPT = (
    "You are a helpful assistant that answers questions based on the provided "
    "context documents. Answer accurately and concisely using ONLY the "
    "information in the context. If the context does not contain enough "
    "information to answer, say so clearly."
)


class RAGPipeline:
    """Multi-step RAG pipeline with explicit step separation.

    Parameters
    ----------
    knowledge_base : KnowledgeBase
        Pre-loaded and embedded knowledge base.
    config : PipelineConfig
        Model and retrieval configuration.
    endpoint : str | None
        Azure OpenAI endpoint (or AZURE_OPENAI_ENDPOINT env var).
    api_key : str | None
        API key (or use Entra ID if None).
    """

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        config: PipelineConfig | None = None,
        endpoint: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.kb = knowledge_base
        self.config = config or PipelineConfig()
        self._endpoint = endpoint
        self._api_key = api_key
        self._clients: dict[str, Any] = {}

    def _get_client(self, model_name: str) -> AzureOpenAI | OpenAI:
        """Lazy client creation — one client per model."""
        if model_name not in self._clients:
            self._clients[model_name] = create_client(
                model_name, self._endpoint, self._api_key,
            )
        return self._clients[model_name]

    # Step 1: Query rephrasing (optional)
    def rephrase_query(self, query: str) -> str | None:
        """Rephrase the query for better retrieval. Returns None if disabled."""
        if not self.config.rephraser_model:
            return None

        client = self._get_client(self.config.rephraser_model)
        messages = [
            {"role": "system", "content": REPHRASE_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]
        response = call_model(
            client,
            self.config.rephraser_model,
            messages,
            deployment=self.config.rephraser_deployment,
            temperature=0.0,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()

    # Step 2: Embedding
    def embed_query(self, query: str) -> list[float]:
        """Embed the query using the configured embedding model."""
        client = self._get_client(self.config.embedding_model)
        return self.kb.embed_query(client, query)

    # Step 3: Retrieval
    def retrieve(
        self, query_embedding: list[float], top_k: int | None = None,
    ) -> list[SearchResult]:
        """Retrieve top-k documents from the knowledge base."""
        return self.kb.search(query_embedding, top_k=top_k or self.config.top_k)

    # Step 4: Answer generation
    def generate_answer(self, query: str, context_text: str) -> str:
        """Generate an answer grounded in the retrieved context."""
        client = self._get_client(self.config.generator_model)
        user_message = (
            f"Context:\n{context_text}\n\n"
            f"Question: {query}\n\n"
            f"Answer based only on the context above."
        )
        messages = [
            {"role": "system", "content": GENERATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        response = call_model(
            client,
            self.config.generator_model,
            messages,
            deployment=self.config.generator_deployment,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        return response.choices[0].message.content.strip()

    # Full pipeline
    def _format_context(self, results: list[SearchResult]) -> str:
        """Format retrieved documents into a context string."""
        parts = []
        for r in results:
            parts.append(
                f"[{r.document.id}] {r.document.title}\n{r.document.content}"
            )
        return "\n\n---\n\n".join(parts)

    def run(self, query: str) -> PipelineResult:
        """Execute the full RAG pipeline and return all intermediate results.

        Steps:
        1. Rephrase query (if rephraser model configured)
        2. Embed query
        3. Retrieve top-k documents
        4. Generate answer from retrieved context

        Returns a PipelineResult with all intermediates for evaluation.
        """
        timings: list[StepTiming] = []

        # Step 1: Rephrase
        t0 = time.perf_counter()
        rephrased = self.rephrase_query(query)
        search_query = rephrased or query
        timings.append(StepTiming(
            "rephrase", (time.perf_counter() - t0) * 1000,
        ))

        # Step 2: Embed
        t0 = time.perf_counter()
        query_embedding = self.embed_query(search_query)
        timings.append(StepTiming(
            "embed", (time.perf_counter() - t0) * 1000,
        ))

        # Step 3: Retrieve
        t0 = time.perf_counter()
        results = self.retrieve(query_embedding)
        timings.append(StepTiming(
            "retrieve", (time.perf_counter() - t0) * 1000,
        ))

        # Step 4: Generate
        context_text = self._format_context(results)
        t0 = time.perf_counter()
        answer = self.generate_answer(query, context_text)
        timings.append(StepTiming(
            "generate", (time.perf_counter() - t0) * 1000,
        ))

        return PipelineResult(
            query=query,
            rephrased_query=rephrased,
            retrieved_docs=results,
            retrieved_ids=[r.document.id for r in results],
            context_text=context_text,
            answer=answer,
            timings=timings,
        )

    def __repr__(self) -> str:
        return f"RAGPipeline(config={self.config.label!r}, kb={self.kb!r})"
