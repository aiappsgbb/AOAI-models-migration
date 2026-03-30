# RAG Pipeline — Multi-Step Migration Sample

A self-contained Retrieval-Augmented Generation pipeline designed to demonstrate
**how to evaluate and migrate multi-model applications**.

> **This is a methodology demo, not a production RAG system.** The in-memory
> vector store and sample documents are intentionally simple so you can focus
> on the evaluation patterns. Replace them with your own retrieval backend
> (Azure AI Search, pgvector, Pinecone, etc.) and your own documents — the
> evaluation framework, golden test format, and scoring methodology all
> transfer directly.

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

### 2. Launch Chat UI

Interactive chat interface with live model swapping:

```bash
cd samples/rag_pipeline
chainlit run app.py
```

Open http://localhost:8000 — ask questions, then change the model via ⚙️ **Settings**.
Each pipeline step (Rephrase → Embed → Retrieve → Generate) is visible as an
expandable step with timing. **Swap models live — zero code changes.**

### 3. Run End-to-End Test

The fastest way to validate a migration — change `.env`, run the same script:

```bash
# Edit .env to set your models:
#   RAG_SOURCE_MODEL=gpt-4o
#   RAG_TARGET_MODEL=gpt-4.1

python samples/rag_pipeline/test_e2e.py
```

This runs 20 tests × 2 models with full dual-layer evaluation, per-category
breakdown, and exports JSON results. **Change only `.env` to test different
model pairs — zero code changes.**

### 4. Swap Models — Config Only

```bash
# .env — just change these lines:
RAG_SOURCE_MODEL=gpt-4.1
RAG_TARGET_MODEL=gpt-5.4

# Run the exact same script:
python samples/rag_pipeline/test_e2e.py
# → Compares gpt-4.1 vs gpt-5.4 with the same golden tests
```

### 5. Compare Models (A/B Migration)

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
| `app.py` | Chainlit chat UI — interactive demo with live model swap |
| `pipeline.py` | RAG pipeline class with 4 swappable steps |
| `knowledge_base.py` | In-memory vector store (numpy cosine similarity) |
| `evaluate_pipeline.py` | Dual-layer evaluation (end-to-end + task-level + per-category) |
| `migrate_and_compare.py` | A/B migration comparison script |
| `test_e2e.py` | Full E2E test — .env-driven, JSON export |
| `drift_analysis.py` | Temporal drift analysis across historical result files |
| `upload_to_foundry.py` | Upload results to Azure AI Foundry dashboard |
| `data/documents.json` | 20 enterprise IT policy documents |
| `data/golden_tests.jsonl` | 20 golden test cases (incl. 5 adversarial) with expected results |
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

## Golden Test Cases

20 test cases across 7 difficulty categories:

| Category | Count | Tests |
|----------|-------|-------|
| Simple lookup | 5 | Single-doc factual retrieval |
| Multi-doc | 4 | Requires combining info from 2-3 documents |
| Cross-category | 2 | Spans policy domains (e.g., security + remote work) |
| Specific detail | 2 | Precise numerical answers (RTO, mileage rate) |
| Negation | 2 | Should answer "not enough information" |
| Adversarial — misleading | 2 | Questions with wrong assumptions the model must correct |
| Adversarial — nuance | 1 | Requires distinguishing overlapping policies |
| Adversarial — contradiction | 1 | Asks about seemingly conflicting policies |
| Adversarial — multi-hop | 1 | Requires reasoning across 4 documents |

The adversarial cases are designed to break naive retrieval and generation — they
test whether a model change introduces subtle reasoning failures, not just factual recall.

## Evaluation Methodology

> **Why LLM-as-judge instead of similarity scoring?** Similarity-based evaluators (e.g., cosine similarity between old and new responses, or BLEU/ROUGE scores) only tell you if the output *changed* — not whether it *improved or regressed*. Two answers can be textually different but equally correct, or textually similar but one subtly wrong. LLM-as-judge evaluates **semantic correctness, groundedness, and relevance** — the qualities that actually matter to end users. This is especially critical for model migrations where the new model may express the same answer differently.

> **⚠️ Judge model independence:** The judge model **must be different** from both the source and target models being compared — otherwise the judge is biased toward its own outputs. By default, `RAG_JUDGE_MODEL` is set to `gpt-4o`. If you're comparing `gpt-4o` vs another model, change the judge to a different model (e.g., `gpt-4.1`).

### When to Use Which Layer

| Scenario | End-to-End | Task-Level |
|----------|-----------|------------|
| Routine model update | ✅ Primary | Only if regression found |
| Critical use case | ✅ Required | ✅ Required |
| Debugging regression | Initial signal | ✅ Root cause |
| New model family (4o→5.1) | ✅ Required | ✅ Recommended |

### API Calls per Evaluation Cycle

The number of API calls scales linearly with the number of golden test cases and the evaluation layers you enable. For a typical run with ~20 test cases, expect a few hundred API calls total — covering pipeline runs for both models, LLM-as-judge scoring, and isolated generation evaluation.

The dataset is reusable across every migration cycle — you pay the API cost once per evaluation run, not per migration.

> **🔑 Designed for repeatability:** The entire evaluation — data preparation, dual-layer scoring, A/B comparison, and JSON audit export — runs as a single script invocation (`python test_e2e.py`). Swap models by changing `.env`, re-run the same command, compare. Golden datasets are write-once, reuse-forever: the same test cases work for every migration cycle (gpt-4o → gpt-4.1, then gpt-4.1 → gpt-5.1, etc.) without rebuilding anything. This keeps the evaluation effort proportional to the number of *use cases you have*, not the number of *model changes you make*.

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

## Drift Analysis Over Time

After multiple evaluation runs (e.g., testing gpt-4o → gpt-4.1, then gpt-4o → gpt-5.4-mini),
the `data/results/` folder accumulates JSON audit trails. The drift analysis script reads
all of them and identifies trends:

```bash
python samples/rag_pipeline/drift_analysis.py
```

Output includes:
- **Timeline**: All evaluation runs with aggregate scores
- **Per-model trend**: Which metrics improved, regressed, or stayed stable
- **Per-category cluster analysis**: Identifies if specific topic categories (e.g., Security, HR) regressed while others improved
- **Retrieval stability**: Whether the same documents are being retrieved across model versions

This maps directly to the **drift detection and cluster analysis** pattern: instead of
one-off comparisons, you track quality trends across every migration cycle.

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

## Use Your Own Data

This sample comes with pre-built test data, but it's designed to work with **your** application's data:

```bash
# Point to your own golden tests and knowledge base:
python samples/rag_pipeline/test_e2e.py \
    --golden-path /path/to/your/golden_tests.jsonl \
    --docs-path /path/to/your/documents.json \
    --results-dir /path/to/your/results/
```

Golden test JSONL format (one per line):
```json
{"query": "your question", "expected_doc_ids": ["doc-1"], "expected_answer": "reference answer", "category": "your-category"}
```

See [Building Golden Datasets](../../docs/building-golden-datasets.md) for how to mine production traffic into this format.

## See Also

- [Migrating Multi-Step Applications](../../docs/migrating-multi-step-apps.md) — Full methodology guide
- [Building Golden Datasets](../../docs/building-golden-datasets.md) — How to create test sets + sizing guide
- [Evaluation Guide](../../docs/evaluation-guide.md) — LLM-as-judge and Foundry evaluation
- [CI/CD Automation](../../.github/workflows/eval-on-schedule.yml) — Sample GitHub Actions for nightly eval
