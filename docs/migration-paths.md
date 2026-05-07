# Migration Paths — Choosing Your Target Model

> **⚠️ Retirement dates and model availability change frequently.** Always verify against the **[official Azure OpenAI Model Retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)**.

This guide helps you pick the right target model when migrating from GPT-4o, GPT-4o-mini, or o-series models.

---

## GPT Series

| Source Model | Target Model | Type | Use Case |
|--------------|--------------|------|----------|
| GPT-4o | **GPT-4.1** | Standard | Low-latency, high-throughput, drop-in replacement |
| GPT-4o | **GPT-5.1** | Reasoning | Official auto-migration target, built-in reasoning |
| GPT-4o | **GPT-5.5** | Reasoning | Latest GA model (Apr 2026), highest quality |
| GPT-4o | **GPT-5.4** | Reasoning | GA model (Mar 2026), excellent quality |
| GPT-4o | **GPT-5.2** | Reasoning | Proven GA model (Dec 2025), excellent quality |
| GPT-4o | **GPT-5** | Reasoning | Configurable thinking levels |
| GPT-4o-mini | **GPT-4.1-mini** | Standard | Official auto-migration target |
| GPT-4o-mini | **GPT-5-mini** | Reasoning | Alternative with reasoning (higher cost) |

### Standard vs Reasoning: What's the Difference?

- **Standard models** (GPT-4.1 family) behave like GPT-4o — they generate a response directly. They support `temperature`, `top_p`, and the `system` role. They are the cheapest and lowest-latency option.
- **Reasoning models** (GPT-5 family, o-series) "think before answering". They produce internal reasoning tokens before the final response. They do **not** support `temperature`/`top_p`, they use the `developer` role instead of `system`, and they add a `reasoning_effort` parameter. See the [API Changes deep dive](api-changes-by-model.md) for full details.

### GPT-4.1 Family — Deprecated

> **⚠️ Important:** As of April 14, 2026, GPT-4.1 family is **deprecated** — no new customers can create deployments. Existing deployments work until retirement (2026-10-14). If you're starting a new project, target GPT-5 family directly.

| Model | Context | Best For |
|-------|---------|----------|
| **GPT-4.1** | 1M tokens | General-purpose, coding, instruction following — drop-in GPT-4o replacement |
| **GPT-4.1-mini** | 1M tokens | Cost-efficient tasks, high throughput — drop-in GPT-4o-mini replacement |
| **GPT-4.1-nano** | 1M tokens | Ultra-low-cost classification, extraction, routing |

**Key strengths over GPT-4o:**
- 1M token context (vs 128K)
- Better instruction following and coding
- Lower cost per token
- Same API surface — easiest migration path

### GPT-5 Family

| Model | Context | Best For |
|-------|---------|----------|
| **GPT-5** | 1M tokens | Complex reasoning, agentic workflows, configurable thinking |
| **GPT-5-mini** | 1M tokens | Balanced reasoning at lower cost |
| **GPT-5-nano** | 1M tokens | Lightweight reasoning tasks |
| **GPT-5.1** | 1M tokens | GPT-4o official replacement; `reasoning_effort=none` for zero-overhead mode |
| **GPT-5.2** | 1M tokens | Proven model (Dec 2025); excellent quality |
| **GPT-5.3-codex** | 1M tokens | Optimized for code generation and editing |
| **GPT-5.5** | 1M tokens | Latest GA (Apr 2026); newest generation, best overall quality |
| **GPT-5.4** | 1M tokens | GA (Mar 2026); excellent quality |
| **GPT-5.4-pro** | 1M tokens | Premium tier; highest quality for complex tasks |
| **GPT-5.4-mini** | 1M tokens | Cost-efficient alternative to GPT-5.4 |
| **GPT-5.4-nano** | 1M tokens | Ultra-low-cost lightweight reasoning |

**Key differences from GPT-4.1:**
- Built-in reasoning (internal chain-of-thought)
- No `temperature`/`top_p` support
- Uses `developer` role instead of `system`
- `reasoning_effort` controls thinking depth (and cost/latency)
- GPT-5.1 and GPT-5.2 support `reasoning_effort="none"` — effectively disabling reasoning to match GPT-4.1 behavior at comparable latency

> **💡 GPT-5.1 with `reasoning_effort="none"`** is Microsoft's official auto-migration target for GPT-4o Standard deployments. When reasoning is set to "none", the model behaves like a standard model with no extra latency or reasoning token costs.

### Pre-Upgrade Checklist: GPT-4o Standard → GPT-5.1

If you have **Standard GPT-4o deployments**, Azure has auto-upgraded them to GPT-5.1 (completed March 31, 2026). Use this checklist to verify your code works correctly:

- [ ] **Client type:** GPT-5.1 uses the v1 API. Switch from `AzureOpenAI` to `OpenAI` with `base_url`. See [API Changes](api-changes-by-model.md).
- [ ] **`max_tokens` → `max_completion_tokens`:** The parameter name changed for v1 models.
- [ ] **`system` role → `developer` role:** GPT-5.1 is a reasoning model and uses the `developer` role.
- [ ] **Remove `temperature` / `top_p`:** Reasoning models don't support these. Use `reasoning_effort` instead.
- [ ] **`reasoning_effort`:** Set to `"none"` for GPT-4o-equivalent behavior (no reasoning overhead), or `"medium"` for balanced reasoning.
- [ ] **Structured outputs:** GPT-5.1 supports `response_format: { type: "json_schema", ... }` — same as GPT-4o.
- [ ] **Tool calling:** Fully supported, same schema. Test tool parameter accuracy as models may make different decisions.
- [ ] **Run evaluation:** Use the [Evaluation Guide](evaluation-guide.md) to compare GPT-4o vs GPT-5.1 on your data before the auto-upgrade completes.

> **💡 Quick path:** If you just want GPT-4o-equivalent behavior from GPT-5.1, set `reasoning_effort="none"`. This disables reasoning tokens entirely — same latency and cost profile as a standard model.

---

## o-Series (Reasoning Models)

| Source Model | Target Model | Type | Use Case |
|--------------|--------------|------|----------|
| o1 | **o3** | Reasoning | Successor reasoning model |
| o3-mini | **o4-mini** | Reasoning | Faster, cheaper reasoning |
| o1-pro | **o3-pro** | Reasoning | Pro-tier reasoning |

The o-series models are dedicated reasoning models with `reasoning_effort` support (`low`, `medium`, `high`). They use the `developer` role and `max_completion_tokens` (not `max_tokens`). See the [API Changes deep dive](api-changes-by-model.md) for parameter details.

---

## How to Choose?

### Quick Decision Matrix

| Priority | GPT-4o replacement | GPT-4o-mini replacement |
|----------|-------------------|------------------------|
| **Best quality/latency tradeoff** | GPT-5.4-mini | GPT-5.4-nano |
| **Best overall quality** | GPT-5.5 | GPT-5.4-mini |
| **Best reasoning / agentic** | GPT-5.5 | GPT-5.4-mini |
| **Lowest cost** | GPT-5.4-nano | GPT-5.4-nano |
| **Smart model routing** | `model-router` | `model-router` |

> **💡 Tier-down strategy:** Newer-generation smaller models often match or exceed the quality of older-generation larger ones — with better latency and lower cost. If you're planning a **manual migration**, consider dropping a tier:
>
> | Migrating from… | Target… | Why |
> |-----------------|---------|-----|
> | GPT-4o / GPT-4.1 | **GPT-5.4-mini** | Comparable quality to GPT-4.1 at lower cost and latency, with 12-month runway (retires Mar 2027) |
> | GPT-4o-mini / GPT-4.1-mini | **GPT-5.4-nano** | Comparable quality to GPT-4.1-mini at a fraction of the cost, with 12-month runway (retires Mar 2027) |
>
> This gives you a better quality-to-latency tradeoff *and* avoids a second migration when GPT-5/5.1 retire in early 2027. The API surface is identical across all GPT-5.x models — the only difference is `reasoning_effort` defaults. See [API Changes](api-changes-by-model.md) for details.

### Decision Tree

1. **Do you need reasoning (chain-of-thought)?**
   - **No** → Use **GPT-5.4-mini** (replaces GPT-4o/4.1 at lower cost) or **GPT-5.4-nano** (replaces GPT-4o-mini/4.1-mini). Set `reasoning_effort="none"` for standard-model behavior.
   - **Yes** → Continue below.

2. **How much reasoning do you need?**
   - **Light reasoning, mostly standard use** → **GPT-5.4-mini** with `reasoning_effort="none"` by default, bump to `"medium"` or `"high"` for specific prompts.
   - **Heavy reasoning / agentic workflows** → **GPT-5.4** with `reasoning_effort="high"`.
   - **Best quality, cost is secondary** → **GPT-5.5** or **GPT-5.4-pro**.

3. **Are you currently on o-series?**
   - **o1** → Migrate to **o3**.
   - **o3-mini** → Migrate to **o4-mini**.

### Model Router

> **💡 `model-router`** (GA Nov 2025) automatically routes requests to the best-suited model based on prompt complexity. Consider it when:
> - You have diverse workloads (some simple, some complex)
> - You want automatic cost optimization
> - You don't want to pick a single fixed model

---

## What Changes When You Migrate?

Depending on your target model, you'll need to update your code. Here's a summary — see the **[API Changes deep dive](api-changes-by-model.md)** for full details and code examples.

| Migration | Client Change | Parameter Changes | Prompt Changes |
|-----------|--------------|-------------------|----------------|
| GPT-4o → GPT-4.1 | `AzureOpenAI` → `OpenAI` (v1 API) | `max_tokens` → `max_completion_tokens` | None |
| GPT-4o → GPT-5.x | `AzureOpenAI` → `OpenAI` (v1 API) | Drop `temperature`/`top_p`, use `max_completion_tokens`, add `reasoning_effort` | `system` → `developer` role |
| GPT-4o → o-series | `AzureOpenAI` → `OpenAI` (v1 API) | Drop `temperature`/`top_p`, use `max_completion_tokens`, add `reasoning_effort` | `system` → `developer` role |
| o1 → o3 | No change (already v1) | Minimal | None |
| o1-pro → o3-pro | No change (already v1) | Minimal | None |

---

## Next Steps

- **[Retirement Timeline](retirement-timeline.md)** — know when your current model retires
- **[API Changes](api-changes-by-model.md)** — code-level changes needed for each model family
- **[Evaluation Guide](evaluation-guide.md)** — validate quality before deploying
- **[Getting Started](getting-started.md)** — set up the repo and run your first migration
