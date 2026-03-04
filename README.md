# Azure OpenAI Models Migration Guide

Complete guide for migrating from GPT-4o/GPT-4o-mini to newer Azure OpenAI models, with **evaluation tools** to validate quality before deploying.

> **⚠️ Retirement dates and model availability change frequently.**
> Always verify against the **[official Azure OpenAI Model Retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)** for the latest authoritative information.
> This guide was last updated **February 2026**.

> **Scope:** This guide focuses on **text generation models** (GPT series and o-series). For audio, image, and embedding models, see the [official retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements).

> [!TIP]
> **Looking for a visual evaluation tool?** The [`model_migration_eval/`](model_migration_eval/) subfolder contains a full-featured **web UI** for comparing models side-by-side. Run it with `cd model_migration_eval && pip install -r requirements.txt && python app.py`.

## Migration at a Glance

1. **[Pick your target model](docs/migration-paths.md)** — choose based on your priorities (cost, quality, reasoning)
2. **[Check the timeline](docs/retirement-timeline.md)** — know when your current model retires
3. **[Update your code](docs/api-changes-by-model.md)** — switch client, adapt parameters, adjust prompts
4. **[Evaluate before deploying](docs/evaluation-guide.md)** — compare quality with automated metrics
5. **Roll out progressively** — canary first, then full traffic ([Lifecycle Best Practices](docs/llm-upgrade-lifecycle-best-practices.md))

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

## Repository Structure

```
├── docs/                                     # Deep-dive guides (see table above)
├── azure_openai_migration_technical.ipynb    # Interactive technical migration notebook
├── azure_openai_evaluation_guide.ipynb       # Interactive evaluation demo notebook
├── src/                                      # Reusable Python modules
│   ├── config.py                             #   Model helpers (is_v1, is_reasoning, is_o_series)
│   ├── clients.py                            #   Client factory, call_model() with auto-adaptation
│   └── evaluate/                             #   Evaluation framework (scenarios, prompts, Foundry)
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
