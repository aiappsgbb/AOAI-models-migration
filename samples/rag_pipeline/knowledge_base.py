"""In-memory vector store for the RAG pipeline sample.

Uses numpy cosine similarity — no external vector DB required.
Designed to demonstrate model migration evaluation, not production retrieval.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from openai import OpenAI


@dataclass
class Document:
    """A single document in the knowledge base."""

    id: str
    title: str
    content: str
    category: str
    embedding: list[float] | None = None


@dataclass
class SearchResult:
    """A ranked search result returned by the knowledge base."""

    document: Document
    score: float
    rank: int


class KnowledgeBase:
    """In-memory vector store backed by numpy cosine similarity.

    Parameters
    ----------
    documents : list[Document]
        The corpus of documents to index.
    embedding_model : str
        Name of the OpenAI embedding model to use.
    """

    _EMBED_BATCH_SIZE = 20

    def __init__(
        self,
        documents: list[Document],
        embedding_model: str = "text-embedding-3-large",
    ) -> None:
        self.documents = documents
        self.embedding_model = embedding_model

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_json(
        cls,
        path: str,
        embedding_model: str = "text-embedding-3-large",
    ) -> KnowledgeBase:
        """Load documents from a JSON file.

        The file should contain a JSON array of objects with keys
        ``id``, ``title``, ``content``, and ``category``.
        """
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        docs = [
            Document(
                id=item["id"],
                title=item["title"],
                content=item["content"],
                category=item["category"],
            )
            for item in raw
        ]
        return cls(docs, embedding_model=embedding_model)

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def embed_documents(self, client: OpenAI) -> None:
        """Embed every document using the provided OpenAI client.

        Documents are batched in groups of
        :pyattr:`_EMBED_BATCH_SIZE` for efficiency.
        """
        for start in range(0, len(self.documents), self._EMBED_BATCH_SIZE):
            batch = self.documents[start : start + self._EMBED_BATCH_SIZE]
            texts = [doc.content for doc in batch]
            response = client.embeddings.create(
                model=self.embedding_model,
                input=texts,
            )
            for doc, embedding_obj in zip(batch, response.data):
                doc.embedding = embedding_obj.embedding

    def embed_query(self, client: OpenAI, query: str) -> list[float]:
        """Embed a single query string and return the vector."""
        response = client.embeddings.create(
            model=self.embedding_model,
            input=query,
        )
        return response.data[0].embedding

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 3,
    ) -> list[SearchResult]:
        """Return the *top_k* most similar documents by cosine similarity.

        Raises
        ------
        ValueError
            If documents have not been embedded yet
            (call :pymeth:`embed_documents` first).
        """
        if not self.documents or self.documents[0].embedding is None:
            raise ValueError(
                "Documents have not been embedded yet. "
                "Call embed_documents() before searching."
            )

        query_vec = np.asarray(query_embedding, dtype=np.float64)
        query_norm = np.linalg.norm(query_vec)

        scores: list[tuple[int, float]] = []
        for idx, doc in enumerate(self.documents):
            doc_vec = np.asarray(doc.embedding, dtype=np.float64)
            dot = np.dot(query_vec, doc_vec)
            denom = query_norm * np.linalg.norm(doc_vec)
            similarity = float(dot / denom) if denom != 0 else 0.0
            scores.append((idx, similarity))

        scores.sort(key=lambda t: t[1], reverse=True)

        results: list[SearchResult] = []
        for rank, (idx, score) in enumerate(scores[: top_k], start=1):
            results.append(
                SearchResult(
                    document=self.documents[idx],
                    score=score,
                    rank=rank,
                )
            )
        return results

    def search_by_text(
        self,
        client: OpenAI,
        query: str,
        top_k: int = 3,
    ) -> list[SearchResult]:
        """Convenience wrapper: embed *query* then search."""
        query_embedding = self.embed_query(client, query)
        return self.search(query_embedding, top_k=top_k)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.documents)

    def __repr__(self) -> str:
        embedded = sum(1 for d in self.documents if d.embedding is not None)
        return (
            f"KnowledgeBase(documents={len(self.documents)}, "
            f"embedded={embedded}, model={self.embedding_model!r})"
        )
