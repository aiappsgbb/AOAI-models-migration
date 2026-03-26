# RAG Pipeline — Multi-Step Migration Sample

A self-contained Retrieval-Augmented Generation pipeline designed to demonstrate
**how to evaluate and migrate multi-model applications**.

Most enterprise AI apps are not single model calls — they chain multiple models
together (embedding → retrieval → generation). When you migrate one model in the
chain, you need to know:

1. **Did overall quality change?** (end-to-end evaluation)
2. **WHERE did it break?** (task-level evaluation)

This sample implements both evaluation layers — the **hybrid methodology**
described in [`docs/migrating-multi-step-apps.md`](../../docs/migrating-multi-step-apps.md).

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Rephrase   │────▶│    Embed     │────▶│   Retrieve   │────▶│   Generate   │
│  (optional)  │     │              │     │   (top-k)    │     │              │
│  gpt-4o-mini │     │ text-embed-  │     │   cosine     │     │   gpt-4o     │
│              │     │ 3-large      │     │  similarity  │     │              │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
     LLM call            API call           numpy (local)          LLM call

     ▲ Swap to                                                    ▲ Swap to
     gpt-4.1-mini                                                 gpt-4.1
```

Each step is isolated — you can swap individual models without touching the rest.

## Quick Start

### 1. Setup

```bash
# From the repo root
pip install -r requirements.txt
cp .env_example .env
# Edit .env with your Azure OpenAI endpoint and deployments
```

### 2. Run the Pipeline

```python
from samples.rag_pipeline.pipeline import RAGPipeline, PipelineConfig
from samples.rag_pipeline.knowledge_base import KnowledgeBase
from src.clients import create_client

# Load knowledge base
kb = KnowledgeBase.from_json("samples/rag_pipeline/data/documents.json")

# Embed documents (one-time, uses embedding model)
client = create_client("text-embedding-3-large")
kb.embed_documents(client)

# Create pipeline with current models
config = PipelineConfig(generator_model="gpt-4o")
pipeline = RAGPipeline(kb, config)

# Run a query
result = pipeline.run("What is the remote work policy?")
print(result.answer)
print(f"Retrieved: {result.retrieved_ids}")
print(f"Total time: {result.total_ms:.0f}ms")
```

### 3. Evaluate (Dual-Layer)

```python
from samples.rag_pipeline.evaluate_pipeline import evaluate_dual_layer

report = evaluate_dual_layer(
    pipeline=pipeline,
    golden_path="samples/rag_pipeline/data/golden_tests.jsonl",
    kb=kb,
    judge_model="gpt-4o",
)
report.print_summary()
```

This runs both layers:
- **End-to-end**: Full pipeline → LLM-as-judge scoring (groundedness, relevance, correctness)
- **Task-level retrieval**: Precision@k, Recall@k vs expected documents (no LLM cost)
- **Task-level generation**: Correct context → answer quality (isolates generation from retrieval)

### 4. Compare Models (A/B Migration)

```python
from samples.rag_pipeline.migrate_and_compare import compare_from_golden

config_a = PipelineConfig(generator_model="gpt-4o")
config_b = PipelineConfig(generator_model="gpt-4.1")

report = compare_from_golden(
    kb=kb,
    config_a=config_a,
    config_b=config_b,
    golden_path="samples/rag_pipeline/data/golden_tests.jsonl",
)
report.print_report()
```

Output shows:
- **Retrieval stability**: Did the same documents get retrieved?
- **Answer changes**: How many answers changed?
- **Timing comparison**: Per-step latency (rephrase, embed, retrieve, generate)
- **Verdict**: Clear summary of what changed

## Files

| File | Purpose |
|------|---------|
| `pipeline.py` | RAG pipeline class with 4 swappable steps |
| `knowledge_base.py` | In-memory vector store (numpy cosine similarity) |
| `evaluate_pipeline.py` | Dual-layer evaluation (end-to-end + task-level) |
| `migrate_and_compare.py` | A/B migration comparison script |
| `data/documents.json` | 20 enterprise IT policy documents |
| `data/golden_tests.jsonl` | 15 golden test cases with expected results |

## Knowledge Base

20 documents across 5 categories:

| Category | Count | Topics |
|----------|-------|--------|
| Security | 5 | Password policy, MFA, data classification, incident response, acceptable use |
| HR | 4 | Remote work, expenses, leave, onboarding |
| Infrastructure | 4 | VPN, cloud provisioning, disaster recovery, SLAs |
| Data Governance | 4 | Retention, GDPR, backups, access requests |
| Development | 3 | Code review, CI/CD, API versioning |

## Evaluation Methodology

### When to Use Which Layer

| Scenario | End-to-End | Task-Level |
|----------|-----------|------------|
| Routine model update | ✅ Primary | Only if regression found |
| Critical use case | ✅ Required | ✅ Required |
| Debugging regression | Initial signal | ✅ Root cause |
| New model family (4o→5.1) | ✅ Required | ✅ Recommended |

### Cost per Evaluation Cycle

For 15 golden test cases:

| Component | API Calls | Cost |
|-----------|-----------|------|
| End-to-end (2 configs × 15 cases) | 30 pipeline runs | ~$1.00 |
| Retrieval scoring | 0 (computation only) | $0.00 |
| Generation scoring (isolated) | 30 generation calls | ~$0.50 |
| LLM-as-judge | 30 judgments | ~$0.50 |
| **Total** | | **~$2.00** |

## Migration Workflow

```
1. Baseline: Run eval with current models → save scores
2. Swap ONE model (e.g., generator: gpt-4o → gpt-4.1)
3. Run A/B comparison → check retrieval stability + timing
4. Run dual-layer eval → check quality scores
5. If regression: task-level eval reveals WHERE
6. If OK: lock in change, move to next model
7. All models migrated: final regression suite
```

## See Also

- [Migrating Multi-Step Applications](../../docs/migrating-multi-step-apps.md) — Full methodology guide
- [Building Golden Datasets](../../docs/building-golden-datasets.md) — How to create test sets
- [Evaluation Guide](../../docs/evaluation-guide.md) — LLM-as-judge and Foundry evaluation
