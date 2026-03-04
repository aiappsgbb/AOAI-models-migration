# Key API Changes by Model Family

> **⚠️ Retirement dates and model availability change frequently.** Always verify against the **[official Azure OpenAI Model Retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)**.

This document covers the technical API differences you need to handle when migrating between Azure OpenAI model families. For migration paths and timelines, see the [README](../README.md).

## Client Configuration

GPT-4.1+ and GPT-5+ use the **v1 API**, which requires the `OpenAI` client instead of `AzureOpenAI`.

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

> **Tip:** The repo's `src/clients.py` provides a `create_client()` factory that picks the right client automatically based on model name, and a `_TokenRefreshingOpenAI` wrapper that refreshes Entra ID tokens on every request.

## Parameter Changes

| Parameter | GPT-4o | GPT-4.1 | GPT-5 / GPT-5.1 / GPT-5.2 | o-series (o1, o3, o4-mini) |
|-----------|--------|---------|---------------------------|---------------------------|
| `max_tokens` | Supported | Use `max_completion_tokens` | Use `max_completion_tokens` | Use `max_completion_tokens` |
| `temperature` | Supported | Supported | **Not supported** | **Not supported** |
| `top_p` | Supported | Supported | **Not supported** | **Not supported** |
| `reasoning_effort` | N/A | N/A | See table below | Supported |
| System role | `system` | `system` | `developer` | `developer` |

**Parameter adaptation pattern:**

```python
from src.config import is_v1, is_reasoning, uses_developer_role

# max_tokens → max_completion_tokens for v1 models
if is_v1(model_name) and "max_tokens" in params:
    params["max_completion_tokens"] = params.pop("max_tokens")

# Reasoning models don't support temperature/top_p
if is_reasoning(model_name):
    params.pop("temperature", None)
    params.pop("top_p", None)

# Reasoning models use "developer" role instead of "system"
if uses_developer_role(model_name):
    messages = [
        {**m, "role": "developer"} if m.get("role") == "system" else m
        for m in messages
    ]
```

> **Tip:** The repo's `call_model()` in `src/clients.py` does all of this automatically.

## Reasoning Effort by Model

> **Important:** `reasoning_effort="none"` is only supported from GPT-5.1 onwards. GPT-5, GPT-5-mini, and GPT-5-nano do **not** support `"none"` — their minimum is `"minimal"`, which still incurs reasoning tokens and added latency. This is a key consideration when migrating from a non-reasoning model like GPT-4o.

| Model | Type | `reasoning_effort` levels | Default |
|-------|------|--------------------------|---------|
| GPT-4.1 / 4.1-mini / 4.1-nano | Standard | N/A (no reasoning) | — |
| GPT-5 / 5-mini / 5-nano | Reasoning | `minimal`, `low`, `medium`, `high` | `medium` |
| GPT-5.1 | Reasoning | `none`, `low`, `medium`, `high` | `none` |
| GPT-5.2 | Reasoning | `none`, `low`, `medium`, `high` | `none` |
| o-series (o1, o3, o4-mini) | Reasoning | `low`, `medium`, `high` | `medium` |

## Model Family Classification

Use these helpers (from `src/config.py`) to determine which API and parameters a model requires:

```python
from src import is_v1, is_reasoning, is_o_series, uses_developer_role

is_v1("gpt-4.1")           # True — uses OpenAI client with /openai/v1/
is_v1("gpt-4o")            # False — uses AzureOpenAI client

is_reasoning("gpt-5.1")    # True — no temperature/top_p, max_completion_tokens
is_reasoning("gpt-4.1")    # False — standard model

is_o_series("o3")           # True
uses_developer_role("gpt-5")  # True — "developer" instead of "system"
```

## Structured Outputs

If your application uses `response_format` for JSON output, be aware of model differences:

| Feature | GPT-4o | GPT-4.1 | GPT-5+ |
|---------|--------|---------|--------|
| `{ "type": "json_object" }` | Supported | Supported | Supported |
| `{ "type": "json_schema", ... }` | Supported (2024-08-06+) | Supported | Supported |
| Strict mode | Supported | Supported | Supported |

Test your JSON schemas against the new model — while the API is compatible, different models may interpret schema constraints differently.

## Responses API

Azure OpenAI now supports the **Responses API** alongside Chat Completions. The Responses API is the recommended path forward for new development, offering built-in tool use, file search, and web search. Existing Chat Completions code continues to work. See the [Responses API docs](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/responses) for details.

## Other SDKs (C#, JavaScript, Java)

This repo provides Python examples, but the same migration concepts apply to all Azure OpenAI SDKs:

| Language | Package | v1 API Support |
|----------|---------|----------------|
| **C# / .NET** | `Azure.AI.OpenAI` + `OpenAI` | Use `OpenAIClient` with `/openai/v1/` base URL |
| **JavaScript / TypeScript** | `openai` (npm) | Use `OpenAI` with `baseURL` pointing to `/openai/v1/` |
| **Java** | `com.azure:azure-ai-openai` | Use the OpenAI-compatible client |

> See [Azure OpenAI SDKs](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/supported-languages) for SDK-specific documentation.
