# LLM Upgrade Lifecycle Best Practices — Azure OpenAI & Microsoft Foundry

> **⚠️ IMPORTANT: Retirement dates and model availability change frequently.**
> Always verify against the **[official Azure OpenAI Model Retirements page](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements)** for the latest authoritative information. See also: **[What's New in Azure OpenAI](https://learn.microsoft.com/azure/ai-foundry/openai/whats-new)**.

> **Audience:** Platform teams, architects, and engineering leads building on Azure OpenAI / Microsoft Foundry.
> **Last updated:** February 2026

---

## 1. Why This Matters

Azure OpenAI models are **continually refreshed** with newer, more capable versions. Older models go through a formal **deprecation → retirement** cycle. Once retired, deployments return errors — there is no grace period.

The pace is fast: GA models are guaranteed for **12 months minimum**, with an additional 6-month window for existing customers. Preview models can retire in as little as **90–120 days**. Treating LLMs as permanent infrastructure is a recipe for production incidents.

---

## 2. Key Terminology

| Term | Meaning |
|---|---|
| **Deprecation** | Model is no longer available to *new* customers. Existing deployments keep working until retirement. |
| **Retirement** | Model is removed entirely. All deployments return error responses. |
| **Auto-update to default** | Deployment automatically moves to the new default version when one is published. |
| **Upgrade when expired** | Deployment automatically upgrades only when its current version reaches retirement. |
| **No Auto Upgrade** | Deployment **stops working** at retirement — you must migrate manually. |

---

## 3. Understanding the Model Lifecycle Timeline

```
Model Launch (GA)
    │
    ├── 12 months ──► Deprecation (no new customers)
    │
    ├── up to 18 months ──► Retirement (existing customers)
    │                        ↳ Standard deployments may auto-upgrade ~3 weeks before retirement
    │                        ↳ Provisioned / Global Standard may get extended timelines
    │
    └── Replacement model available (N+1) for side-by-side comparison
         ↳ Customers get 60 days to try the new GA model before any auto-upgrades begin
```

**Preview models** follow an accelerated timeline: 90–120 day "not sooner than" retirement, with 30 days' notice before upgrades.

### Current Lifecycle Examples (as of Feb 2026)

#### GPT Series

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

#### o-Series (Reasoning)

| Model | GA Version | Retirement (not before) | Replacement |
|---|---|---|---|
| `o1` | 2024-12-17 | 2026-07-15 | `o3` |
| `o3-mini` | 2025-01-31 | 2026-08-02 | `o4-mini` |
| `o3` | 2025-04-16 | 2026-10-16 | — |
| `o4-mini` | 2025-04-16 | 2026-10-16 | — |

#### Other

| Model | GA Version | Retirement (not before) | Notes |
|---|---|---|---|
| `model-router` | 2025-11-18 | 2027-05-20 | Auto-routes requests to optimal model |

> ⚠️ These are "not sooner than" dates — they can be extended but not shortened. Always check the [official retirements page](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements).
> 📌 **Note:** ChatGPT (consumer) and Azure Foundry (enterprise) have **independent** retirement schedules. Don't rely on ChatGPT announcements for Azure dates.

---

## 4. How to Migrate to a Newer Model (Tactical Playbook)

### 4.1 Set Up Notifications Early

Azure notifies via **Azure Service Health** and **email to subscription owners**.

**Recommended configuration:**
- Go to **Azure Service Health → Health Alerts**
- Service: `Azure OpenAI Service`
- Event types: `Health advisories` (covers upgrade, deprecation & retirement notifications)
- Create an **Action Group** with email + SMS for your on-call team

> Anyone with *reader* permissions can configure personalized alerts. Don't rely solely on subscription-owner emails.

### 4.2 Inventory Your Deployments

Maintain a **live inventory** of every deployment across all subscriptions:

| Subscription | Resource Group | Resource | Deployment Name | Model | Version | Update Policy | Region |
|---|---|---|---|---|---|---|---|
| Prod-Sub-01 | rg-ai-prod | aoai-eastus | chat-prod | gpt-4o | 2024-08-06 | Upgrade when expired | East US |
| … | … | … | … | … | … | … | … |

Automate this via the **Control Plane API** or **Azure Resource Graph** queries. Review quarterly at minimum.

**Azure Resource Graph query example** (run in Azure Portal → Resource Graph Explorer or via `az graph query`):

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

> This gives you a live, cross-subscription view of every Azure OpenAI deployment and its update policy.

### 4.3 Deploy the Replacement Model Side-by-Side

Azure guarantees the current model `N` and its successor `N+1` are available concurrently (for models from `gpt-4o` onwards). Use this window to:

1. **Deploy the new model** in a non-production slot or a parallel deployment.
2. **Run your evaluation suite** against both (see Section 5).
3. **Compare** latency, token costs, output quality, and behavioral differences.

### 4.4 Run Evaluations

Use [Azure OpenAI Evaluations (Preview)](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/evaluations) to compare model versions systematically:

- Create an evaluation dataset from real production prompts (use **Stored Completions** as a source).
- Define testing criteria: **Criteria match**, **Text quality** (BLEU/ROUGE), **Valid JSON**, **Matches schema**, or custom grading prompts.
- Run the same dataset against both the old and new model.
- Use the **compare** view or **cluster analysis** in Foundry to identify regressions.

> 💡 **Tip:** The new Foundry portal's **Ask AI** feature can scan your project, detect deprecated models, recommend replacements, and even kick off evaluation runs for you.

### 4.5 Update Prompts & Parameters

Newer models often have different:
- **Default behaviors** (e.g., verbosity, formatting, tool-calling conventions)
- **Supported parameters** (new features may be added; some may change semantics)
- **Token limits & pricing**

Review the model's release notes and `what's new` documentation. Adjust system prompts and parameters accordingly.

### 4.6 Migrate Production Traffic

Choose your strategy based on risk tolerance:

| Strategy | Description | Best For |
|---|---|---|
| **Blue-green** | Deploy new model in parallel, switch traffic at once after validation. | Low-traffic or batch workloads |
| **Canary / progressive rollout** | Route a small % of traffic to the new model; increase over days. | High-traffic, latency-sensitive workloads |
| **In-place migration** (Provisioned only) | Update the existing deployment's model version; Azure migrates traffic over 20–30 min. | Provisioned deployments where you want to keep the same deployment name/endpoint |

For **Standard** deployments, create a new deployment with the target model and update your application config.

For **Provisioned** deployments, you can use:
- **In-place migration:** Same deployment name, Azure handles traffic cut-over (~20–30 min).
- **Multi-deployment migration:** Create a second provisioned deployment with the new model, shift traffic, then delete the old one. Requires sufficient quota for both simultaneously.

### 4.7 Clean Up

After migration:
- Delete old deployments to free quota.
- Remove stale serverless API connections from projects.
- Update documentation and runbooks.

---

## 5. Defining and Running Evaluations — A Deep Dive

Evaluation is the **gate** between "we have a new model" and "we can ship it." This section provides concrete guidance on building evaluations that give you confidence during model upgrades.

### 5.1 Choose the Right Evaluators

The `azure-ai-evaluation` SDK provides a rich set of built-in evaluators. Pick the ones that matter for your use case:

| Category | Evaluators | When to Use |
|---|---|---|
| **General quality** | `CoherenceEvaluator`, `FluencyEvaluator`, `QAEvaluator` | Every model upgrade — baseline quality |
| **RAG / grounding** | `GroundednessEvaluator`, `GroundednessProEvaluator`, `RelevanceEvaluator`, `RetrievalEvaluator`, `ResponseCompletenessEvaluator` | Apps using retrieval-augmented generation |
| **Textual similarity** | `SimilarityEvaluator`, `F1ScoreEvaluator`, `BleuScoreEvaluator`, `RougeScoreEvaluator`, `MeteorScoreEvaluator` | Comparing outputs against known-good reference answers |
| **Safety** | `ViolenceEvaluator`, `SexualEvaluator`, `SelfHarmEvaluator`, `HateUnfairnessEvaluator`, `ContentSafetyEvaluator`, `IndirectAttackEvaluator` | Every model upgrade — compliance |
| **Agentic** | `IntentResolutionEvaluator`, `ToolCallAccuracyEvaluator`, `TaskAdherenceEvaluator` | If your app uses function calling / agents |
| **Azure OpenAI Graders** | `AzureOpenAILabelGrader`, `AzureOpenAIStringCheckGrader`, `AzureOpenAIGrader` | Custom classification or structured-output checks |

**Composite evaluators** for convenience:
- `QAEvaluator` = groundedness + relevance + coherence + fluency + similarity + F1 in one call.
- `ContentSafetyEvaluator` = violence + sexual + self-harm + hate/unfairness in one call.

### 5.2 Prepare Your Evaluation Dataset

Your evaluation dataset is the most important asset. Format it as **JSONL** with one record per line:

```jsonl
{"query": "What is our refund policy?", "response": "You can request a refund within 30 days.", "context": "Our refund policy allows returns within 30 days of purchase.", "ground_truth": "Refunds are available within 30 days."}
{"query": "How do I reset MFA?", "response": "Go to Settings > Security > Reset MFA.", "context": "MFA can be reset from the Security settings page.", "ground_truth": "Navigate to Settings, then Security, then Reset MFA."}
```

**Key fields** (not all evaluators need all fields):

| Field | Description | Required By |
|---|---|---|
| `query` | The user's question/prompt | Most evaluators |
| `response` | The model's output | All evaluators |
| `context` | Grounding documents / retrieved context | Groundedness, Retrieval |
| `ground_truth` | Human-verified correct answer | Similarity, F1, BLEU, ROUGE |

**Dataset sizing guidance:**
- **Smoke test:** 10–20 rows covering core scenarios — run during development.
- **Pre-production gate:** 100–500 rows with diverse intents, edge cases, adversarial inputs.
- **Full regression:** 500+ rows, ideally sampled from real production traffic (use **Stored Completions**).

### 5.3 Local Evaluation with `azure-ai-evaluation` SDK

This approach runs evaluations on your dev machine. Good for fast iteration during prompt tuning.

**Install:**

```bash
pip install azure-ai-evaluation
```

**Single-row spot check:**

```python
import os
from azure.ai.evaluation import RelevanceEvaluator

model_config = {
    "azure_endpoint": os.environ["AZURE_OPENAI_ENDPOINT"],
    "api_key": os.environ["AZURE_OPENAI_API_KEY"],
    "azure_deployment": os.environ["AZURE_OPENAI_DEPLOYMENT"],  # judge model
}

relevance_eval = RelevanceEvaluator(model_config)
result = relevance_eval(
    query="What is the capital of France?",
    response="Paris is the capital of France.",
)
print(result)
# {'relevance': 5.0, 'gpt_relevance': 5.0, 'relevance_reason': '...'}
```

**Batch evaluation on a dataset:**

```python
import os
from azure.ai.evaluation import (
    evaluate,
    CoherenceEvaluator,
    RelevanceEvaluator,
    GroundednessEvaluator,
    F1ScoreEvaluator,
)

model_config = {
    "azure_endpoint": os.environ["AZURE_OPENAI_ENDPOINT"],
    "api_key": os.environ["AZURE_OPENAI_API_KEY"],
    "azure_deployment": os.environ["AZURE_OPENAI_DEPLOYMENT"],
}

# Provide your Foundry project to log results to the portal
azure_ai_project = {
    "subscription_id": os.environ["AZURE_SUBSCRIPTION_ID"],
    "resource_group_name": os.environ["AZURE_RESOURCE_GROUP"],
    "project_name": os.environ["AZURE_PROJECT_NAME"],
}
# Alternative: use project URL directly
# azure_ai_project = "https://<resource>.services.ai.azure.com/api/projects/<project>"

result = evaluate(
    data="golden_dataset.jsonl",
    evaluators={
        "coherence": CoherenceEvaluator(model_config=model_config),
        "relevance": RelevanceEvaluator(model_config=model_config),
        "groundedness": GroundednessEvaluator(model_config=model_config),
        "f1_score": F1ScoreEvaluator(),
    },
    evaluator_config={
        "relevance": {
            "column_mapping": {
                "query": "${data.query}",
                "response": "${data.response}",
                "context": "${data.context}",
            }
        },
        "groundedness": {
            "column_mapping": {
                "query": "${data.query}",
                "response": "${data.response}",
                "context": "${data.context}",
            }
        },
    },
    azure_ai_project=azure_ai_project,
    output_path="./eval_results.json",
)

print(result["metrics"])
# {'coherence.coherence': 4.2, 'relevance.relevance': 4.5, ...}
print(f"View in Foundry: {result['studio_url']}")
```

**Evaluating with a target function** (send queries live to your app):

```python
from my_app import chat_with_products

def evaluate_target(query):
    response = chat_with_products(messages=[{"role": "user", "content": query}])
    return {"response": response["message"].content, "context": response["context"]}

result = evaluate(
    data="queries.jsonl",          # only needs "query" column
    target=evaluate_target,         # your app wrapper
    evaluators={"groundedness": GroundednessEvaluator(model_config=model_config)},
    evaluator_config={
        "default": {
            "column_mapping": {
                "query": "${data.query}",
                "context": "${target.context}",
                "response": "${target.response}",
            }
        }
    },
)
```

### 5.4 Cloud Evaluation with `azure-ai-projects` SDK

Cloud evaluation is recommended for **pre-production gates and CI/CD pipelines** — it runs in Azure, scales to large datasets, and logs everything to your Foundry project.

> ⚠️ **Important SDK version difference:** The `azure-ai-projects` SDK has significantly different APIs between v1.x and v2.x (≥ 2.0.0b3). Both are shown below.

---

#### Approach A — `azure-ai-projects` v1.x (uses `project_client.evaluations.create()`)

This is the **original cloud evaluation API**. It uses `EvaluatorConfiguration` objects and submits via the project client's `.evaluations` namespace.

```bash
pip install azure-ai-projects azure-identity
```

```python
import os
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    Evaluation,
    InputDataset,
    EvaluatorConfiguration,
    EvaluatorIds,
)

endpoint = os.environ["PROJECT_ENDPOINT"]
model_endpoint = os.environ["MODEL_ENDPOINT"]
model_api_key = os.environ["MODEL_API_KEY"]
model_deployment_name = os.environ["MODEL_DEPLOYMENT_NAME"]

project_client = AIProjectClient(
    endpoint=endpoint,
    credential=DefaultAzureCredential(),
)

# 1. Upload your dataset
data_id = project_client.datasets.upload_file(
    name="model-upgrade-eval",
    version="1.0",
    file_path="./golden_dataset.jsonl",
).id

# 2. Define evaluators
evaluators = {
    "relevance": EvaluatorConfiguration(
        id=EvaluatorIds.RELEVANCE.value,
        init_params={"deployment_name": model_deployment_name},
        data_mapping={
            "query": "${data.query}",
            "response": "${data.response}",
        },
    ),
    "groundedness": EvaluatorConfiguration(
        id=EvaluatorIds.GROUNDEDNESS.value,
        init_params={"deployment_name": model_deployment_name},
        data_mapping={
            "query": "${data.query}",
            "response": "${data.response}",
            "context": "${data.context}",
        },
    ),
    "violence": EvaluatorConfiguration(
        id=EvaluatorIds.VIOLENCE.value,
        init_params={"azure_ai_project": endpoint},
    ),
    "bleu_score": EvaluatorConfiguration(
        id=EvaluatorIds.BLEU_SCORE.value,
    ),
}

# 3. Submit the evaluation
evaluation = Evaluation(
    display_name="gpt-4.1 upgrade eval",
    description="Pre-upgrade evaluation comparing gpt-4o → gpt-4.1",
    data=InputDataset(id=data_id),
    evaluators=evaluators,
)

evaluation_response = project_client.evaluations.create(
    evaluation,
    headers={
        "model-endpoint": model_endpoint,
        "api-key": model_api_key,
    },
)

print(f"Evaluation: {evaluation_response.name}, Status: {evaluation_response.status}")
```

---

#### Approach B — `azure-ai-projects` ≥ 2.0.0b3 (uses OpenAI Evals API via `openai_client.evals`)

Starting with v2, the SDK uses the **OpenAI Evals API** exposed through `project_client.get_openai_client()`. This is a fundamentally different programming model using `testing_criteria` and `data_source_config` instead of `EvaluatorConfiguration`.

```bash
pip install azure-ai-projects>=2.0.0b3 azure-identity
```

```python
import os
import time
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

endpoint = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
model_deployment_name = os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]

with (
    DefaultAzureCredential() as credential,
    AIProjectClient(endpoint=endpoint, credential=credential) as project_client,
    project_client.get_openai_client() as openai_client,
):
    # 1. Define data schema
    data_source_config = {
        "type": "custom",
        "item_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "response": {"type": "string"},
                "context": {"type": "string"},
                "ground_truth": {"type": "string"},
            },
            "required": [],
        },
        "include_sample_schema": True,
    }

    # 2. Define testing criteria (mix built-in evaluators, graders, etc.)
    testing_criteria = [
        {
            "type": "azure_ai_evaluator",
            "name": "violence",
            "evaluator_name": "builtin.violence",
            "data_mapping": {
                "query": "{{item.query}}",
                "response": "{{item.response}}",
            },
        },
        {
            "type": "azure_ai_evaluator",
            "name": "coherence",
            "evaluator_name": "builtin.coherence",
            "initialization_parameters": {"deployment_name": model_deployment_name},
        },
        {
            "type": "azure_ai_evaluator",
            "name": "fluency",
            "evaluator_name": "builtin.fluency",
            "initialization_parameters": {"deployment_name": model_deployment_name},
            "data_mapping": {
                "query": "{{item.query}}",
                "response": "{{item.response}}",
            },
        },
        {
            "type": "azure_ai_evaluator",
            "name": "f1",
            "evaluator_name": "builtin.f1_score",
        },
        # You can also add OpenAI-native graders:
        {
            "type": "score_model",
            "model": model_deployment_name,
            "name": "helpfulness",
            "input": [
                {
                    "role": "user",
                    "content": (
                        "Score the assistant response for helpfulness (0.0–1.0). "
                        "Query: {{item.query}}. Response: {{item.response}}."
                    ),
                }
            ],
            "range": [0.0, 1.0],
        },
    ]

    # 3. Create the evaluation object
    eval_object = openai_client.evals.create(
        name="Model Upgrade Eval — gpt-4o vs gpt-4.1",
        data_source_config=data_source_config,
        testing_criteria=testing_criteria,
    )
    print(f"Eval created: {eval_object.id}")

    # 4. Upload dataset and create a run
    dataset = project_client.datasets.upload_file(
        name="upgrade-eval-data",
        version="1.0",
        file_path="./golden_dataset.jsonl",
    )

    from openai.types.evals.create_eval_jsonl_run_data_source_param import (
        CreateEvalJSONLRunDataSourceParam,
        SourceFileID,
    )

    eval_run = openai_client.evals.runs.create(
        eval_id=eval_object.id,
        name="gpt-4.1 upgrade run",
        data_source=CreateEvalJSONLRunDataSourceParam(
            type="jsonl",
            source=SourceFileID(type="file_id", id=dataset.id),
        ),
    )
    print(f"Run created: {eval_run.id}")

    # 5. Poll for completion
    while True:
        run = openai_client.evals.runs.retrieve(
            run_id=eval_run.id, eval_id=eval_object.id
        )
        if run.status in ("completed", "failed"):
            break
        time.sleep(5)

    # 6. Inspect results
    output_items = list(
        openai_client.evals.runs.output_items.list(
            run_id=run.id, eval_id=eval_object.id
        )
    )
    for item in output_items:
        print(item)
    print(f"Report: {run.report_url}")
```

---

#### SDK Version Comparison at a Glance

| Aspect | `azure-ai-projects` v1.x | `azure-ai-projects` ≥ 2.0.0b3 |
|---|---|---|
| **Client** | `AIProjectClient` → `.evaluations` | `AIProjectClient` → `.get_openai_client()` → `.evals` |
| **Evaluator config** | `EvaluatorConfiguration` + `EvaluatorIds` enum | `testing_criteria` dicts with `builtin.*` names |
| **Data mapping** | `data_mapping={"query": "${data.query}"}` | `data_mapping={"query": "{{item.query}}"}` |
| **Submission** | `project_client.evaluations.create(Evaluation(...))` | `openai_client.evals.create(...)` then `.evals.runs.create(...)` |
| **Auth headers** | Passed in `headers={"model-endpoint": ..., "api-key": ...}` | Inherited from project client; credential-based |
| **Native graders** | Not supported | `score_model`, `label_model`, `string_check`, `text_similarity` |
| **Run management** | Single call returns results | Eval object + Run object (poll for completion) |

> **Migration note:** If upgrading from v1 to v2, you will need to rewrite your evaluation code. The APIs are not backward-compatible. Plan this as part of your SDK upgrade.

### 5.5 Running Evaluations for A/B Model Comparison

The core workflow for a model upgrade evaluation:

```
┌──────────────┐    ┌──────────────┐
│ Golden Dataset│───▶│  Model A     │──▶ eval_results_A.json
│  (JSONL)     │    │  (current)   │
│              │    └──────────────┘
│              │    ┌──────────────┐
│              │───▶│  Model B     │──▶ eval_results_B.json
│              │    │  (candidate) │
└──────────────┘    └──────────────┘
                           │
                    Compare metrics
                    Flag regressions
```

**Steps:**

1. **Generate responses from both models** on the same dataset. Use the `target` parameter of `evaluate()` with two different wrapper functions, or pre-generate responses and include them in separate columns.
2. **Run the same evaluators** on both result sets.
3. **Compare metrics.** A simple Python script or Foundry's compare view can surface deltas.
4. **Define acceptance thresholds:**

| Metric | Minimum Acceptable | Action if Below |
|---|---|---|
| Coherence | ≥ 3.5 / 5 | Block migration |
| Groundedness | ≥ 4.0 / 5 | Block migration |
| Relevance | ≥ 3.5 / 5 | Block migration |
| Safety (violence, etc.) | Severity < 2 | Block migration |
| BLEU / ROUGE | Δ ≤ 10% drop vs current | Investigate, may block |
| Latency P95 | ≤ 120% of current | Investigate |

### 5.6 Continuous Evaluation for Post-Deployment Monitoring

After migrating, set up **continuous evaluation** to catch regressions in production:

```python
from azure.ai.projects.models import (
    EvaluationRule,
    ContinuousEvaluationRuleAction,
    EvaluationRuleFilter,
    EvaluationRuleEventType,
)

# Assumes you already created an eval_object via openai_client.evals.create()
continuous_eval_rule = project_client.evaluation_rules.create_or_update(
    id="post-migration-monitor",
    evaluation_rule=EvaluationRule(
        display_name="Post-Migration Quality Monitor",
        description="Runs on every agent response after gpt-4.1 migration",
        action=ContinuousEvaluationRuleAction(
            eval_id=eval_object.id, max_hourly_runs=100
        ),
        event_type=EvaluationRuleEventType.RESPONSE_COMPLETED,
        filter=EvaluationRuleFilter(agent_name="my-prod-agent"),
        enabled=True,
    ),
)
```

This monitors production traffic against your testing criteria and flags quality drops automatically.

### 5.7 Leverage GitHub Copilot for Evaluation Design

GitHub Copilot has evolved well beyond inline completions. Its current capabilities — **Agent Mode**, **Custom Agents**, **Agent Skills**, and the **Copilot Coding Agent** — can dramatically accelerate every stage of your model-upgrade evaluation workflow.

#### 5.7.1 Use Copilot Agent Mode for Evaluation Tasks

Agent Mode (available in VS Code, Copilot CLI, and GitHub.com) autonomously plans, executes multi-file changes, runs terminal commands, and self-corrects. Use it directly for evaluation work:

**Generate a golden dataset from production logs:**

```
Generate a JSONL evaluation dataset from the files in /logs/chat-completions/.
Each line should have "query", "response", "context", and "ground_truth" fields.
Sample from diverse user intents. Include at least 50 entries.
Save to ./evaluation/golden_dataset.jsonl.
```

**Scaffold a complete evaluation script:**

```
Write a Python script that compares gpt-4o outputs against gpt-4.1 outputs
on our golden dataset using RelevanceEvaluator, CoherenceEvaluator, and
GroundednessEvaluator from azure-ai-evaluation. Log results to our Foundry
project. Include column mappings and output_path. Run the script to verify it works.
```

**Build a custom evaluator for domain-specific quality:**

```
Create a custom evaluator class compatible with azure-ai-evaluation's evaluate() API
that checks whether assistant responses follow our citation format:
[Source: <name>, <date>]. Score 1 if compliant, 0 if not. Add unit tests.
```

**Generate A/B comparison reports:**

```
Compare the evaluation results in eval_results_gpt4o.json and eval_results_gpt41.json.
Create a markdown summary table showing metric deltas per evaluator.
Flag any regressions greater than 5%.
```

> In Agent Mode, Copilot will read files, write code, run the script, inspect errors, and iterate — all autonomously. You approve the final result.

#### 5.7.2 Create a Reusable Agent Skill for Model Evaluation

**Agent Skills** (`.github/skills/` or `~/.copilot/skills/`) let you encode repeatable evaluation workflows so that any team member (or the Copilot Coding Agent) can trigger them consistently.

Create a skill at `.github/skills/model-evaluation/SKILL.md`:

```markdown
---
name: model-evaluation
description: "Run standardized evaluation suite comparing current and candidate LLM models for quality, safety, and cost."
---

# Model Evaluation Skill

## Purpose
Run the standard model-upgrade evaluation pipeline to compare a current model
deployment against a candidate replacement.

## Steps
1. Load the golden dataset from `./evaluation/golden_dataset.jsonl`.
2. Generate responses from both the current and candidate model deployments.
3. Run the following evaluators using `azure-ai-evaluation`:
   - Quality: CoherenceEvaluator, FluencyEvaluator, RelevanceEvaluator
   - RAG: GroundednessEvaluator, ResponseCompletenessEvaluator
   - Safety: ContentSafetyEvaluator
   - Textual: BleuScoreEvaluator, F1ScoreEvaluator
4. Save results to `./evaluation/results/<model_name>_<date>.json`.
5. Generate a comparison report in `./evaluation/reports/comparison_<date>.md`.
6. Log both evaluation runs to the Foundry project.

## Acceptance Criteria
- Coherence ≥ 3.5, Groundedness ≥ 4.0, Relevance ≥ 3.5
- All safety evaluator scores: severity < 2
- BLEU/F1 regression must be ≤ 10% vs current model

## Environment Variables Required
- AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT
- AZURE_AI_PROJECT (Foundry project URL)
- CURRENT_MODEL_DEPLOYMENT, CANDIDATE_MODEL_DEPLOYMENT

## Must Not
- Commit API keys or evaluation data containing PII.
- Modify production deployment configuration.
```

Once this skill is in your repo, any prompt referencing model evaluation will automatically pick it up — in Copilot Chat, Agent Mode, Copilot CLI, or the Coding Agent.

#### 5.7.3 Define a Custom Agent for Upgrade Governance

**Custom Agents** (`.github/agents/`) create specialized AI personas. Define one for model-upgrade governance:

Create `.github/agents/model-upgrade-evaluator.agent.md`:

```markdown
---
name: model-upgrade-evaluator
description: "Specialized agent for evaluating LLM model upgrades. Runs evaluation pipelines, compares metrics, and produces go/no-go recommendations."
tools: ["test", "read", "edit", "terminal"]
---

# Model Upgrade Evaluator Agent

## Role
You are a quality assurance engineer specializing in LLM model migrations.
Your job is to evaluate candidate models against the current production model
and produce a go/no-go recommendation.

## Workflow
1. Read the current deployment inventory from `./config/deployments.yaml`.
2. Identify which models are approaching deprecation (check against the
   retirement dates in the config).
3. For each model needing upgrade, run the model-evaluation skill.
4. Compare results against acceptance thresholds.
5. Produce a summary report with a clear PASS/FAIL recommendation per deployment.

## Output Format
- Markdown report in `./evaluation/reports/`
- Include: model names, versions, all metric scores, deltas, and recommendation
- If ANY metric fails threshold, recommendation = **FAIL** with specific reasons

## Boundaries
- Never modify production deployments or IaC files.
- Never skip safety evaluators.
- Always log results to the Foundry project for audit.
```

#### 5.7.4 Assign Model Upgrade Tasks to the Copilot Coding Agent

The **Copilot Coding Agent** can be assigned tasks directly from GitHub Issues or Copilot Chat. It works autonomously in a secure cloud environment, creates a branch, makes changes, and opens a draft PR for your review.

Use it for model-upgrade evaluation tasks:

- **From a GitHub Issue:**
  > **Title:** Evaluate gpt-4.1 as replacement for gpt-4o in chat-prod deployment
  >
  > **Body:** Run the model-evaluation skill against the golden dataset. Compare gpt-4o (2024-08-06) vs gpt-4.1 (2025-04-14). Update the evaluation report and add results to `/evaluation/reports/`. Assign to Copilot.

- **From Copilot Chat (agent mode):**
  ```
  Run the model-upgrade-evaluator agent to assess whether we can migrate
  our chat-prod deployment from gpt-4o to gpt-4.1. Use the golden dataset
  and log results to Foundry.
  ```

The coding agent will clone the repo, run the evaluation scripts, commit the results, and open a PR with the comparison report — all without manual intervention.

#### 5.7.5 Wire Evaluations into CI/CD with Copilot's Help

Ask Copilot to create a GitHub Actions workflow that gates deployments on evaluation results:

```
Create a GitHub Actions workflow that triggers on PRs changing files in
config/model-deployments/ or prompts/. It should:
1. Run the model-evaluation skill against the golden dataset.
2. Parse the evaluation results JSON.
3. Fail the check if groundedness < 4.0 or any safety score severity >= 2.
4. Post a summary comment on the PR with all metric scores.
```

> 💡 **Tip:** Copilot CLI (`gh copilot`) can also explain existing evaluator code, suggest which built-in evaluators fit your scenario, and help debug column-mapping issues — just describe the problem in natural language.

---

## 6. Mid-to-Long Term Planning to Minimize Impact

### 6.1 Architect for Model Portability

**Abstract the model reference.** Never hard-code model names deep in application code.

```
✅ Good: config/environment variable → CHAT_MODEL_DEPLOYMENT=chat-prod
❌ Bad:  hard-coded "gpt-4o" scattered across 47 source files
```

Design your application so that swapping a model is a **configuration change**, not a code change.

### 6.2 Choose the Right Update Policy

| Policy | Pros | Cons | Recommended For |
|---|---|---|---|
| **Auto-update to default** | Hands-off; always on latest default | May introduce unexpected behavior changes | Dev/test environments, non-critical workloads |
| **Upgrade when expired** | Gives maximum time on current version; auto-migrates before retirement | Still a forced upgrade at end of life; limited testing window | Production workloads with moderate change tolerance |
| **No Auto Upgrade** | Full control over timing | **Deployment breaks at retirement if you miss the deadline** | Regulated workloads with strict change-control — *only if you commit to proactive migration* |

> **Recommendation for production:** Use **"Upgrade when expired"** as a safety net, but always plan to migrate proactively well before the retirement date.

### 6.3 Build a Continuous Evaluation Pipeline

Don't wait for a deprecation notice to evaluate models. Build evaluation into your CI/CD:

1. **Golden dataset:** Maintain a curated set of representative prompt/response pairs reflecting your real-world use cases.
2. **Automated eval runs:** On a schedule (e.g., monthly) or triggered by new model availability, run your golden dataset against current and candidate models.
3. **Quality gates:** Define pass/fail thresholds (accuracy, latency P95, cost per 1K tokens). Block migration if thresholds aren't met.
4. **Track drift:** Compare current production model outputs against your golden dataset over time to detect degradation even within the same model version.

### 6.4 Plan on a ~12-Month Cadence

Given the 12-month GA lifecycle, plan for **at least one major model migration per year** for each model family you consume. Build this into your team's roadmap:

| Quarter | Activity |
|---|---|
| **Q1** | Review current model inventory. Check retirement dates. Identify models entering their last 6 months. |
| **Q2** | Deploy successor models in staging. Run evaluations. Update prompts. |
| **Q3** | Begin canary rollout to production. Monitor quality metrics. |
| **Q4** | Complete migration. Clean up old deployments. Retrospective. |

Adjust timing based on actual retirement dates — the point is to be **proactive, not reactive**.

### 6.5 Manage API Version Lifecycle Separately

Model versions and **API versions** are independent lifecycle tracks. API versions also deprecate and retire.

Key guidance:
- **GA API versions** receive at least 12 months of support.
- **Preview API versions** can retire on shorter notice.
- Starting mid-2025, the **next-generation v1 API** (`/openai/v1/...`) eliminates the need to track monthly `api-version` strings — use `api-version=latest` or `api-version=preview`.
- If using Azure OpenAI SDKs (C#, Go, Java), update the **SDK version** — each SDK release targets specific API versions.
- The **Responses API** is now available alongside Chat Completions and is the recommended path for new development. Existing Chat Completions code continues to work. See [Responses API docs](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/responses).

> ⚠️ Don't conflate model upgrades with API upgrades. Test them independently.

### 6.5.1 Consider `model-router` as a Migration Strategy

**`model-router`** (GA Nov 2025) automatically routes each request to the best-suited model based on prompt complexity. Benefits for lifecycle management:

- **Reduces migration frequency:** As new models are added to the router, your app benefits without code changes.
- **Acts as an abstraction layer:** You don't pin to a specific model version.
- **Has its own retirement date** (2027-05-20 not before), so you still need to track it.

> See [Azure OpenAI Models Overview](https://learn.microsoft.com/azure/ai-services/openai/concepts/models) for `model-router` details.

### 6.6 Account for Fine-Tuned Models

Fine-tuned models follow a **two-phase retirement**: training retirement first, then deployment retirement (typically 1 year later).

- After training retirement, you can no longer fine-tune — but already-trained models keep serving.
- Plan to **re-fine-tune on the successor base model** before deployment retirement.
- Budget time for data preparation, training, and evaluation of the new fine-tuned model.

Current fine-tuned model dates (verify on [official page](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements)):
- `gpt-4o` fine-tuned: training retirement no earlier than **2026-09-30**, deployment retirement **2027-03-31**
- `gpt-4o-mini` fine-tuned: same schedule as `gpt-4o` fine-tuned

### 6.7 Handle Embedding Models with Extra Care

Embedding models (e.g., `text-embedding-3-large`, `text-embedding-ada-002`) are currently scheduled **not to retire before April 2027**, but when they eventually do:

- You will need to **re-embed your entire vector store** with the new model.
- Embeddings from different models are **not compatible** — mixing them breaks retrieval quality.
- Plan for the compute and time cost of full re-indexing.

### 6.8 Govern Multi-Region Deployments

- Model upgrades roll out **region by region** — there is no fixed region schedule.
- A model upgrade can happen in a region **even if the new version isn't yet separately available there**.
- If you use multi-region for resilience, stagger your manual migrations so you can fall back to a region still on the old model during validation.

---

## 7. Operational Checklist

Use this checklist when a model retirement is announced:

- [ ] **Verify notification** — confirm retirement date and replacement model from the [official page](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements).
- [ ] **Inventory impact** — identify all deployments using the retiring model across all subscriptions/regions.
- [ ] **Deploy replacement** — create a new deployment with the successor model in your test environment.
- [ ] **Run evaluations** — execute your golden dataset against the new model; compare results.
- [ ] **Adjust prompts** — tune system prompts, parameters, and any model-specific logic.
- [ ] **Update API version** — if the new model requires a newer API version, test that independently.
- [ ] **Canary rollout** — route a small percentage of production traffic to the new model.
- [ ] **Monitor** — watch error rates, latency, user feedback, and quality metrics for regressions.
- [ ] **Full rollout** — migrate remaining traffic.
- [ ] **Clean up** — delete old deployments, update IaC templates, and close the migration ticket.
- [ ] **Retrospective** — document lessons learned; update evaluation datasets with new edge cases found.

---

## 8. Key Resources

| Resource | Link |
|---|---|
| Model deprecation & retirement dates | https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements |
| Model version upgrade guide | https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-versions |
| API version lifecycle | https://learn.microsoft.com/azure/ai-foundry/openai/api-version-lifecycle |
| Azure OpenAI Evaluations (portal) | https://learn.microsoft.com/azure/ai-foundry/openai/how-to/evaluations |
| Local evaluation SDK guide | https://learn.microsoft.com/azure/ai-foundry/how-to/develop/evaluate-sdk |
| Cloud evaluation SDK guide | https://learn.microsoft.com/azure/ai-foundry/how-to/develop/cloud-evaluation |
| Agent evaluation guide | https://learn.microsoft.com/azure/ai-foundry/how-to/develop/agent-evaluate-sdk |
| `azure-ai-evaluation` SDK reference | https://learn.microsoft.com/python/api/overview/azure/ai-evaluation-readme |
| `azure-ai-projects` SDK (v2 docs) | https://azuresdkdocs.z19.web.core.windows.net/python/azure-ai-projects/2.0.0b2/index |
| Evaluation samples (GitHub) | https://github.com/Azure-Samples/azureai-samples/tree/main/scenarios/evaluate |
| Managing provisioned deployments | https://learn.microsoft.com/azure/ai-foundry/openai/how-to/working-with-models |
| Model upgrade with Ask AI (Foundry) | https://learn.microsoft.com/azure/ai-foundry/observability/how-to/optimization-model-upgrade |
| Continuous evaluation for agents | https://learn.microsoft.com/azure/ai-foundry/how-to/continuous-evaluation-agents |
| Azure Service Health alerts setup | https://learn.microsoft.com/azure/service-health/alerts-activity-log-service-notifications-portal |
| What's new in Azure OpenAI | https://learn.microsoft.com/azure/ai-foundry/openai/whats-new |

---

*This document should be reviewed quarterly and updated when Microsoft publishes new lifecycle policies or tooling changes.*

> **📌 Official Documentation Bookmarks:**
> - [Azure OpenAI Model Retirements](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements) — authoritative retirement dates
> - [Azure OpenAI Models Overview](https://learn.microsoft.com/azure/ai-services/openai/concepts/models) — model capabilities & availability
> - [What's New in Azure OpenAI](https://learn.microsoft.com/azure/ai-foundry/openai/whats-new) — latest changes
> - [Responses API](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/responses) — new API surface
> - [Azure OpenAI SDKs](https://learn.microsoft.com/azure/ai-foundry/openai/supported-languages) — all supported languages
