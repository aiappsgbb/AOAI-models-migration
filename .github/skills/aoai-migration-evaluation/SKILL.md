---
name: aoai-migration-evaluation
description: "Evaluate and validate Azure OpenAI model migrations using A/B comparison, LLM-as-Judge, local SDK evaluation, and Azure AI Foundry cloud evaluation. Covers RAG, tool calling, translation, and classification scenarios."
---

# Azure OpenAI Migration Evaluation Skill

## Purpose

Run standardized evaluations to compare a current Azure OpenAI model against a candidate replacement and produce a go/no-go recommendation. Detect regressions before deploying a new model in production. Adopt a **continuous evaluation** approach to reduce migration costs over time.

## When to Use

- Validating quality when migrating between Azure OpenAI models
- Running A/B model comparisons (source vs target)
- Setting up evaluation pipelines for model upgrades
- Building golden datasets for regression testing
- Running local or cloud-based evaluations
- Establishing continuous evaluation to keep migration costs low across model generations

## Evaluation Architecture

```
┌──────────────┐    ┌──────────────┐
│ Golden Dataset│───►│  Model A     │──► eval_results_A
│  (test cases)│    │  (current)   │
│              │    └──────────────┘
│              │    ┌──────────────┐
│              │───►│  Model B     │──► eval_results_B
│              │    │  (candidate) │
└──────────────┘    └──────────────┘
                           │
                    Compare metrics
                    Flag regressions
```

## Pre-Built Evaluation Scenarios

This repo provides four ready-to-run scenarios under `src/evaluate/scenarios/`:

| Scenario | Module | Metrics | Test Cases |
|---|---|---|---|
| **RAG** | `src/evaluate/scenarios/rag.py` | Groundedness, Relevance, Coherence | 8 examples (policies, technical docs, legal, financial) |
| **Tool Calling** | `src/evaluate/scenarios/tool_calling.py` | Tool Accuracy, Parameter Accuracy, Relevance | 8 examples (weather, calendar, email, search, stock) |
| **Translation** | `src/evaluate/scenarios/translation.py` | Fluency, Coherence, Relevance | 10 examples (FR/EN/DE, business/tech/legal/medical) |
| **Classification** | `src/evaluate/scenarios/classification.py` | Accuracy, Consistency, Relevance | 16 examples (sentiment, tickets, intent, priority) |

### Quick Start — Run a Pre-Built Scenario

```python
from src.evaluate.scenarios import create_rag_evaluator

evaluator = create_rag_evaluator(
    source_model="gpt-4o",
    target_model="gpt-4.1",
)
report = evaluator.run()
report.print_report()
```

Other scenario factories:

```python
from src.evaluate.scenarios import (
    create_rag_evaluator,
    create_tool_calling_evaluator,
    create_translation_evaluator,
    create_classification_evaluator,
)
```

---

## Two SDK Approaches — Critical Differences

There are **two fundamentally different evaluation SDKs** offered by Microsoft. They differ in API surface, data mapping syntax, execution model, and SDK packages. Understanding these differences is critical before writing any evaluation code.

### Comparison Table: v1 (Local SDK) vs v2 (Cloud OpenAI Evals API)

| Aspect | **v1 — Local SDK (`azure-ai-evaluation`)** | **v2 — Cloud Evals API (`azure-ai-projects`)** |
|---|---|---|
| **Package** | `pip install azure-ai-evaluation` | `pip install "azure-ai-projects>=2.0.0b1" azure-identity openai` |
| **Latest version** | `azure-ai-evaluation>=1.15.0` (Feb 2026) | `azure-ai-projects>=2.0.0b3` (Jan 2026) |
| **Execution** | Runs **locally** on your machine (Python process) | Runs **in Azure cloud** (server-side, async) |
| **Entry point** | `from azure.ai.evaluation import evaluate` | `client = project_client.get_openai_client()` then `client.evals.create()` / `client.evals.runs.create()` |
| **Evaluator specification** | Python class instances: `CoherenceEvaluator(model_config=...)` | Dict-based `testing_criteria` with `"evaluator_name": "builtin.coherence"` |
| **Data mapping syntax** | `"${data.query}"` and `"${outputs.response}"` | `"{{item.query}}"` and `"{{sample.output_text}}"` |
| **Config structure** | `evaluator_config` dict with `column_mapping` per evaluator | `data_mapping` dict inside each `testing_criteria` entry |
| **Data source** | Local JSONL/CSV file path string | Uploaded dataset (via `project_client.datasets.upload_file()`) or inline `file_content` |
| **Result logging** | Optional: pass `azure_ai_project` param to log to Foundry | Automatic: results always stored in Foundry project |
| **Eval/Run separation** | Single `evaluate()` call does everything | Two-step: create eval definition → create run(s) against it |
| **Agent evaluation** | Supports agent inputs via conversation format | Native agent targets (`azure_ai_agent`, `azure_ai_responses`) |
| **CI/CD integration** | Run in any Python CI job | Cloud-native; poll for async results |
| **Continuous evaluation** | Manual scheduling via cron/CI triggers | Native: `evaluation_rules` + `schedules` on `AIProjectClient` |
| **Custom evaluators** | Any Python callable | Register via ML Client, or use prompt-based `azure_ai_evaluator` type |
| **Grader types** | N/A (evaluators are Python classes) | `string_check`, `model_grader`, `azure_ai_evaluator`, `text_similarity` |
| **Portal support** | Foundry classic portal | Both Foundry classic and Foundry (new) portals |

### Key Syntax Differences — Side by Side

**Data mapping:**
```
v1 (local):  "${data.query}"       "${data.response}"       "${outputs.context}"
v2 (cloud):  "{{item.query}}"      "{{item.response}}"      "{{sample.output_text}}"
```

**Evaluator reference:**
```
v1 (local):  CoherenceEvaluator(model_config=model_config)     # Python class instance
v2 (cloud):  {"evaluator_name": "builtin.coherence", ...}       # String identifier
```

**Submission pattern:**
```
v1 (local):  result = evaluate(data="data.jsonl", evaluators={...})         # Single call
v2 (cloud):  eval_obj = client.evals.create(...)                            # Step 1: define
             eval_run = client.evals.runs.create(eval_id=eval_obj.id, ...)  # Step 2: run
```

---

## When to Use Each Approach

| Scenario | Recommended Approach | Why |
|---|---|---|
| **Quick local prototyping** | v1 (local SDK) | No cloud setup needed, fast iteration |
| **CI/CD pre-deployment gate** | v2 (cloud) OR v1 with `azure_ai_project` | Cloud scales better; v1 can also log to Foundry |
| **Large dataset evaluation (500+ rows)** | v2 (cloud) | No local compute limits; async execution |
| **Continuous post-deployment monitoring** | v2 (cloud) | Native `evaluation_rules` and scheduling |
| **A/B model comparison during migration** | v1 (local) or v2 (cloud) | v1 for quick iteration; v2 for production-grade |
| **Agent evaluation** | v2 (cloud) | Native `azure_ai_agent` target support |
| **Red teaming** | v2 (cloud) | Native `azure_ai_red_team` scenario |

---

## Approach 1: Built-in LLM-as-Judge (Quick, No Extra Dependencies)

Uses `MigrationEvaluator` from `src/evaluate/core.py`. Calls both models, scores outputs with an LLM judge, and generates a comparison report.

```python
from src.evaluate.core import MigrationEvaluator, TestCase

evaluator = MigrationEvaluator(
    source_model="gpt-4o",
    target_model="gpt-4.1",
    test_cases=[
        TestCase(
            prompt="What is Azure OpenAI?",
            system_prompt="You are a helpful assistant.",
            expected_output="Azure OpenAI is...",
        ),
    ],
    metrics=["coherence", "fluency", "relevance"],
)

report = evaluator.run()
report.print_report()
report.save("migration_report.json")
```

---

## Approach 2: Local SDK Evaluation — `azure-ai-evaluation` (v1)

Uses Microsoft's built-in evaluator classes that run **locally in your Python process**. The `evaluate()` function accepts a JSONL file, instantiated evaluator objects, and optional column mappings using `${data.field}` syntax.

### Installation

```bash
pip install azure-ai-evaluation
# For cloud logging support:
pip install azure-ai-evaluation[remote]
```

### Built-in Evaluators Available (v1)

| Category | Evaluators |
|---|---|
| **Quality (AI-assisted)** | `CoherenceEvaluator`, `FluencyEvaluator`, `RelevanceEvaluator`, `SimilarityEvaluator`, `GroundednessEvaluator`, `GroundednessProEvaluator`, `RetrievalEvaluator`, `ResponseCompletenessEvaluator` |
| **Quality (NLP)** | `F1ScoreEvaluator`, `RougeScoreEvaluator`, `GleuScoreEvaluator`, `BleuScoreEvaluator`, `MeteorScoreEvaluator` |
| **Safety** | `ViolenceEvaluator`, `SexualEvaluator`, `SelfHarmEvaluator`, `HateUnfairnessEvaluator`, `IndirectAttackEvaluator`, `ProtectedMaterialEvaluator`, `CodeVulnerabilityEvaluator` |
| **Agent** | `IntentResolutionEvaluator`, `ToolCallAccuracyEvaluator`, `TaskAdherenceEvaluator` |
| **Composite** | `QAEvaluator`, `ContentSafetyEvaluator` |

### Usage Pattern

```python
import os
from azure.ai.evaluation import (
    evaluate,
    CoherenceEvaluator,
    FluencyEvaluator,
    RelevanceEvaluator,
    GroundednessEvaluator,
)

# model_config points to the judge model (not the model being evaluated)
model_config = {
    "azure_endpoint": os.environ["AZURE_OPENAI_ENDPOINT"],
    "api_key": os.environ.get("AZURE_OPENAI_API_KEY"),
    "azure_deployment": os.environ["EVAL_MODEL_DEPLOYMENT"],
}

result = evaluate(
    data="golden_dataset.jsonl",          # JSONL file path
    evaluators={
        "coherence": CoherenceEvaluator(model_config=model_config),
        "fluency": FluencyEvaluator(model_config=model_config),
        "relevance": RelevanceEvaluator(model_config=model_config),
        "groundedness": GroundednessEvaluator(model_config=model_config),
    },
    evaluator_config={
        "default": {
            "column_mapping": {
                "query": "${data.query}",            # <- v1 syntax: ${data.field}
                "response": "${data.response}",
                "context": "${data.context}",
            }
        }
    },
    # Optional: log results to Foundry portal
    azure_ai_project=os.environ.get("AZURE_AI_PROJECT_ENDPOINT"),
)

print(result["metrics"])  # Aggregate scores
print(result["rows"])     # Per-row results
```

### Logging v1 Results to Foundry

Pass `azure_ai_project` to `evaluate()` to upload results:

```python
result = evaluate(
    data="data.jsonl",
    evaluators={...},
    azure_ai_project="https://<account>.services.ai.azure.com/api/projects/<project>",
    evaluation_name="migration-gpt4o-to-gpt41",
    tags={"migration": "gpt-4o-to-gpt-4.1", "environment": "staging"},
)
print(result.studio_url)  # Link to Foundry portal results
```

### A/B Comparison with Local SDK

```python
from src.evaluate.local_eval import quick_evaluate, get_model_config, compare_local

model_config = get_model_config()
evaluator = create_rag_evaluator("gpt-4o", "gpt-4.1")
source_items, target_items = evaluator.collect()

result = compare_local(
    source_items, target_items,
    metrics=["coherence", "fluency", "relevance", "groundedness"],
    model_config=model_config,
    source_label="gpt-4o",
    target_label="gpt-4.1",
)
print(result["summary"])
```

---

## Approach 3: Cloud Evaluation — Azure AI Foundry Evals API (v2)

Runs evaluations **in Azure cloud** using the OpenAI Evals API surfaced through `azure-ai-projects`. Results are always stored in the Foundry project. This approach uses a **two-step pattern**: create an eval definition (schema + testing criteria), then create run(s) against it.

### Installation

```bash
pip install "azure-ai-projects>=2.0.0b1" azure-identity openai
```

### Client Setup

```python
import os
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from openai.types.eval_create_params import DataSourceConfigCustom
from openai.types.evals.create_eval_jsonl_run_data_source_param import (
    CreateEvalJSONLRunDataSourceParam,
    SourceFileContent,
    SourceFileContentContent,
    SourceFileID,
)

project_client = AIProjectClient(
    endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
    credential=DefaultAzureCredential(),
)
client = project_client.get_openai_client()
```

### Step 1: Upload Dataset

```python
data_id = project_client.datasets.upload_file(
    name="migration-eval-dataset",
    version="1.0",
    file_path="./golden_dataset.jsonl",
).id
```

### Step 2: Define Eval with Testing Criteria

```python
model_deployment_name = os.environ["EVAL_MODEL_DEPLOYMENT"]

data_source_config = DataSourceConfigCustom(
    type="custom",
    item_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "response": {"type": "string"},
            "context": {"type": "string"},
            "ground_truth": {"type": "string"},
        },
        "required": ["query", "response"],
    },
)

testing_criteria = [
    {
        "type": "azure_ai_evaluator",
        "name": "coherence",
        "evaluator_name": "builtin.coherence",               # <- v2 syntax: builtin.*
        "initialization_parameters": {
            "deployment_name": model_deployment_name,
        },
        "data_mapping": {
            "query": "{{item.query}}",                         # <- v2 syntax: {{item.field}}
            "response": "{{item.response}}",
        },
    },
    {
        "type": "azure_ai_evaluator",
        "name": "groundedness",
        "evaluator_name": "builtin.groundedness",
        "initialization_parameters": {
            "deployment_name": model_deployment_name,
        },
        "data_mapping": {
            "query": "{{item.query}}",
            "response": "{{item.response}}",
            "context": "{{item.context}}",
        },
    },
    {
        "type": "azure_ai_evaluator",
        "name": "relevance",
        "evaluator_name": "builtin.relevance",
        "initialization_parameters": {
            "deployment_name": model_deployment_name,
        },
        "data_mapping": {
            "query": "{{item.query}}",
            "response": "{{item.response}}",
        },
    },
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
        "name": "f1",
        "evaluator_name": "builtin.f1_score",
        "data_mapping": {
            "response": "{{item.response}}",
            "ground_truth": "{{item.ground_truth}}",
        },
    },
]
```

### Step 3: Create Eval and Run

```python
import time

# Create eval definition
eval_object = client.evals.create(
    name="migration-gpt4o-to-gpt41",
    data_source_config=data_source_config,
    testing_criteria=testing_criteria,
)

# Create a run against the uploaded dataset
eval_run = client.evals.runs.create(
    eval_id=eval_object.id,
    name="run-gpt41-candidate",
    data_source=CreateEvalJSONLRunDataSourceParam(
        type="jsonl",
        source=SourceFileID(type="file_id", id=data_id),
    ),
)

# Poll for completion
while True:
    run = client.evals.runs.retrieve(run_id=eval_run.id, eval_id=eval_object.id)
    if run.status in ("completed", "failed"):
        break
    time.sleep(5)

# Retrieve results
output_items = list(
    client.evals.runs.output_items.list(run_id=run.id, eval_id=eval_object.id)
)
print(f"Report URL: {run.report_url}")
```

### v2 Model Target Evaluation (A/B Testing)

Run the **same evaluation** against different models to compare them directly:

```python
# Same eval definition, different model targets
for model_name in ["gpt-4o", "gpt-4.1", "gpt-5.1"]:
    data_source = {
        "type": "azure_ai_target_completions",
        "source": {"type": "file_id", "id": data_id},
        "input_messages": {
            "type": "template",
            "template": [
                {"type": "message", "role": "user",
                 "content": {"type": "input_text", "text": "{{item.query}}"}}
            ],
        },
        "target": {
            "type": "azure_ai_model",
            "model": model_name,
            "sampling_params": {"max_completion_tokens": 2048},
        },
    }
    eval_run = client.evals.runs.create(
        eval_id=eval_object.id,
        name=f"run-{model_name}",
        data_source=data_source,
    )
```

### v2 Available Built-in Evaluators

Reference these `evaluator_name` values in `testing_criteria`:

| Category | Evaluator Names |
|---|---|
| **Text quality** | `builtin.coherence`, `builtin.fluency`, `builtin.relevance`, `builtin.groundedness`, `builtin.groundedness_pro`, `builtin.similarity`, `builtin.response_completeness` |
| **NLP metrics** | `builtin.f1_score`, `builtin.bleu_score`, `builtin.rouge_score`, `builtin.gleu_score`, `builtin.meteor_score` |
| **Safety** | `builtin.violence`, `builtin.sexual`, `builtin.self_harm`, `builtin.hate_unfairness`, `builtin.indirect_attack`, `builtin.protected_material`, `builtin.code_vulnerability` |
| **Agent** | `builtin.intent_resolution`, `builtin.task_adherence`, `builtin.tool_call_accuracy` |
| **Other graders** | `string_check`, `model_grader` (custom prompt-based), `text_similarity` (embedding-based) |

### v2 Result Interpretation

Each evaluator outputs:

- **`label`**: binary `"pass"` or `"fail"`
- **`score`**: numeric on the evaluator's native scale (1-5 for quality, 0-7 for safety, 0-1 for NLP)
- **`threshold`**: default pass/fail cutoff (user-overridable)
- **`reason`**: LLM-generated explanation of the score

Aggregate results report a **pass rate** per evaluator over the dataset.

---

## v1 (Classic) vs v2 (Cloud) — Which API Am I Using?

Use this **quick reference** if you are looking at existing code:

| If you see... | You are using... |
|---|---|
| `from azure.ai.evaluation import evaluate` | **v1 — Local SDK** |
| `from azure.ai.evaluation import CoherenceEvaluator` | **v1 — Local SDK** |
| `"${data.query}"` in column mappings | **v1 — Local SDK** |
| `evaluator_config` with `column_mapping` | **v1 — Local SDK** |
| `project_client.get_openai_client()` | **v2 — Cloud Evals API** |
| `client.evals.create(...)` | **v2 — Cloud Evals API** |
| `"{{item.query}}"` in data mappings | **v2 — Cloud Evals API** |
| `testing_criteria` with `"evaluator_name": "builtin.*"` | **v2 — Cloud Evals API** |
| `EvaluatorConfiguration` + `EvaluatorIds` | **v1 via `azure-ai-projects` cloud wrapper** (hybrid, uses v1 evaluators in cloud) |

> **NOTE**: The `azure-ai-projects` v1.x SDK also had a cloud evaluation path using `EvaluatorConfiguration` and `EvaluatorIds` enums with `${data.field}` mapping syntax. This is a **hybrid** approach — v1 evaluators run in the cloud. The v2 path (`azure-ai-projects>=2.0.0b1`) uses the OpenAI Evals API with `testing_criteria` and `{{item.field}}` syntax. Do not mix them.

---

## Continuous Evaluation for Model Migration Cost Reduction

The biggest cost in model migrations is **repeated manual evaluation effort** each time a model is retired. Continuous evaluation eliminates this by keeping evaluation infrastructure always running, so when a new model becomes available, you have an instant baseline and can run comparisons immediately.

### Why Continuous Evaluation Reduces Migration Costs

1. **Always-fresh baseline**: scores for the current production model are continuously captured, so you never start a migration from zero
2. **Instant candidate comparison**: run the same eval against the candidate model and compare pass rates immediately
3. **Drift detection**: catch quality degradation between model upgrades (e.g., a prompt that worked on gpt-4o but regresses on gpt-4.1)
4. **Golden dataset maintenance**: production traffic sampled into eval datasets keeps them representative
5. **CI/CD integration**: evaluation gates in your pipeline block bad models from reaching production automatically

### Continuous Evaluation with v2 (Cloud) — Recommended

The `azure-ai-projects` v2 SDK exposes `evaluation_rules` and `schedules` on `AIProjectClient`, enabling:

- **Scheduled evaluations**: run evaluations on a cron schedule against stored completions
- **Evaluation rules**: automatically trigger evaluations when new agent responses are generated
- **Stored completions**: use the Responses API to log production interactions, then evaluate them asynchronously

```python
# Example: schedule recurring evaluation via azure-ai-projects v2
# (API shape may evolve — verify with latest SDK docs)
project_client.evaluation_rules.create(
    name="weekly-migration-readiness",
    evaluators=["builtin.coherence", "builtin.groundedness", "builtin.relevance"],
    schedule={"type": "cron", "expression": "0 9 * * 1"},  # Every Monday 9am
    data_source={"type": "azure_ai_responses", "scenario": "responses"},
)
```

### Continuous Evaluation with v1 (Local) — CI/CD Approach

Use CI/CD pipelines to run v1 local evaluations on a schedule:

```yaml
# GitHub Actions example
name: Model Evaluation
on:
  schedule:
    - cron: '0 9 * * 1'  # Weekly Monday 9am
  workflow_dispatch:      # Manual trigger for migration testing

jobs:
  evaluate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install azure-ai-evaluation
      - run: python -m src.evaluate.local_eval --dataset golden_dataset.jsonl
        env:
          AZURE_OPENAI_ENDPOINT: ${{ secrets.AZURE_OPENAI_ENDPOINT }}
          AZURE_OPENAI_API_KEY: ${{ secrets.AZURE_OPENAI_API_KEY }}
```

### Continuous Eval Migration Workflow

```
                  ONGOING (between migrations)
    ┌─────────────────────────────────────────────────┐
    │  Production traffic → sample → golden dataset   │
    │  Scheduled eval runs → baseline scores captured  │
    │  Drift alerts if scores drop                     │
    └──────────────────────┬──────────────────────────┘
                           │
              NEW MODEL ANNOUNCED (e.g., gpt-5.1)
                           │
    ┌──────────────────────▼──────────────────────────┐
    │  1. Deploy candidate in staging                  │
    │  2. Run SAME eval suite against candidate        │
    │  3. Compare pass rates vs stored baseline        │
    │  4. If pass → canary rollout                     │
    │  5. If fail → tune prompts, re-evaluate          │
    └─────────────────────────────────────────────────┘
```

---

## Acceptance Thresholds

Define pass/fail criteria for your migration:

| Metric | Minimum Acceptable | Action if Below |
|---|---|---|
| Coherence | >= 3.5 / 5 | Block migration |
| Groundedness | >= 4.0 / 5 | Block migration |
| Relevance | >= 3.5 / 5 | Block migration |
| Safety (violence, etc.) | Severity < 2 | Block migration |
| BLEU / ROUGE | Delta <= 10% drop vs current | Investigate, may block |
| Latency P95 | <= 120% of current | Investigate |

## Building a Golden Dataset

Format as JSONL with one record per line:

```jsonl
{"query": "What is our refund policy?", "response": "", "context": "Our refund policy allows...", "ground_truth": "Refunds within 30 days."}
```

**Key fields:**

| Field | Required By |
|---|---|
| `query` | Most evaluators |
| `response` | All evaluators |
| `context` | Groundedness, Retrieval |
| `ground_truth` | Similarity, F1, BLEU, ROUGE |

**Dataset sizing:**

- Smoke test: 10-20 rows
- Pre-production gate: 100-500 rows with diverse intents
- Full regression: 500+ rows sampled from production traffic

You can also load test cases from JSONL:

```python
from src.evaluate.core import load_test_cases

test_cases = load_test_cases("golden_dataset.jsonl")
```

## Custom Prompty Files

System prompts are stored in Prompty format (`.prompty` files) under `src/evaluate/prompts/`:

```python
from src.evaluate.prompts import load_prompty, list_prompty

# List available prompts
print(list_prompty())  # ['classify_category', 'classify_intent', 'rag', 'tool_calling', ...]

# Load a specific prompt
prompty = load_prompty("rag")
print(prompty["system_prompt"])
```

## Environment Variables Required

| Variable | Used By | Description |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | v1 + v2 | Azure OpenAI resource endpoint |
| `AZURE_OPENAI_API_KEY` | v1 (optional with Entra ID) | API key for the judge model |
| `EVAL_MODEL_DEPLOYMENT` | v1 + v2 | Deployment name for the judge/evaluator model |
| `AZURE_AI_PROJECT_ENDPOINT` | v2 (required), v1 (optional) | Azure AI Foundry project endpoint |
| `GPT4O_DEPLOYMENT` | Both | Deployment for gpt-4o model |
| `GPT41_DEPLOYMENT` | Both | Deployment for gpt-4.1 model |
| `GPT51_DEPLOYMENT` | Both | Deployment for gpt-5.1 model |

## Must Not

- Deploy a new model to production without running evaluation first
- Use fewer than 10 test cases for any evaluation — results will not be statistically meaningful
- Skip safety evaluators during model upgrades
- Commit API keys or evaluation data containing PII
- Ignore regressions flagged by the evaluation report
- Compare models using different system prompts — keep prompts identical for fair comparison
- Mix v1 `${data.field}` syntax with v2 `{{item.field}}` syntax — they are incompatible
- Use `EvaluatorConfiguration` / `EvaluatorIds` (v1 cloud wrapper) when targeting the v2 OpenAI Evals API
- Skip continuous evaluation setup — one-off evals increase migration costs for every model generation

## References

- [Azure AI Evaluation SDK (v1) — Python Reference](https://learn.microsoft.com/python/api/overview/azure/ai-evaluation-readme)
- [Local Evaluation with azure-ai-evaluation (v1)](https://learn.microsoft.com/azure/ai-foundry/how-to/develop/evaluate-sdk?view=foundry-classic)
- [Cloud Evaluation with Foundry SDK (v2)](https://learn.microsoft.com/azure/ai-foundry/how-to/develop/cloud-evaluation?view=foundry-classic)
- [Azure OpenAI Evals API (v2)](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/evaluations?view=foundry-classic)
- [Evals API REST Reference](https://learn.microsoft.com/azure/ai-foundry/openai/authoring-reference-preview#evaluation---create)
- [Built-in Evaluators Catalog](https://learn.microsoft.com/azure/ai-foundry/concepts/built-in-evaluators?view=foundry-classic)
- [Custom Evaluators Guide](https://learn.microsoft.com/azure/ai-foundry/concepts/evaluation-evaluators/custom-evaluators?view=foundry-classic)
- [Continuous Evaluation for Agents](https://learn.microsoft.com/azure/ai-foundry/how-to/continuous-evaluation-agents?view=foundry-classic)
- [azure-ai-projects PyPI (v2)](https://pypi.org/project/azure-ai-projects/)
- [azure-ai-evaluation PyPI (v1)](https://pypi.org/project/azure-ai-evaluation/)
