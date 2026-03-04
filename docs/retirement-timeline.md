# Retirement Timeline

> **⚠️ Retirement dates and model availability change frequently.** Always verify against the **[official Azure OpenAI Model Retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)** for the latest authoritative information.
>
> This page was last updated **February 2026**.

---

## Models Being Retired

### GPT-4o

| Deployment Type | GPT-4o (2024-05-13, 2024-08-06) | GPT-4o (2024-11-20) |
|----------------|----------------------------------|---------------------|
| **Standard** | **2026-03-31** (auto-upgrade starts 2026-03-09) | 2026-10-01 |
| **Provisioned** | 2026-10-01 | 2026-10-01 |
| **Global** | 2026-10-01 | 2026-10-01 |
| **DataZone** | 2026-10-01 | 2026-10-01 |

> **⏰ Urgent:** Standard GPT-4o (05-13, 08-06) auto-upgrade to GPT-5.1 begins **2026-03-09** — less than a month away. If you haven't tested against GPT-5.1 yet, start now. See the [Evaluation Guide](evaluation-guide.md) for how to validate quality.

### GPT-4o-mini

| Deployment Type | GPT-4o-mini |
|----------------|-------------|
| **Standard** | **2026-03-31** (auto-upgrade starts 2026-03-09) |
| **Provisioned** | 2026-10-01 |
| **Global** | 2026-10-01 |
| **DataZone** | 2026-10-01 |

> **Auto-migration target:** GPT-4o-mini Standard → **GPT-4.1-mini**.

### o1 and o3-mini

| Model | Retirement Date | Replacement |
|-------|----------------|-------------|
| `o1` (2024-12-17) | **2026-07-15** | `o3` |
| `o3-mini` (2025-01-31) | **2026-08-02** | `o4-mini` |

---

## Current Models — Retirement Dates

These are the models you should be migrating **to**. Their retirement dates give you a planning horizon.

| Model | GA Date | Retirement (not before) | Successor |
|-------|---------|------------------------|-----------|
| `gpt-4.1` | 2025-04-14 | 2026-10-14 | `gpt-5` |
| `gpt-4.1-mini` | 2025-04-14 | 2026-10-14 | `gpt-5-mini` |
| `gpt-4.1-nano` | 2025-04-14 | 2026-10-14 | `gpt-5-nano` |
| `o3` | 2025-04-16 | 2026-10-16 | — |
| `o4-mini` | 2025-04-16 | 2026-10-16 | — |
| `gpt-5` | 2025-07-17 | 2027-01-17 | — |
| `gpt-5-mini` | 2025-08-07 | 2027-02-06 | — |
| `gpt-5-nano` | 2025-08-07 | 2027-02-06 | — |
| `gpt-5.1` | 2025-11-13 | 2027-05-15 | — |
| `gpt-5.2` | 2025-12-11 | ~2027-05-12 | — |
| `model-router` | 2025-11-18 | 2027-05-20 | — |

> **Tip:** Models with later retirement dates give you more runway. GPT-5.1 and GPT-5.2 won't retire until mid-2027 at the earliest.

---

## How Azure OpenAI Retirements Work

### Deployment Types and Auto-Upgrade Behavior

| Deployment Type | What Happens at Retirement |
|----------------|---------------------------|
| **Standard** | Auto-upgraded to the designated replacement model on the upgrade date. No action required, but behavior may change. |
| **Provisioned** | Must be manually redeployed before retirement date. Provisioned throughput units (PTUs) are released. |
| **Global** | Auto-upgraded. Same behavior as Standard but routed across regions. |
| **DataZone** | Auto-upgraded. Same behavior as Standard but with data residency guarantees. |

### Key Concepts

- **"Retirement (not before)"** — Microsoft guarantees the model will be available until at least this date. The actual retirement may be later.
- **Auto-upgrade** — For Standard/Global/DataZone deployments, Microsoft automatically switches your deployment to the replacement model. Your endpoint URL stays the same, but the model behind it changes.
- **No-longer-available (NLA)** — After retirement, the model cannot be deployed or re-deployed. Existing deployments stop working.

### What You Should Do

1. **Inventory your deployments** — Know which models and deployment types you're using. See the [Lifecycle Best Practices guide](llm-upgrade-lifecycle-best-practices.md) for how to automate this with Azure CLI/SDK.
2. **Test before the auto-upgrade date** — Run your evaluation suite against the replacement model. See the [Evaluation Guide](evaluation-guide.md).
3. **For Provisioned deployments** — You must manually create a new deployment with the target model and migrate traffic. Auto-upgrade does not apply.
4. **Set up notifications** — Use Azure Service Health alerts to get notified of upcoming retirements. See the [Lifecycle Best Practices guide](llm-upgrade-lifecycle-best-practices.md#2-monitor-notifications).

---

## Planning Your Migration

| If you're on... | Urgency | Recommended action |
|----------------|---------|-------------------|
| GPT-4o Standard (05-13, 08-06) | 🔴 **Urgent** — auto-upgrade 2026-03-09 | Test GPT-5.1 or GPT-4.1 now. See [Migration Paths](migration-paths.md). |
| GPT-4o-mini Standard | 🔴 **Urgent** — auto-upgrade 2026-03-09 | Test GPT-4.1-mini now. |
| GPT-4o Provisioned | 🟡 **Plan by Q3 2026** | Create new deployment with target model before 2026-10-01. |
| GPT-4o (11-20) any | 🟡 **Plan by Q3 2026** | Retirement 2026-10-01. |
| o1 | 🟡 **Plan by Q2 2026** | Migrate to o3 before 2026-07-15. |
| o3-mini | 🟡 **Plan by Q2 2026** | Migrate to o4-mini before 2026-08-02. |
| GPT-4.1 family | 🟢 **No rush** | Retirement not before 2026-10-14. Start planning for GPT-5 family. |

---

## Official Sources

- **[Azure OpenAI Model Retirements](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements)** — authoritative retirement dates (always check this)
- **[Azure OpenAI Models Overview](https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models)** — capabilities and regional availability
- **[What's New in Azure OpenAI](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/whats-new)** — latest changes and announcements

---

## Next Steps

- **[Migration Paths](migration-paths.md)** — choose your target model
- **[API Changes](api-changes-by-model.md)** — code-level changes needed
- **[Evaluation Guide](evaluation-guide.md)** — validate quality before deploying
