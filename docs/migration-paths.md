# Migration Paths — Choosing Your Target Model

> **⚠️ Retirement dates and model availability change frequently.** Always verify against the **[official Azure OpenAI Model Retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)**.
>
> **Last verified: July 2026.**

This guide helps you pick the right target model when migrating from GPT-4o, GPT-4o-mini, or o-series models.

---

## GPT Series

| Source Model | Target Model | Type | Use Case |
|--------------|--------------|------|----------|
| GPT-4o | **GPT-4.1** | Standard | Low-latency, high-throughput, drop-in replacement |
| GPT-4o | **GPT-5.1** | Reasoning | Official auto-migration target, built-in reasoning |
| GPT-4o / GPT-4.1 | **GPT-5.5** | Reasoning | Current GA candidate; validate quality, latency, capacity, and cost |
| GPT-4o / GPT-4.1 | **GPT-5.6 series** | Reasoning | Newest GA candidate family; validate the Sol, Terra, and Luna variants |
| GPT-4o-mini | **GPT-4.1-mini** | Standard | Official auto-migration target |
| GPT-4o-mini / GPT-4.1-mini | **Current GPT-5 candidates** | Reasoning | No designated one-to-one replacement; evaluate against your workload |

### Standard vs Reasoning: What's the Difference?

- **Standard models** (GPT-4.1 family) behave like GPT-4o — they generate a response directly. They support `temperature`, `top_p`, and the `system` role.
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
| **GPT-5.1** | 1M tokens | GPT-4o Standard auto-migration target; supports `reasoning_effort=none` |
| **GPT-5.2** | 1M tokens | GA model retiring 2026-12-12 |
| **GPT-5.3-codex** | 1M tokens | Responses-API-only model optimized for coding workflows |
| **GPT-5.4** | 1M tokens | GA reasoning model retiring 2027-03-05 |
| **GPT-5.4-pro** | 1M tokens | Pro-tier model retiring 2027-03-06 |
| **GPT-5.4-mini** | 1M tokens | Smaller model candidate retiring 2027-03-18 |
| **GPT-5.4-nano** | 1M tokens | Lightweight model candidate retiring 2027-03-18 |
| **GPT-5.5** | 1.05M tokens | Current GA model (Apr 2026) |
| **GPT-5.6-sol / terra / luna** | 1.05M tokens | Current GA model family (Jul 2026) |

**Key differences from GPT-4.1:**
- Built-in reasoning (internal chain-of-thought)
- No `temperature`/`top_p` support
- Uses `developer` role instead of `system`
- `reasoning_effort` controls thinking depth (and cost/latency)
- GPT-5.1 and GPT-5.2 support `reasoning_effort="none"` — effectively disabling reasoning to match GPT-4.1 behavior at comparable latency

> **💡 GPT-5.1 with `reasoning_effort="none"`** is Microsoft's official auto-migration target for affected GPT-4o Standard deployments. Evaluate its workload-specific quality, latency, and token usage rather than assuming equivalence.

### Pre-Upgrade Checklist: GPT-4o Standard → GPT-5.1

If you have **Standard GPT-4o deployments**, Azure has auto-upgraded them to GPT-5.1 (completed March 31, 2026). Use this checklist to verify your code works correctly:

- [ ] **Client type:** GPT-5.1 uses the v1 API. Switch from `AzureOpenAI` to `OpenAI` with `base_url`. See [API Changes](api-changes-by-model.md).
- [ ] **`max_tokens` → `max_completion_tokens`:** The parameter name changed for v1 models.
- [ ] **`system` role → `developer` role:** GPT-5.1 is a reasoning model and uses the `developer` role.
- [ ] **Remove `temperature` / `top_p`:** Reasoning models don't support these. Use `reasoning_effort` instead.
- [ ] **`reasoning_effort`:** Set to `"none"` for GPT-4o-equivalent behavior (no reasoning overhead), or `"medium"` for balanced reasoning.
- [ ] **Structured outputs:** GPT-5.1 supports `response_format: { type: "json_schema", ... }` — same as GPT-4o.
- [ ] **Tool calling:** Fully supported, same schema. Test tool parameter accuracy as models may make different decisions.
- [ ] **Run evaluation:** Use the [Evaluation Guide](evaluation-guide.md) to validate GPT-5.1 on your data and remediate any post-upgrade regression.

> **💡 Quick path:** For behavior closest to a non-reasoning model, start GPT-5.1 evaluation with `reasoning_effort="none"`, then measure the actual latency, quality, and token usage.

---

## o-Series (Reasoning Models)

| Source Model | Target Model | Type | Use Case |
|--------------|--------------|------|----------|
| o1 | **Current GA GPT-5 reasoning models** | Reasoning | No designated replacement; evaluate before the 2026-09-16 retirement |
| o3-mini | **Current GA GPT-5 reasoning models** | Reasoning | No designated replacement; `o4-mini` is already deprecated |
| o1-pro | **Current GA GPT-5 reasoning models** | Reasoning | No designated replacement; evaluate before the 2026-09-18 retirement |

The o-series models are dedicated reasoning models with `reasoning_effort` support (`low`, `medium`, `high`). They use the `developer` role and `max_completion_tokens` (not `max_tokens`). See the [API Changes deep dive](api-changes-by-model.md) for parameter details.

---

## How to Choose?

### Quick Decision Matrix

| Evaluation goal | Candidates to compare |
|-----------------|-----------------------|
| **Preserve completed Standard auto-migration behavior** | GPT-5.1 |
| **Current GA general-purpose reasoning** | GPT-5.5, GPT-5.6 series |
| **Smaller GPT-5 deployments** | GPT-5.4-mini, GPT-5.4-nano |
| **Dynamic routing across workloads** | `model-router` |

> **💡 Evaluation-first sizing:** Smaller models can be valuable candidates, but do not assume equivalent quality, cost, latency, or lifecycle. GPT-5.4-mini and GPT-5.4-nano retire on 2027-03-18—not September 2027. Compare them with current GPT-5.5 and GPT-5.6 candidates using your golden dataset. See [API Changes](api-changes-by-model.md) for model-specific differences.

### Decision Tree

1. **Do you need reasoning (chain-of-thought)?**
   - **No** → Start with a model/version that supports `reasoning_effort="none"`, then measure whether it meets your workload targets.
   - **Yes** → Continue below.

2. **How much reasoning do you need?**
   - **Light reasoning, mostly standard use** → Compare smaller GPT-5 candidates with reasoning disabled or minimized.
   - **Heavy reasoning / agentic workflows** → Compare GPT-5.5 and the GPT-5.6 variants with workload-specific reasoning settings.
   - **Strict quality or latency target** → Benchmark multiple current GA candidates; do not select from model age alone.

3. **Are you currently on o-series?**
   - **o1 / o1-pro / o3-mini** → No designated replacement is listed. Evaluate current GA GPT-5 reasoning models; `o4-mini` is deprecated.

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
| o-series → GPT-5.x | `OpenAI` v1 client remains | Re-evaluate supported reasoning settings and remove unsupported sampling parameters | Revalidate role handling and prompts |

---

## Next Steps

- **[Retirement Timeline](retirement-timeline.md)** — know when your current model retires
- **[API Changes](api-changes-by-model.md)** — code-level changes needed for each model family
- **[Evaluation Guide](evaluation-guide.md)** — validate quality before deploying
- **[Getting Started](getting-started.md)** — set up the repo and run your first migration
