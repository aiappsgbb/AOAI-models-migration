# Azure OpenAI Models Migration Guide

Complete guide for migrating from GPT-4o/GPT-4o-mini to newer Azure OpenAI models, with **evaluation tools** to validate quality before deploying.

> **⚠️ Retirement dates and model availability change frequently.**
> Always verify against the **[official Azure OpenAI Model Retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)** for the latest authoritative information.
> This guide was last updated **February 2026**.

> **Scope:** This guide focuses on **text generation models** (GPT series and o-series). For audio models (gpt-audio, gpt-realtime, Whisper), image models (gpt-image, DALL-E, Sora), and embedding models (text-embedding-3-*), see the [official retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements).

## GitHub Copilot Skills

This repo includes three **GitHub Copilot Skills** (`.github/skills/`) that provide contextual guidance when working in VS Code, GitHub.com, or the Copilot Coding Agent. Skills are automatically picked up by Copilot when they match your task.

| Skill | File | What It Does |
|-------|------|-------------|
| **aoai-model-migration** | [`.github/skills/aoai-model-migration/SKILL.md`](.github/skills/aoai-model-migration/SKILL.md) | Guides API changes, client configuration, parameter adaptation, and code patterns when migrating between Azure OpenAI model families. |
| **aoai-migration-evaluation** | [`.github/skills/aoai-migration-evaluation/SKILL.md`](.github/skills/aoai-migration-evaluation/SKILL.md) | Runs A/B model comparisons using built-in LLM-as-Judge, local SDK evaluation (`azure-ai-evaluation`), or Azure AI Foundry cloud evaluation. Covers RAG, tool calling, translation, and classification scenarios. |
| **aoai-model-lifecycle** | [`.github/skills/aoai-model-lifecycle/SKILL.md`](.github/skills/aoai-model-lifecycle/SKILL.md) | Plans and tracks model retirement timelines, deployment inventories, update policies, and the operational checklist for production migrations. |

**How to use:** Simply ask Copilot a relevant question (e.g., *"Migrate my GPT-4o code to GPT-4.1"* or *"Evaluate my model migration"*) and the matching skill will be used automatically. You can also reference a skill explicitly in Copilot Chat with `@workspace`.

## Migration Paths

### GPT Series

| Source Model | Target Model | Type | Use Case |
|--------------|--------------|------|----------|
| GPT-4o | **GPT-4.1** | Standard | Low-latency, high-throughput, drop-in replacement |
| GPT-4o | **GPT-5.1** | Reasoning | Official auto-migration target, built-in reasoning |
| GPT-4o | **GPT-5.2** | Reasoning | Latest GA model (Dec 2025), best overall quality |
| GPT-4o | **GPT-5** | Reasoning | Configurable thinking levels |
| GPT-4o-mini | **GPT-4.1-mini** | Standard | Official auto-migration target |
| GPT-4o-mini | **GPT-5-mini** | Reasoning | Alternative with reasoning (higher cost) |

### o-Series (Reasoning Models)

| Source Model | Target Model | Type | Use Case |
|--------------|--------------|------|----------|
| o1 | **o3** | Reasoning | Successor reasoning model |
| o3-mini | **o4-mini** | Reasoning | Faster, cheaper reasoning |
| o1-pro | **o3-pro** | Reasoning | Pro-tier reasoning |

### How to Choose?

| Priority | GPT-4o replacement | GPT-4o-mini replacement |
|----------|-------------------|------------------------|
| **Low latency / high throughput** | GPT-4.1 | GPT-4.1-mini |
| **Balanced (cost + quality)** | GPT-5.1 | GPT-4.1-mini |
| **Best overall quality** | GPT-5.2 | GPT-5-mini |
| **Best reasoning / agentic** | GPT-5 | GPT-5-mini |
| **Lowest cost** | GPT-4.1 | GPT-4.1-mini |
| **Smart model routing** | `model-router` | `model-router` |

> **💡 `model-router`** (GA Nov 2025) automatically routes requests to the best-suited model. Consider it as an alternative to choosing a fixed model.

## Retirement Timeline (Updated February 2026)

> **⚠️ These dates shift.** Always check the [official retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements) for the latest.

### GPT-4o / GPT-4o-mini (Retiring)

| Deployment Type | GPT-4o (05-13, 08-06) | GPT-4o (11-20) | GPT-4o-mini |
|----------------|------------------------|-----------------|-------------|
| **Standard** | 2026-03-31 (auto-upgrade starts 03-09) | 2026-10-01 | 2026-03-31 |
| **Provisioned / Global / DataZone** | 2026-10-01 | 2026-10-01 | 2026-10-01 |

### Current Models & Their Retirement Dates

| Model | Version | Retirement (not before) | Replacement |
|-------|---------|------------------------|-------------|
| `gpt-4.1` | 2025-04-14 | 2026-10-14 | `gpt-5` |
| `gpt-4.1-mini` | 2025-04-14 | 2026-10-14 | `gpt-5-mini` |
| `gpt-4.1-nano` | 2025-04-14 | 2026-10-14 | `gpt-5-nano` |
| `o1` | 2024-12-17 | 2026-07-15 | `o3` |
| `o3-mini` | 2025-01-31 | 2026-08-02 | `o4-mini` |
| `o3` | 2025-04-16 | 2026-10-16 | — |
| `o4-mini` | 2025-04-16 | 2026-10-16 | — |
| `gpt-5-mini` | 2025-08-07 | 2027-02-06 | — |
| `gpt-5-nano` | 2025-08-07 | 2027-02-06 | — |
| `gpt-5.1` | 2025-11-13 | 2027-05-15 | — |
| `gpt-5.2` | 2025-12-11 | ~2027-05-12 | — |
| `model-router` | 2025-11-18 | 2027-05-20 | — |

> Source: [Azure OpenAI Model Retirements](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)

## Repository Structure

```
.
├── .github/
│   └── skills/                              # GitHub Copilot Skills
│       ├── aoai-model-migration/SKILL.md    # API changes, client config, parameter adaptation
│       ├── aoai-migration-evaluation/SKILL.md # A/B testing, LLM-as-Judge, SDK & Foundry eval
│       └── aoai-model-lifecycle/SKILL.md    # Retirement timelines, governance, checklists
├── azure_openai_migration_technical.ipynb   # Technical migration guide (API changes, code)
├── azure_openai_evaluation_guide.ipynb      # Evaluation demo notebook
├── docs/
│   └── llm-upgrade-lifecycle-best-practices.md  # Full lifecycle best practices guide
├── src/                                      # Reusable Python modules
│   ├── __init__.py
│   ├── config.py                            # Model helpers (is_v1, is_reasoning, is_o_series), env loading
│   ├── clients.py                           # Client factory (AzureOpenAI vs OpenAI), call_model()
│   └── evaluate/                            # Evaluation framework
│       ├── __init__.py
│       ├── core.py                          # MigrationEvaluator, LLM-as-Judge, reports
│       ├── local_eval.py                    # Local SDK evaluation (azure-ai-evaluation)
│       ├── foundry.py                       # Azure AI Foundry cloud evaluation
│       ├── prompts/                         # System prompts in Prompty format (.prompty)
│       │   ├── __init__.py                  # load_prompty() / list_prompty() loader
│       │   ├── rag.prompty                  # RAG system prompt
│       │   ├── tool_calling.prompty         # Tool calling system prompt
│       │   ├── translate_*.prompty          # Translation prompts (fr_en, en_fr, en_de, technical)
│       │   └── classify_*.prompty           # Classification prompts (sentiment, category, intent, priority)
│       └── scenarios/                       # Pre-built evaluation scenarios
│           ├── __init__.py
│           ├── rag.py                       # RAG: groundedness, relevance, coherence
│           ├── tool_calling.py              # Tool calling: accuracy, parameters
│           ├── translation.py              # Translation: fluency, semantic equivalence
│           └── classification.py           # Classification: accuracy, consistency
├── requirements.txt
├── .env_example
└── README.md
```

## Key API Changes

### Client Configuration

**Before (GPT-4o — versioned API):**
```python
from openai import AzureOpenAI

client = AzureOpenAI(
    azure_ad_token_provider=token_provider,
    api_version="2024-02-15-preview",
    azure_endpoint=AZURE_OPENAI_ENDPOINT
)
```

**After (GPT-4.1 / GPT-5 series — v1 API):**
```python
from openai import OpenAI

client = OpenAI(
    api_key=token_provider(),
    base_url=f"{AZURE_OPENAI_ENDPOINT}/openai/v1/"
)
```

### Parameter Changes

| Parameter | GPT-4o | GPT-4.1 | GPT-5 / GPT-5.1 / GPT-5.2 | o-series (o1, o3, o4-mini) |
|-----------|--------|---------|---------------------------|---------------------------|
| `max_tokens` | Supported | Use `max_completion_tokens` | Use `max_completion_tokens` | Use `max_completion_tokens` |
| `temperature` | Supported | Supported | **Not supported** | **Not supported** |
| `top_p` | Supported | Supported | **Not supported** | **Not supported** |
| `reasoning_effort` | N/A | N/A | See table below | Supported |
| System role | `system` | `system` | `developer` | `developer` |

### Reasoning Effort by Model

> **Important:** `reasoning_effort="none"` is only supported from GPT-5.1 onwards. GPT-5, GPT-5-mini, and GPT-5-nano do **not** support `"none"` — their minimum is `"minimal"`, which still incurs reasoning tokens and added latency. This is a key consideration when migrating from a non-reasoning model like GPT-4o.

| Model | Type | `reasoning_effort` levels | Default |
|-------|------|--------------------------|---------|
| GPT-4.1 / 4.1-mini / 4.1-nano | Standard | N/A (no reasoning) | — |
| GPT-5 / 5-mini / 5-nano | Reasoning | `minimal`, `low`, `medium`, `high` | `medium` |
| GPT-5.1 | Reasoning | `none`, `low`, `medium`, `high` | `none` |
| GPT-5.2 | Reasoning | `none`, `low`, `medium`, `high` | `none` |
| o-series (o1, o3, o4-mini) | Reasoning | `low`, `medium`, `high` | `medium` |

## Structured Outputs & Responses API

### Structured Outputs

If your application uses `response_format` for JSON output, be aware of model differences:

| Feature | GPT-4o | GPT-4.1 | GPT-5+ |
|---------|--------|---------|--------|
| `{ "type": "json_object" }` | Supported | Supported | Supported |
| `{ "type": "json_schema", ... }` | Supported (2024-08-06+) | Supported | Supported |
| Strict mode | Supported | Supported | Supported |

Test your JSON schemas against the new model — while the API is compatible, different models may interpret schema constraints differently.

### Responses API

Azure OpenAI now supports the **Responses API** alongside Chat Completions. The Responses API is the recommended path forward for new development, offering built-in tool use, file search, and web search. Existing Chat Completions code continues to work. See the [Responses API docs](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/responses) for details.

## Other SDKs (C#, JavaScript, Java)

This repo provides Python examples, but the same migration concepts apply to all Azure OpenAI SDKs:

| Language | Package | v1 API Support |
|----------|---------|----------------|
| **C# / .NET** | `Azure.AI.OpenAI` + `OpenAI` | Use `OpenAIClient` with `/openai/v1/` base URL |
| **JavaScript / TypeScript** | `openai` (npm) | Use `OpenAI` with `baseURL` pointing to `/openai/v1/` |
| **Java** | `com.azure:azure-ai-openai` | Use the OpenAI-compatible client |

> See [Azure OpenAI SDKs](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/supported-languages) for SDK-specific documentation.

## Evaluation

The evaluation framework helps you **detect regressions** before deploying a new model in production. It works by running the same prompts through both models and comparing the outputs.

### Pre-built Scenarios

Each scenario includes sample test data and adapted metrics — ready to run:

| Scenario | Metrics | Test Cases |
|----------|---------|------------|
| **RAG** | Groundedness, Relevance, Coherence | 8 examples (company docs, financial, legal) |
| **Tool Calling** | Tool Accuracy, Parameter Accuracy | 8 examples (weather, calendar, email, DB) |
| **Translation** | Fluency, Coherence, Relevance | 10 examples (FR/EN/DE, business/technical/legal) |
| **Classification** | Accuracy, Consistency, Relevance | 16 examples (sentiment, tickets, intent, priority) |

### Quick Start

```python
from src.evaluate.scenarios import create_rag_evaluator

# Compare GPT-4o vs GPT-4.1 on RAG tasks
evaluator = create_rag_evaluator(
    source_model="gpt-4o",
    target_model="gpt-4.1",
)
report = evaluator.run()
report.print_report()
```

### Cloud Evaluation (Azure AI Foundry)

```python
from src.evaluate.foundry import FoundryEvaluator

foundry = FoundryEvaluator(project_endpoint="https://your-project.services.ai.azure.com/...")
results = foundry.evaluate_cloud(report, evaluators=["coherence", "fluency", "relevance"])
```

See [azure_openai_evaluation_guide.ipynb](azure_openai_evaluation_guide.ipynb) for the full walkthrough.

## Prerequisites

1. **Azure OpenAI Resource** with access to target models
2. **Azure CLI** installed and authenticated:
   ```bash
   az login
   ```
3. **Python packages**:
   ```bash
   pip install -r requirements.txt
   ```

## Authentication

This guide uses **Microsoft Entra ID** authentication (recommended):

```python
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(),
    "https://cognitiveservices.azure.com/.default"
)
```

## Usage

1. Copy `.env_example` to `.env` and fill in your values
2. Run `az login` to authenticate
3. Start with the **technical guide** notebook for API migration
4. Use the **evaluation guide** notebook to validate quality
5. Check the **pricing notebook** for cost analysis

## Official Documentation

> **📌 Bookmark these** — they are updated by Microsoft as models and dates change:

- **[Azure OpenAI Model Retirements](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)** — authoritative retirement dates
- **[Azure OpenAI Models Overview](https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models)** — model capabilities and regional availability
- **[GPT-5 vs GPT-4.1: Choosing the Right Model](https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/gpt-5-vs-gpt-41)** — comparison guide
- **[Azure AI Foundry Evaluation](https://learn.microsoft.com/en-us/azure/ai-foundry/evaluation/)** — evaluation tools
- **[What's New in Azure OpenAI](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/whats-new)** — latest changes and features
- **[Azure OpenAI Responses API](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/responses)** — new API surface
- **[Azure OpenAI SDKs](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/supported-languages)** — all supported languages

## License

MIT
