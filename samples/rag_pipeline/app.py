"""Chainlit chat UI for the RAG pipeline — demonstrates config-only model swap.

Run with:
    cd samples/rag_pipeline
    chainlit run app.py

Change the generator model live via the ⚙️ Settings panel (no code changes needed).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import chainlit as cl
from chainlit.input_widget import Select, Slider

# Resolve imports
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from samples.rag_pipeline.knowledge_base import KnowledgeBase
from samples.rag_pipeline.pipeline import RAGPipeline, PipelineConfig

# Available models — these should match your Azure OpenAI deployments
GENERATOR_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-5.4",
    "gpt-5.4-mini",
]

EMBEDDING_MODELS = [
    "text-embedding-3-large",
]

# Pre-load knowledge base once (shared across sessions)
_DATA_DIR = Path(__file__).resolve().parent / "data"
_kb: KnowledgeBase | None = None


def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase.from_json(str(_DATA_DIR / "documents.json"))
    return _kb


def build_pipeline(generator: str, top_k: int = 3) -> RAGPipeline:
    config = PipelineConfig(
        generator_model=generator,
        embedding_model=EMBEDDING_MODELS[0],
        top_k=top_k,
        temperature=0.0,
    )
    return RAGPipeline(knowledge_base=get_kb(), config=config)


@cl.on_chat_start
async def on_start():
    """Initialize session: embed KB, show model selector."""
    # Embed KB (one-time, cached in KnowledgeBase)
    kb = get_kb()
    if not kb.documents[0].embedding:
        msg = cl.Message(content="⏳ Embedding knowledge base (first run only)...")
        await msg.send()
        from src.clients import create_client
        embed_client = create_client(EMBEDDING_MODELS[0])
        kb.embed_documents(embed_client)
        msg.content = f"✅ Knowledge base ready — {len(kb.documents)} documents embedded."
        await msg.update()

    # Default model from env or first in list
    default_model = os.getenv("RAG_GENERATOR_MODEL", GENERATOR_MODELS[0])
    if default_model not in GENERATOR_MODELS:
        default_model = GENERATOR_MODELS[0]

    settings = await cl.ChatSettings(
        [
            Select(
                id="generator_model",
                label="Generator Model",
                values=GENERATOR_MODELS,
                initial_value=default_model,
            ),
            Slider(
                id="top_k",
                label="Retrieved Documents (top-k)",
                initial=3,
                min=1,
                max=5,
                step=1,
            ),
        ]
    ).send()

    # Store pipeline in session
    pipeline = build_pipeline(settings["generator_model"], int(settings["top_k"]))
    cl.user_session.set("pipeline", pipeline)
    cl.user_session.set("generator_model", settings["generator_model"])

    await cl.Message(
        content=(
            f"🤖 **RAG Pipeline ready** — generator: `{settings['generator_model']}`\n\n"
            f"Ask questions about IT policies, security, or cloud governance.\n"
            f"Change the model anytime via ⚙️ **Settings** — no code changes needed."
        )
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    """User changed the model in settings — rebuild pipeline (config-only swap)."""
    new_model = settings["generator_model"]
    top_k = int(settings["top_k"])
    old_model = cl.user_session.get("generator_model")

    pipeline = build_pipeline(new_model, top_k)
    cl.user_session.set("pipeline", pipeline)
    cl.user_session.set("generator_model", new_model)

    if new_model != old_model:
        await cl.Message(
            content=f"🔄 **Model swapped**: `{old_model}` → `{new_model}` (config-only, zero code changes)"
        ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """Handle user query — run RAG pipeline with visible intermediate steps."""
    pipeline: RAGPipeline = cl.user_session.get("pipeline")
    model_name = cl.user_session.get("generator_model")

    # Step 1: Rephrase
    async with cl.Step(name="🔄 Rephrase Query", type="tool") as step:
        t0 = time.perf_counter()
        rephrased = pipeline.rephrase_query(message.content)
        duration = (time.perf_counter() - t0) * 1000
        search_query = rephrased or message.content
        if rephrased:
            step.output = f"**Rephrased**: {rephrased}\n\n⏱️ {duration:.0f}ms"
        else:
            step.output = f"ℹ️ Rephrasing disabled — using original query\n\n⏱️ {duration:.0f}ms"

    # Step 2: Embed
    async with cl.Step(name="📐 Embed Query", type="tool") as step:
        t0 = time.perf_counter()
        query_embedding = pipeline.embed_query(search_query)
        duration = (time.perf_counter() - t0) * 1000
        step.output = f"Embedded to {len(query_embedding)}-dim vector\n\n⏱️ {duration:.0f}ms"

    # Step 3: Retrieve
    async with cl.Step(name="🔍 Retrieve Documents", type="retrieval") as step:
        t0 = time.perf_counter()
        results = pipeline.retrieve(query_embedding)
        duration = (time.perf_counter() - t0) * 1000

        docs_text = ""
        for i, r in enumerate(results, 1):
            docs_text += (
                f"**{i}. [{r.document.id}] {r.document.title}** "
                f"(similarity: {r.score:.3f})\n"
                f"> {r.document.content[:150]}...\n\n"
            )
        step.output = f"{docs_text}⏱️ {duration:.0f}ms"

    # Step 4: Generate
    context_text = pipeline._format_context(results)
    async with cl.Step(name=f"✍️ Generate ({model_name})", type="llm") as step:
        step.input = f"Query: {message.content}\n\nContext: {len(results)} documents"
        t0 = time.perf_counter()
        answer = pipeline.generate_answer(message.content, context_text)
        duration = (time.perf_counter() - t0) * 1000
        step.output = f"{answer}\n\n⏱️ {duration:.0f}ms"

    # Final response
    await cl.Message(
        content=answer,
        author=model_name,
    ).send()
