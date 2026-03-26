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
cp .env.template .env
# Edit .env with your Azure OpenAI endpoint
```

### 2. Run End-to-End Test

The fastest way to validate a migration — change `.env`, run the same script:

```bash
# Edit .env to set your models:
#   RAG_SOURCE_MODEL=gpt-4o
#   RAG_TARGET_MODEL=gpt-4.1

python samples/rag_pipeline/test_e2e.py
```

This runs 15 tests × 2 models with full dual-layer evaluation, per-category
breakdown, and exports JSON results. **Change only `.env` to test different
model pairs — zero code changes.**

### 3. Swap Models — Config Only

```bash
# .env — just change these lines:
RAG_SOURCE_MODEL=gpt-4.1
RAG_TARGET_MODEL=gpt-5.4

# Run the exact same script:
python samples/rag_pipeline/test_e2e.py
# → Compares gpt-4.1 vs gpt-5.4 with the same golden tests
```

### 4. Compare Models (A/B Migration)

```python
from samples.rag_pipeline.migrate_and_compare import compare_from_golden
from samples.rag_pipeline.pipeline import PipelineConfig

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
| `evaluate_pipeline.py` | Dual-layer evaluation (end-to-end + task-level + per-category) |
| `migrate_and_compare.py` | A/B migration comparison script |
| `test_e2e.py` | Full E2E test — .env-driven, JSON export |
| `upload_to_foundry.py` | Upload results to Azure AI Foundry dashboard |
| `data/documents.json` | 20 enterprise IT policy documents |
| `data/golden_tests.jsonl` | 15 golden test cases with expected results |
| `data/results/` | JSON audit trail (auto-generated, gitignored) |

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

## Upload Results to Azure AI Foundry

Push evaluation results to the Foundry portal for visual dashboards and
cross-run comparison. Uses `azure-ai-projects>=2.0.0` (new Foundry SDK).

### Setup

```bash
# Add to .env:
AZURE_AI_PROJECT_ENDPOINT=https://YOUR-ACCOUNT.services.ai.azure.com/api/projects/YOUR-PROJECT
FOUNDRY_JUDGE_DEPLOYMENT=gpt-4o
```

### Upload

```bash
# After running test_e2e.py (which exports JSON results):
python samples/rag_pipeline/upload_to_foundry.py
```

This will:
1. Read the latest JSON results from `data/results/`
2. Create a **named evaluation group** (e.g. "RAG Migration: gpt-4o → gpt-4.1")
3. Create **labeled runs** for each model config
4. Run Foundry built-in evaluators (coherence, groundedness, relevance)
5. Print the portal dashboard URL

Each run is named with the model config + timestamp, so you can compare
different migration scenarios side-by-side in the Foundry portal.

## See Also

- [Migrating Multi-Step Applications](../../docs/migrating-multi-step-apps.md) — Full methodology guide
- [Building Golden Datasets](../../docs/building-golden-datasets.md) — How to create test sets
- [Evaluation Guide](../../docs/evaluation-guide.md) — LLM-as-judge and Foundry evaluation
