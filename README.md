# Azure OpenAI Models Migration Guide

Complete guide for migrating from GPT-4o/GPT-4o-mini to newer Azure OpenAI models (GPT-4.1, GPT-5.1, o-series), with **evaluation tools** and **ready-to-use golden datasets** to validate quality before deploying.

> **⚠️ Retirement dates and model availability change frequently.**
> Always verify against the **[official Azure OpenAI Model Retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)** for the latest authoritative information.
> This guide was last updated **March 2026**.

> **Scope:** This guide focuses on **text generation models** (GPT series and o-series). For audio, image, and embedding models, see the [official retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements).

> [!TIP]
> **Looking for a visual evaluation tool?** The [`model_migration_eval/`](model_migration_eval/) subfolder contains a full-featured **web UI** for comparing models side-by-side. Run it with `cd model_migration_eval && pip install -r requirements.txt && python app.py`.

## Migration at a Glance

1. **[Pick your target model](docs/migration-paths.md)** — choose based on your priorities (cost, quality, reasoning)
2. **[Check the timeline](docs/retirement-timeline.md)** — know when your current model retires
3. **[Update your code](docs/api-changes-by-model.md)** — switch client, adapt parameters, adjust prompts
4. **[Build a golden dataset](docs/building-golden-datasets.md)** — from stored completions, APIM logs, or use our [pre-built test data](data/)
5. **[Evaluate before deploying](docs/evaluation-guide.md)** — compare quality with automated metrics
6. **Roll out progressively** — canary first, then full traffic ([Lifecycle Best Practices](docs/llm-upgrade-lifecycle-best-practices.md))

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/aiappsgbb/AOAI-models-migration.git
cd AOAI-models-migration
pip install -r requirements.txt

# 2. Configure (copy .env_example → .env and fill in your values)
cp .env_example .env
```

```python
# 3. Run an A/B evaluation with the pre-built golden dataset
from src.evaluate.core import MigrationEvaluator

evaluator = MigrationEvaluator(
    source_model="gpt-4o",
    target_model="gpt-4.1",
    test_cases="data/golden_rag.jsonl",  # 10 RAG test cases included
    metrics=["coherence", "fluency", "relevance", "groundedness"],
)
report = evaluator.run()
report.print_report()
```

> **📓 Prefer notebooks?** Open [`azure_openai_migration_technical.ipynb`](azure_openai_migration_technical.ipynb) for an interactive walkthrough of code changes, or [`azure_openai_evaluation_guide.ipynb`](azure_openai_evaluation_guide.ipynb) for evaluation scenarios.

## Guides

| Guide | Description |
|-------|-------------|
| 📖 **[Migration Paths](docs/migration-paths.md)** | Which model to migrate to, decision matrix, standard vs reasoning trade-offs |
| 📅 **[Retirement Timeline](docs/retirement-timeline.md)** | All retirement dates, auto-upgrade behavior, urgency planning |
| 🔧 **[API Changes by Model](docs/api-changes-by-model.md)** | Client config, parameter tables, reasoning effort, structured outputs, C#/JS/Java SDKs |
| 🧪 **[Evaluation Guide](docs/evaluation-guide.md)** | Pre-built scenarios (RAG, tool calling, translation, classification), LLM-as-Judge, SDK eval |
| 📊 **[Building Golden Datasets](docs/building-golden-datasets.md)** | How to build eval test data from production logs, AI gateways, agent traces, and synthetic generation |
| ☁️ **[Cloud Eval Tracking](docs/cloud-eval-tracking-across-models.md)** | Reusable eval definitions in Azure AI Foundry, cross-model comparison, CI/CD |
| 🔄 **[Lifecycle Best Practices](docs/llm-upgrade-lifecycle-best-practices.md)** | Deployment inventory, notifications, rollout strategies, fine-tuned models, multi-region |
| 🚀 **[Getting Started](docs/getting-started.md)** | Prerequisites, setup, authentication, quick start |
| 🔗 **[Migrating Multi-Step Apps](docs/migrating-multi-step-apps.md)** | Hybrid evaluation methodology for RAG pipelines and agent workflows |

> **📊 Prefer slides?** A companion slide deck is available separately (not included in this repo). It provides an executive-level overview useful for stakeholder meetings and team onboarding — contact the repo maintainers for access.

## Sample: RAG Pipeline Migration

The [`samples/rag_pipeline/`](samples/rag_pipeline/) directory contains a **self-contained RAG application** that demonstrates how to evaluate and migrate multi-model pipelines:

```
Query → [Rephrase (LLM)] → [Embed] → [Retrieve (vector search)] → [Generate (LLM)] → Answer
```

- **20 knowledge base documents** + **15 golden test cases**
- **Dual-layer evaluation**: end-to-end quality + task-level retrieval/generation scoring
- **A/B migration comparison**: swap one model, see per-step regression analysis
- **Zero infra dependencies**: in-memory vector store with numpy

See the [RAG Pipeline README](samples/rag_pipeline/README.md) for a full walkthrough.

## Repository Structure

```
├── docs/                                     # Deep-dive guides (see table above)
├── data/                                     # Golden test datasets (54 cases, 7 scenarios)
│   ├── golden_rag.jsonl                      #   RAG / grounded Q&A (10 cases)
│   ├── golden_classification.jsonl           #   Intent & sentiment classification (10 cases)
│   ├── golden_tool_calling.jsonl             #   Function calling & tool selection (8 cases)
│   ├── golden_translation.jsonl              #   EN→IT/DE/ES translation (6 cases)
│   ├── golden_summarization.jsonl            #   Meeting notes, emails, incidents (6 cases)
│   ├── golden_agent.jsonl                    #   Multi-step agent reasoning (8 cases)
│   └── golden_multiturn.jsonl                #   Multi-turn conversation context (6 cases)
├── azure_openai_migration_technical.ipynb    # Interactive technical migration notebook
├── azure_openai_evaluation_guide.ipynb       # Interactive evaluation demo notebook
├── src/                                      # Reusable Python modules
│   ├── config.py                             #   Model helpers (is_v1, is_reasoning, is_o_series)
│   ├── clients.py                            #   Client factory, call_model() with auto-adaptation
│   └── evaluate/                             #   Evaluation framework
│       ├── core.py                           #     MigrationEvaluator, TestCase, ComparisonReport
│       ├── local_eval.py                     #     Local SDK evaluation (quick_evaluate, compare_local)
│       ├── foundry.py                        #     Azure AI Foundry cloud evaluation
│       ├── scenarios/                        #     Pre-built test scenarios (RAG, tools, etc.)
│       └── prompts/                          #     .prompty templates for LLM-as-Judge
├── model_migration_eval/                     # Web UI for visual model comparison
├── samples/                                  # Sample applications
│   └── rag_pipeline/                         #   Multi-step RAG pipeline migration demo
│       ├── pipeline.py                       #     RAG pipeline with 4 swappable steps
│       ├── knowledge_base.py                 #     In-memory vector store (numpy)
│       ├── evaluate_pipeline.py              #     Dual-layer evaluation (E2E + task-level)
│       ├── migrate_and_compare.py            #     A/B migration comparison
│       └── data/                             #     20 KB docs + 15 golden test cases
├── .github/skills/                           # GitHub Copilot Skills (see below)
├── requirements.txt
└── .env_example
```

## GitHub Copilot Skills

This repo includes three **[GitHub Copilot Skills](https://docs.github.com/en/copilot/customizing-copilot/adding-custom-skills-for-copilot)** that provide contextual guidance automatically:

| Skill | What It Does |
|-------|--------------|
| **[aoai-model-migration](.github/skills/aoai-model-migration/SKILL.md)** | API changes, client configuration, parameter adaptation |
| **[aoai-migration-evaluation](.github/skills/aoai-migration-evaluation/SKILL.md)** | A/B testing, LLM-as-Judge, SDK & Foundry evaluation |
| **[aoai-model-lifecycle](.github/skills/aoai-model-lifecycle/SKILL.md)** | Retirement timelines, governance, operational checklists |

## Official Documentation

- **[Azure OpenAI Model Retirements](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)** — authoritative retirement dates
- **[Azure OpenAI Models Overview](https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models)** — capabilities and regional availability
- **[GPT-5 vs GPT-4.1](https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/gpt-5-vs-gpt-41)** — comparison guide
- **[Azure AI Foundry Evaluation](https://learn.microsoft.com/en-us/azure/ai-foundry/evaluation/)** — evaluation tools
- **[What's New in Azure OpenAI](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/whats-new)** — latest changes
- **[Responses API](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/responses)** — new API surface

## License

MIT
