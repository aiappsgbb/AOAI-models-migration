# Getting Started

This guide walks you through setting up the repository and running your first model migration evaluation.

---

## Prerequisites

1. **Azure OpenAI Resource** with deployments for your source and target models (e.g., `gpt-4o` and `gpt-4.1`)
2. **Python 3.10+**
3. **Azure CLI** installed and authenticated

---

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/aiappsgbb/AOAI-models-migration.git
cd AOAI-models-migration
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `openai` — Azure OpenAI SDK (for both v1 and versioned API)
- `azure-identity` — Entra ID authentication
- `azure-ai-evaluation` — Local evaluation SDK
- `azure-ai-projects` — Azure AI Foundry cloud evaluation
- `python-dotenv` — Environment variable loading
- `pandas` — Data handling for evaluation reports
- `pyyaml` — Prompty file parsing

### 3. Configure Environment Variables

Copy the example file and fill in your values:

```bash
cp .env_example .env
```

Edit `.env` with your Azure OpenAI resource details:

```env
# Required
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-02-15-preview

# Source model (what you're migrating FROM)
GPT4O_DEPLOYMENT=gpt-4o

# Target models (what you're migrating TO — configure whichever you're testing)
GPT41_DEPLOYMENT=gpt-4.1
GPT41_MINI_DEPLOYMENT=gpt-4.1-mini
GPT5_DEPLOYMENT=gpt-5
GPT51_DEPLOYMENT=gpt-5.1
GPT52_DEPLOYMENT=gpt-5.2

# Reasoning models
O3_DEPLOYMENT=o3
O4_MINI_DEPLOYMENT=o4-mini

# For Azure AI Foundry cloud evaluation (optional)
AZURE_AI_PROJECT_CONNECTION_STRING=your-connection-string
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-4o  # judge model for cloud eval
```

> **Tip:** You only need to configure the source and target models you're actually testing. You don't need all deployments.

### 4. Authenticate with Azure

```bash
az login
```

This repo uses **Microsoft Entra ID** (Azure AD) token-based authentication — no API keys needed:

```python
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(),
    "https://cognitiveservices.azure.com/.default"
)
```

The `src/clients.py` module handles this automatically via `create_client()`.

---

## Quick Start: Your First Migration Test

### Option 1: Run a Pre-built Scenario

The fastest way to validate a migration — run one of the pre-built evaluation scenarios:

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

This will:
1. Send 8 RAG test cases to both models
2. Score each response on Groundedness, Relevance, and Coherence
3. Print a comparison report

### Option 2: Use the Notebooks

For an interactive, step-by-step experience:

1. **Technical migration** — Start with [azure_openai_migration_technical.ipynb](../azure_openai_migration_technical.ipynb) to understand API changes and test client configuration.
2. **Evaluation** — Then run [azure_openai_evaluation_guide.ipynb](../azure_openai_evaluation_guide.ipynb) to compare model quality.

```bash
jupyter notebook azure_openai_migration_technical.ipynb
```

### Option 3: Call Models Directly

Use the helper functions to call any model with automatic parameter adaptation:

```python
from src.clients import create_client, call_model

# Works with any model — handles v1 API, parameter adaptation, and auth automatically
response = call_model(
    model_name="gpt-5.1",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain quantum computing in one paragraph."}
    ],
    max_tokens=200,
    temperature=0.7,
)
print(response)
```

> `call_model()` automatically:
> - Picks the right client (`AzureOpenAI` vs `OpenAI`)
> - Converts `max_tokens` → `max_completion_tokens` for v1 models
> - Removes `temperature`/`top_p` for reasoning models
> - Swaps `system` → `developer` role where needed

---

## Project Structure

| Path | Description |
|------|-------------|
| `src/config.py` | Model classification helpers (`is_v1`, `is_reasoning`, `is_o_series`), environment loading |
| `src/clients.py` | Client factory (`create_client`), universal `call_model()` with automatic parameter adaptation |
| `src/evaluate/core.py` | `MigrationEvaluator` class, LLM-as-Judge, comparison reports |
| `src/evaluate/local_eval.py` | Local SDK evaluation using `azure-ai-evaluation` |
| `src/evaluate/foundry.py` | Azure AI Foundry cloud evaluation (`FoundryEvalsClient`) |
| `src/evaluate/scenarios/` | Pre-built evaluation scenarios (RAG, tool calling, translation, classification) |
| `src/evaluate/prompts/` | System prompts in Prompty format (`.prompty` files) |

---

## Troubleshooting

### "Model not found" or "DeploymentNotFound"

Your `.env` deployment names must match exactly what's in your Azure OpenAI resource. Check with:

```bash
az cognitiveservices account deployment list \
  --name YOUR_RESOURCE_NAME \
  --resource-group YOUR_RG \
  --query "[].{name:name, model:properties.model.name}" \
  -o table
```

### "AuthenticationError" or "401"

Make sure you're logged in and have the right role:

```bash
az login
# Verify your identity
az account show
```

You need the **Cognitive Services OpenAI User** role on the Azure OpenAI resource.

### "Temperature is not supported"

You're calling a reasoning model (GPT-5 family, o-series) with `temperature`. Use `call_model()` which strips unsupported parameters automatically, or remove `temperature` and `top_p` from your request.

### Import errors

Make sure you're running from the repository root and dependencies are installed:

```bash
cd AOAI-models-migration
pip install -r requirements.txt
python -c "from src import is_v1, is_reasoning; print('OK')"
```

---

## Next Steps

- **[Migration Paths](migration-paths.md)** — choose your target model
- **[API Changes](api-changes-by-model.md)** — understand the code changes needed
- **[Evaluation Guide](evaluation-guide.md)** — run a full evaluation before deploying
- **[Lifecycle Best Practices](llm-upgrade-lifecycle-best-practices.md)** — enterprise-grade migration lifecycle

## Migration Checklist

A step-by-step checklist for teams starting their first model migration:

1. **Align on methodology** — share the [Evaluation Guide](evaluation-guide.md) with stakeholders so everyone understands the hybrid (E2E + task-level) approach.
2. **Choose a data strategy** — decide between real production logs ([Stored Completions / APIM](building-golden-datasets.md)) or synthetic generation. Both are supported.
3. **Pilot one use case** — pick a single production app, build a golden dataset (10–20 cases for a smoke test), and run [`test_e2e.py`](../samples/rag_pipeline/) against it.
4. **Scale golden datasets** — extend to additional use cases. Enable [CI/CD automation](../docs/cloud-eval-tracking-across-models.md) for nightly regression detection.
5. **Set up dashboards** — upload evaluation results to [Azure AI Foundry](cloud-eval-tracking-across-models.md) for ongoing monitoring and cross-run comparison.
6. **Establish central visibility** — for organizations with many AI workloads, use one Foundry eval definition per agent or build a [Fabric + Power BI](cloud-eval-tracking-across-models.md) dashboard for cross-org quality tracking.
