# Key API Changes by Model Family

> **⚠️ Retirement dates and model availability change frequently.** Always verify against the **[official Azure OpenAI Model Retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)**.
>
> **Last verified: July 2026.**

This document covers the technical API differences you need to handle when migrating between Azure OpenAI model families. For migration paths and timelines, see the [README](../README.md).

## Client Configuration

Current GPT-4.1+, GPT-5+, and o-series integrations use the **v1 API**, which uses the `OpenAI` client instead of `AzureOpenAI`.

**Before (GPT-4o — versioned API):**
```python
from openai import AzureOpenAI

client = AzureOpenAI(
    azure_ad_token_provider=token_provider,
    api_version="2024-02-15-preview",
    azure_endpoint=AZURE_OPENAI_ENDPOINT
)
```

**After (GPT-4.1, GPT-5, and o-series — v1 API):**
```python
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(),
    "https://ai.azure.com/.default",
)

client = OpenAI(
    api_key=token_provider,
    base_url=f"{AZURE_OPENAI_ENDPOINT}/openai/v1/"
)
```

> **💡 Tip:** Pass the token-provider callable itself, not `token_provider()`. The OpenAI client invokes it when needed, so Entra ID tokens refresh without overriding private SDK methods. The repo's `src/clients.py` applies this pattern automatically.

## Parameter Changes

| Parameter | GPT-4o | GPT-4.1 | GPT-5 / GPT-5.x | o-series (o1, o3, o4-mini) |
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
| GPT-5.2 / 5.3-codex / 5.4 / 5.4-pro | Reasoning | `none`, `low`, `medium`, `high` | `none` |
| GPT-5.4-mini / 5.4-nano | Reasoning | `none`, `low`, `medium`, `high` | `none` |
| GPT-5.5 / GPT-5.6 series / `gpt-chat-latest` | Reasoning | Verify supported levels for the deployed model version | Verify model documentation |
| o-series (o1, o3, o4-mini) | Reasoning | `low`, `medium`, `high` | `medium` |

> **💡 What if you don't set `reasoning_effort` at all?**
>
> - **GPT-5.1 and GPT-5.2** default to `"none"` — reasoning is completely disabled. The model behaves like a standard (non-reasoning) model: no internal chain-of-thought, no reasoning tokens, same cost profile as GPT-4.1. This makes GPT-5.1 a safe drop-in replacement for GPT-4o without unexpected cost or latency increases.
> - **GPT-5, GPT-5-mini, GPT-5-nano** default to `"medium"` — reasoning **is active by default**, which means higher latency and additional reasoning token costs compared to GPT-4o. If you're migrating from GPT-4o and want equivalent behavior, explicitly set `reasoning_effort="minimal"` (or consider GPT-5.1 with `"none"` instead).
> - **o-series (o3, o4-mini)** default to `"medium"` — these are purpose-built reasoning models; reasoning is always active at some level.

## Model Family Classification

Use these helpers (from `src/config.py`) to determine which API and parameters a model requires:

```python
from src import is_v1, is_reasoning, is_o_series, uses_developer_role

is_v1("gpt-4.1")           # True — uses OpenAI client with /openai/v1/
is_v1("o3")                # True — current v1 API path
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

> **⚠️ Important:** Codex models such as `gpt-5.1-codex`, `gpt-5.2-codex`, and `gpt-5.3-codex` are Responses-API-only. The repo's `call_model()` helper uses Chat Completions and raises a clear error for these models; call `client.responses.create()` instead.

## Other SDKs (C#, JavaScript, Java)

This repo provides Python examples, but the same migration concepts apply to all Azure OpenAI SDKs:

| Language | Package | v1 API Support |
|----------|---------|----------------|
| **C# / .NET** | `Azure.AI.OpenAI` + `OpenAI` | Use `OpenAIClient` with `/openai/v1/` base URL |
| **JavaScript / TypeScript** | `openai` (npm) | Use `OpenAI` with `baseURL` pointing to `/openai/v1/` |
| **Java** | `com.azure:azure-ai-openai` | Use the OpenAI-compatible client |

> See [Azure OpenAI SDKs](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/supported-languages) for SDK-specific documentation.
