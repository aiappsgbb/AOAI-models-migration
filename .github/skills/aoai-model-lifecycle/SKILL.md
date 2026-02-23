---
name: aoai-model-lifecycle
description: "Plan and manage the Azure OpenAI model upgrade lifecycle. Covers retirement timelines, deployment inventory, update policies, operational checklists, and mid-to-long term migration planning."
---

# Azure OpenAI Model Lifecycle Management Skill

> **⚠️ Retirement dates and model availability change frequently.** Always verify against the **[official Azure OpenAI Model Retirements page](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements)**.
> See also: **[What's New in Azure OpenAI](https://learn.microsoft.com/azure/ai-foundry/openai/whats-new)**.

## Purpose

Help teams plan, track, and execute Azure OpenAI model upgrades proactively. Covers retirement timelines, deployment governance, update policies, and the operational checklist to follow when a model retirement is announced.

## When to Use

- Checking current Azure OpenAI model retirement dates
- Planning a model migration timeline
- Setting up alerts and inventory for model retirements
- Choosing the right deployment update policy
- Running through the operational migration checklist
- Handling fine-tuned models, embedding models, or multi-region deployments

## Model Lifecycle Timeline

```
Model Launch (GA)
    │
    ├── 12 months ──► Deprecation (no new customers)
    │
    ├── up to 18 months ──► Retirement (existing customers)
    │                        └─ Standard deployments auto-upgrade ~3 weeks before retirement
    │                        └─ Provisioned / Global Standard may get extended timelines
    │
    └── Replacement model N+1 available for side-by-side comparison
         └─ Customers get 60 days to try new GA model before auto-upgrades begin
```

**Preview models** follow an accelerated timeline: 90-120 day "not sooner than" retirement with 30 days' notice.

## Current Retirement Dates (as of February 2026)

> These are "not sooner than" dates — they can be extended but not shortened.
> 📌 **Note:** ChatGPT (consumer) and Azure Foundry (enterprise) have **independent** retirement schedules.

### GPT Series

| Model | GA Version | Retirement (not before) | Replacement |
|---|---|---|---|
| `gpt-4o` | 2024-08-06 | 2026-03-31 (Standard) / 2026-10-01 (Provisioned) | `gpt-5.1` |
| `gpt-4o-mini` | 2024-07-18 | 2026-03-31 (Standard) / 2026-10-01 (Provisioned) | `gpt-4.1-mini` |
| `gpt-4.1` | 2025-04-14 | 2026-10-14 | `gpt-5` |
| `gpt-4.1-mini` | 2025-04-14 | 2026-10-14 | `gpt-5-mini` |
| `gpt-4.1-nano` | 2025-04-14 | 2026-10-14 | `gpt-5-nano` |
| `gpt-5` | 2025-08-07 | 2027-02-05 | — |
| `gpt-5-mini` | 2025-08-07 | 2027-02-06 | — |
| `gpt-5.1` | 2025-11-13 | 2027-05-15 | — |
| `gpt-5.2` | 2025-12-11 | ~2027-05-12 | — |

### o-Series (Reasoning)

| Model | GA Version | Retirement (not before) | Replacement |
|---|---|---|---|
| `o1` | 2024-12-17 | 2026-07-15 | `o3` |
| `o3-mini` | 2025-01-31 | 2026-08-02 | `o4-mini` |
| `o3` | 2025-04-16 | 2026-10-16 | — |
| `o4-mini` | 2025-04-16 | 2026-10-16 | — |

### Other

| Model | GA Version | Retirement (not before) | Notes |
|---|---|---|---|
| `model-router` | 2025-11-18 | 2027-05-20 | Auto-routes requests to optimal model |

> Always verify against the [official retirements page](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements).

### Deployment Type Timelines

| Deployment Type | GPT-4o (05-13, 08-06) | GPT-4o (11-20) | GPT-4o-mini |
|---|---|---|---|
| **Standard** | 2026-03-31 (auto-upgrade starts 03-09) | 2026-10-01 | 2026-03-31 |
| **Provisioned / Global / DataZone** | 2026-10-01 | 2026-10-01 | 2026-10-01 |

## Key Terminology

| Term | Meaning |
|---|---|
| **Deprecation** | Model no longer available to *new* customers. Existing deployments keep working. |
| **Retirement** | Model removed entirely. All deployments return errors. |
| **Auto-update to default** | Deployment auto-moves to latest default version when published. |
| **Upgrade when expired** | Deployment auto-upgrades only when its version reaches retirement. |
| **No Auto Upgrade** | Deployment **stops working** at retirement — manual migration required. |

## Deployment Update Policies

| Policy | Best For | Risk |
|---|---|---|
| **Auto-update to default** | Dev/test, non-critical workloads | May introduce unexpected behavior |
| **Upgrade when expired** | Production with moderate change tolerance | Forced upgrade at end of life |
| **No Auto Upgrade** | Regulated workloads with strict change control | **Deployment breaks if you miss the deadline** |

> **Recommendation for production:** Use "Upgrade when expired" as a safety net, but always plan to migrate proactively before retirement.

## Operational Checklist

Follow this when a model retirement is announced:

- [ ] **Verify notification** — confirm retirement date and replacement model from the [official page](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements)
- [ ] **Inventory impact** — identify all deployments using the retiring model across all subscriptions/regions
- [ ] **Deploy replacement** — create new deployment with the successor model in test environment
- [ ] **Run evaluations** — execute golden dataset against new model; compare results (use `aoai-migration-evaluation` skill)
- [ ] **Adjust prompts** — tune system prompts, parameters, and model-specific logic
- [ ] **Update API version** — if the new model requires a newer API version, test independently
- [ ] **Canary rollout** — route small percentage of production traffic to new model
- [ ] **Monitor** — watch error rates, latency, user feedback, quality metrics
- [ ] **Full rollout** — migrate remaining traffic
- [ ] **Clean up** — delete old deployments, update IaC templates, close migration ticket
- [ ] **Retrospective** — document lessons learned; update evaluation datasets

## Set Up Notifications

Azure notifies via **Azure Service Health** and **email to subscription owners**.

1. Go to **Azure Service Health > Health Alerts**
2. Service: `Azure OpenAI Service`
3. Event types: `Health advisories`
4. Create an **Action Group** with email + SMS for your on-call team

## Deployment Inventory

Maintain a live inventory of all deployments:

| Subscription | Resource Group | Resource | Deployment Name | Model | Version | Update Policy | Region |
|---|---|---|---|---|---|---|---|
| Prod-Sub-01 | rg-ai-prod | aoai-eastus | chat-prod | gpt-4o | 2024-08-06 | Upgrade when expired | East US |

Automate via the **Control Plane API** or **Azure Resource Graph** queries. Review quarterly.

**Azure Resource Graph query** (run in Azure Portal → Resource Graph Explorer or `az graph query`):

```kusto
resources
| where type == "microsoft.cognitiveservices/accounts/deployments"
| extend deployment = properties.name,
         model = properties.model.name,
         version = properties.model.version,
         updatePolicy = properties.versionUpgradeOption
| project subscriptionId, resourceGroup, name, deployment, model, version, updatePolicy, location
| order by model asc, version asc
```

## Production Migration Strategies

| Strategy | Description | Best For |
|---|---|---|
| **Blue-green** | Deploy new model in parallel, switch all traffic after validation | Low-traffic or batch workloads |
| **Canary / progressive rollout** | Route small % of traffic to new model; increase over days | High-traffic, latency-sensitive |
| **In-place migration** (Provisioned only) | Update existing deployment's model version; Azure migrates over 20-30 min | Provisioned deployments, keep same endpoint |

## Planning on a ~12-Month Cadence

Given the 12-month GA lifecycle, plan at least one major migration per year per model family:

| Quarter | Activity |
|---|---|
| **Q1** | Review inventory. Check retirement dates. Identify models in last 6 months. |
| **Q2** | Deploy successors in staging. Run evaluations. Update prompts. |
| **Q3** | Begin canary rollout. Monitor quality metrics. |
| **Q4** | Complete migration. Clean up. Retrospective. |

## Continuous Evaluation — Reducing Migration Costs

The single biggest cost driver in model migrations is **rebuilding evaluation infrastructure from scratch** each time. Continuous evaluation eliminates this by keeping evaluation running at all times, so baselines are always fresh and candidate comparisons are instant.

### How Continuous Eval Fits the Lifecycle

```
Model N in Production
       │
       ├── Continuous eval runs on schedule (weekly/daily)
       │   └── Baseline scores captured and trended
       │   └── Production traffic sampled into golden datasets
       │   └── Drift alerts if quality degrades
       │
       ├── Model N+1 Announced
       │   └── Deploy N+1 in staging
       │   └── Run SAME eval suite against N+1 (instant comparison)
       │   └── No need to rebuild test harness — it already exists
       │
       └── Migration Decision
           └── Compare N vs N+1 pass rates
           └── If pass → canary rollout
           └── If fail → tune prompts, re-evaluate
```

### Implementation Approaches

| Approach | SDK | Best For |
|---|---|---|
| **Cloud scheduled evaluation** | `azure-ai-projects>=2.0.0b1` (v2) | Production monitoring, agent evaluation, native Foundry integration |
| **CI/CD pipeline evaluation** | `azure-ai-evaluation` (v1) | PR gates, scheduled GitHub Actions/ADO pipelines, local iteration |
| **Both** | v1 + v2 | CI gates (v1) for speed + cloud monitoring (v2) for production |

### Key Practices

- **Sample production traffic** into your golden dataset monthly — keep it representative
- **Version your golden datasets** — tie each dataset version to a model generation
- **Set alerts** on pass-rate thresholds — don't wait for model retirement to discover issues
- **Reuse eval definitions** across model generations — the v2 eval/run separation makes this natural
- **Track eval scores over time** in Foundry portal — compare runs side by side across model versions

> See the `aoai-migration-evaluation` skill for detailed instructions on both v1 (local SDK) and v2 (cloud Evals API) approaches, including syntax differences and code examples.

## Special Considerations

### Fine-Tuned Models
- Follow a two-phase retirement: training retirement first, then deployment retirement (~1 year later)
- Plan to re-fine-tune on the successor base model before deployment retirement
- Budget time for data preparation, training, and evaluation

### Embedding Models
- Currently scheduled not to retire before **April 2027**
- When they retire, you must **re-embed your entire vector store** — embeddings from different models are not compatible
- Plan for compute/time cost of full re-indexing

### Multi-Region Deployments
- Model upgrades roll out region by region — no fixed region schedule
- Stagger manual migrations so you can fall back to a region still on the old model during validation

### API Versions (Independent Lifecycle)
- GA API versions get 12 months of support minimum
- Preview API versions can retire on shorter notice
- The v1 API (`/openai/v1/...`) eliminates monthly `api-version` strings — use `api-version=latest`
- The **Responses API** is now available alongside Chat Completions and is the recommended path for new development. See [Responses API](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/responses).
- Don't conflate model upgrades with API upgrades — test independently

### Consider `model-router`
- **`model-router`** (GA Nov 2025) automatically routes requests to the best-suited model, reducing migration frequency.
- Has its own retirement timeline (2027-05-20 not before).
- See [Azure OpenAI Models Overview](https://learn.microsoft.com/azure/ai-services/openai/concepts/models) for details.

## Must Not

- Treat LLM models as permanent infrastructure — they have a defined lifecycle
- Rely solely on subscription-owner emails for retirement notifications — set up Health Alerts
- Wait until auto-upgrade kicks in to test a replacement model
- Mix embeddings from different model versions in the same vector store
- Skip the cleanup step — old deployments waste quota

## References

- [Azure OpenAI Model Retirements](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements) — authoritative retirement dates
- [Model Version Upgrade Guide](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-versions)
- [Azure OpenAI Models Overview](https://learn.microsoft.com/azure/ai-services/openai/concepts/models) — model capabilities & availability
- [API Version Lifecycle](https://learn.microsoft.com/azure/ai-foundry/openai/api-version-lifecycle)
- [Managing Provisioned Deployments](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/working-with-models)
- [Responses API](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/responses) — new API surface
- [Azure Service Health Alerts](https://learn.microsoft.com/azure/service-health/alerts-activity-log-service-notifications-portal)
- [What's New in Azure OpenAI](https://learn.microsoft.com/azure/ai-foundry/openai/whats-new) — latest changes
- [Azure OpenAI SDKs](https://learn.microsoft.com/azure/ai-foundry/openai/supported-languages) — all supported languages
