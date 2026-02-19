---
name: aoai-model-migration
description: "Migrate Azure OpenAI applications from GPT-4o/GPT-4o-mini to newer models (GPT-4.1, GPT-5, GPT-5.1). Covers API changes, client configuration, parameter adaptation, prompt adjustments, and authentication."
---

# Azure OpenAI Model Migration Skill

## Purpose

Guide developers through migrating Azure OpenAI applications from GPT-4o / GPT-4o-mini to newer model families (GPT-4.1, GPT-5, GPT-5.1). This skill covers API surface changes, client configuration, parameter adaptation, and prompt adjustments.

## When to Use

- Migrating from GPT-4o or GPT-4o-mini to any newer Azure OpenAI model
- Adapting code to the new v1 API (`/openai/v1/`) used by GPT-4.1+ and GPT-5+
- Adapting parameters and system prompts for reasoning models (GPT-5, GPT-5.1)
- Choosing the right replacement model for a given workload

## Migration Paths

| Source Model | Target Model | Type | Best For |
|---|---|---|---|
| GPT-4o | **GPT-4.1** | Standard | Low-latency, high-throughput, drop-in replacement, lowest cost |
| GPT-4o | **GPT-5.1** | Reasoning | Official auto-migration target, built-in reasoning, `reasoning_effort=none` supported |
| GPT-4o | **GPT-5** | Reasoning | Best reasoning and agentic capability |
| GPT-4o-mini | **GPT-4.1-mini** | Standard | Official auto-migration target, lowest cost |
| GPT-4o-mini | **GPT-5-mini** | Reasoning | Alternative with reasoning (higher cost) |

### How to Choose

| Priority | GPT-4o replacement | GPT-4o-mini replacement |
|---|---|---|
| **Low latency / high throughput** | GPT-4.1 | GPT-4.1-mini |
| **Balanced (cost + quality)** | GPT-5.1 | GPT-4.1-mini |
| **Best reasoning / agentic** | GPT-5 | GPT-5-mini |
| **Lowest cost** | GPT-4.1 | GPT-4.1-mini |

## Key API Changes

### 1. Client Configuration

GPT-4.1+ and GPT-5+ use the **v1 API**, which requires the `OpenAI` client instead of `AzureOpenAI`.

**Before (GPT-4o — versioned API):**

```python
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(),
    "https://cognitiveservices.azure.com/.default"
)

client = AzureOpenAI(
    azure_ad_token_provider=token_provider,
    api_version="2024-12-01-preview",
    azure_endpoint=AZURE_OPENAI_ENDPOINT
)
```

**After (GPT-4.1 / GPT-5 — v1 API):**

```python
from openai import OpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(),
    "https://cognitiveservices.azure.com/.default"
)

client = OpenAI(
    api_key=token_provider(),
    base_url=f"{AZURE_OPENAI_ENDPOINT}/openai/v1/"
)
```

### 2. Model Family Classification

Use these sets to determine which API and parameters a model requires:

```python
# Models using the v1 API (OpenAI client with /openai/v1/ endpoint)
V1_MODELS = {
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "gpt-5", "gpt-5.1", "gpt-5-mini", "gpt-5-nano",
}

# Reasoning models (no temperature/top_p, use max_completion_tokens)
REASONING_MODELS = {
    "gpt-5", "gpt-5.1", "gpt-5-mini", "gpt-5-nano",
}
```

### 3. Parameter Adaptation

| Parameter | GPT-4o | GPT-4.1 | GPT-5 / GPT-5.1 |
|---|---|---|---|
| `max_tokens` | Supported | Use `max_completion_tokens` | Use `max_completion_tokens` |
| `temperature` | Supported | Supported | **Not supported** (remove it) |
| `top_p` | Supported | Supported | **Not supported** (remove it) |
| `reasoning_effort` | N/A | N/A | See below |
| System role | `"system"` | `"system"` | `"developer"` |

**Parameter adaptation pattern:**

```python
def adapt_params(model_name: str, params: dict) -> dict:
    """Adapt parameters for the target model."""
    adapted = params.copy()

    # max_tokens → max_completion_tokens for v1 models
    if model_name in V1_MODELS and "max_tokens" in adapted:
        adapted["max_completion_tokens"] = adapted.pop("max_tokens")

    # Reasoning models don't support temperature/top_p
    if model_name in REASONING_MODELS:
        adapted.pop("temperature", None)
        adapted.pop("top_p", None)

    return adapted
```

### 4. Reasoning Effort

| Model | Type | `reasoning_effort` levels | Default |
|---|---|---|---|
| GPT-4.1 / 4.1-mini / 4.1-nano | Standard | N/A (no reasoning) | — |
| GPT-5 / 5-mini / 5-nano | Reasoning | `minimal`, `low`, `medium`, `high` | `medium` |
| GPT-5.1 | Reasoning | `none`, `low`, `medium`, `high` | `none` |

> **Important:** `reasoning_effort="none"` is only supported on GPT-5.1. GPT-5, GPT-5-mini, and GPT-5-nano minimum is `"minimal"`, which still incurs reasoning tokens and added latency.

### 5. System Role for Reasoning Models

GPT-5/GPT-5.1 use `"developer"` instead of `"system"` for the system message role:

```python
# GPT-4o / GPT-4.1
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": query},
]

# GPT-5 / GPT-5.1
messages = [
    {"role": "developer", "content": "You are a helpful assistant."},
    {"role": "user", "content": query},
]
```

### 6. Client Factory Pattern

Use a factory function to create the right client for any model:

```python
from openai import AzureOpenAI, OpenAI

def create_client(model_name: str, endpoint: str, api_key: str = None) -> AzureOpenAI | OpenAI:
    """Create the appropriate client for a given model."""
    if model_name in V1_MODELS:
        base_url = endpoint.rstrip("/") + "/openai/v1"
        return OpenAI(base_url=base_url, api_key=api_key or token_provider())
    else:
        return AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=token_provider,
            api_version="2024-12-01-preview",
        )
```

## Repository Resources

This repo provides reusable modules under `src/`:

- `src/config.py` — Model family helpers (`is_v1()`, `is_reasoning()`), environment config
- `src/clients.py` — Client factory (`create_client()`), parameter-adapting `call_model()`
- `src/evaluate/` — Full evaluation framework for comparing models (see `aoai-migration-evaluation` skill)

## Steps for a Migration

1. **Identify your target model** using the migration paths table above.
2. **Update client initialization** — switch from `AzureOpenAI` to `OpenAI` for v1 models.
3. **Adapt parameters** — replace `max_tokens` with `max_completion_tokens`, remove `temperature`/`top_p` for reasoning models.
4. **Update system message role** — use `"developer"` for GPT-5/GPT-5.1.
5. **Set `reasoning_effort`** if using a reasoning model (start with `"low"` for cost-sensitive workloads).
6. **Run evaluations** to validate the new model matches or exceeds the old model's quality (see `aoai-migration-evaluation` skill).
7. **Deploy progressively** — canary rollout for high-traffic workloads.

## Must Not

- Hard-code model names deep in application code. Use config/environment variables.
- Use `temperature` or `top_p` with reasoning models (GPT-5, GPT-5.1) — they are not supported.
- Use `max_tokens` with v1 API models — use `max_completion_tokens` instead.
- Skip evaluation before deploying a new model in production.
- Assume `reasoning_effort="none"` works on GPT-5/GPT-5-mini — only GPT-5.1 supports it.
- Use `AzureOpenAI` client with v1 models — use `OpenAI` client with `base_url` pointing to `/openai/v1/`.

## References

- [Azure OpenAI Model Retirements](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements)
- [Azure OpenAI Models Overview](https://learn.microsoft.com/azure/ai-services/openai/concepts/models)
- [GPT-5 vs GPT-4.1: Choosing the Right Model](https://learn.microsoft.com/azure/ai-services/openai/concepts/gpt-5-vs-gpt-41)
