# Azure OpenAI Model Migration Evaluation Framework

A comprehensive evaluation framework for migrating production systems between Azure OpenAI model generations (e.g. GPT-4.1 → GPT-5.2).  Features a full web UI with multi-topic management, AI-powered prompt & test-data generation (with dynamic per-topic category taxonomies using readable `snake_case` codes), deep batch evaluation across classification/dialog/general scenarios, side-by-side model comparison with statistical significance, versioned prompt history, a test-data explorer/editor, rich narrative verbose logging, token & cost analytics, consistency/reproducibility testing, and persistent results with filtering & deletion. 

---

## 🎯 Overview

When you upgrade a model deployment in Azure AI Foundry — from GPT-4.1 to GPT-5.2, for example — you need to answer questions like:

- *"Does the new model still classify tickets correctly?"*
- *"Is latency better or worse?"*
- *"Do my prompts need to be rewritten?"*

This framework automates that process end-to-end:

1. **Generate** domain-specific prompts + synthetic test data for any topic via AI.
2. **Evaluate** each model independently against classification, dialog, and general scenarios.
3. **Compare** two models head-to-head with quantified metrics and significance levels.
4. **Browse** saved results, filter by type, inspect details, and delete old runs.
5. **Manage** multiple topics — archive, switch, restore, or **import** your own prompt + data sets.

### Key Capabilities

| Area | Highlights |
|------|------------|
| **Multi-Model** | Configure unlimited models in `settings.yaml` (GPT-4.1, GPT-5.2, GPT-5, o-series, etc.) |
| **Multi-Topic** | Switch between self-contained topic archives (prompts + data) without losing anything |
| **AI Generation** | One-click generation of 4 optimised prompts + 3 test datasets tailored to any domain, with dynamic category taxonomy and JSON retry logic |
| **Topic Import** | Import your own GPT-4 prompts + test data from disk (web UI or CLI) — GPT-5 prompts are auto-generated and the topic is archived ready to activate |
| **Classification** | Accuracy, F1, precision, recall, subcategory/priority/sentiment accuracy, confidence calibration, confusion matrix |
| **Dialog** | Follow-up quality, context coverage, rule compliance, empathy score, optimal similarity, resolution efficiency, consistency |
| **General** | Format compliance, completeness, reasoning, safety, structured output |
| **Token & Cost** | Per-request token breakdown (prompt/completion/cached/reasoning), cost estimation, cache hit rate, throughput (tok/s) |
| **Consistency** | Multi-run reproducibility scoring, response variance, format consistency |
| **Model Comparison** | Dimension-by-dimension comparison with statistical significance (Welch's t-test) and actionable recommendations |
| **Prompt Versioning** | Every save creates a timestamped snapshot — preview, restore, or delete any version |
| **Test Data Editor** | View and edit raw test scenarios in-browser for each evaluation type |
| **Results Persistence** | Evaluations and comparisons auto-save to disk — browse, filter, inspect, and delete from the UI |
| **Verbose Logging** | Rich narrative verbose mode with colour-coded entries (step/ok/warn/err/detail/head) and timestamped progress feed |
| **Foundry Control Plane** | Optional LLM-as-judge evaluation via Microsoft Foundry Runtime — coherence, fluency, relevance, task adherence, intent resolution — with results visible in the Foundry dashboard |
| **Copilot Studio UI** | Fluent 2 design system inspired by Microsoft Copilot Studio — top header bar, collapsible sidebar, brand-blue palette, flat controls, Segoe UI typography |
| **Auto-Detection** | SDK automatically uses `max_completion_tokens` for GPT-5/o-series models |

---

## 📁 Project Structure

```
model_migration_eval/
├── app.py                          # Main entry point (CLI + web server)
├── start.bat                       # Quick-launch script (Windows)
├── requirements.txt                # Python dependencies
├── .env.example                    # Environment variables template
├── .gitignore                      # Git ignore rules
├── Dockerfile                      # Container image (Python 3.13-slim + Flask)
├── .dockerignore                   # Files excluded from Docker build context
├── deploy.ps1                      # One-script deployment (Docker Desktop or Azure)
│
├── config/
│   ├── settings.yaml               # Azure credentials & model definitions
│   └── model_params.yaml           # Model parameter reference table
│
├── data/
│   ├── synthetic/                  # Active synthetic evaluation datasets
│   │   ├── classification/         #   Classification scenarios (20)
│   │   ├── dialog/                 #   Follow-up dialog samples (15)
│   │   ├── general/               #   General capability tests (15)
│   │   └── topics/                #   ⬅ Archived topic datasets
│   │       ├── red_sea_diving_travel/
│   │       ├── specialized_agent_.../  # aeronautics
│   │       └── telco_customer_service/
│   └── results/                    # Auto-saved evaluation & comparison JSON files
│
├── prompts/                        # ⬅ Prompt templates (editable on disk or via UI)
│   ├── gpt4/                       #   GPT-4 optimised prompts
│   │   ├── classification_agent_system.md
│   │   └── dialog_agent_system.md
│   ├── gpt5/                       #   GPT-5 optimised prompts
│   │   ├── classification_agent_system.md
│   │   └── dialog_agent_system.md
│   ├── history/                    #   Version history (auto-managed)
│   │   └── versions.json
│   └── topics/                     #   ⬅ Archived topic prompts
│       ├── red_sea_diving_travel/
│       ├── specialized_agent_.../  # aeronautics
│       └── telco_customer_service/
│
├── src/
│   ├── clients/
│   │   └── azure_openai.py         # Azure OpenAI client (sync/async/streaming)
│   ├── evaluation/
│   │   ├── metrics.py              # MetricsCalculator — classification, dialog quality, latency, cost, consistency
│   │   ├── evaluator.py            # ModelEvaluator + EvaluationResult (classification/dialog/general)
│   │   ├── comparator.py           # ModelComparator + ComparisonReport with statistical significance
│   │   └── foundry_evaluator.py    # Microsoft Foundry Control Plane integration (optional)
│   ├── utils/
│   │   ├── prompt_loader.py        # PromptLoader — template loading with caching
│   │   ├── prompt_manager.py       # PromptManager — editing, versioning, AI gen, topics
│   │   └── data_loader.py          # DataLoader — synthetic scenario loading
│   └── web/
│       ├── routes.py               # Flask API routes (1500+ lines, 46 routes)
│       └── templates/
│           ├── _fluent_head.html    # Fluent 2 design system (CSS tokens, Tailwind config, component classes)
│           ├── _sidebar.html        # Top header bar + collapsible left sidebar navigation
│           ├── index.html           # Dashboard — quick single-prompt test
│           ├── evaluate.html        # Batch evaluator with verbose mode
│           ├── compare.html         # Model comparison with charts
│           ├── results.html         # Results browser with filters & delete
│           ├── prompts.html         # Prompt Manager (edit, generate, history, data explorer)
│           └── prompts_V1.html      # Prompt Manager (previous layout version)
│
├── tools/
│   ├── import_topic.py              # CLI tool: import external topic from GPT-4 prompt + test data
│   ├── regenerate_all_topics.py     # Regenerate prompts + test data for all archived topics
│   ├── gpt4_classification_prompt.md # Sample GPT-4 classification prompt for import testing
│   ├── gpt4_dialog_prompt.txt       # Sample GPT-4 dialog prompt for import testing
│   ├── test_data_classification.json # Sample classification test data for import testing
│   ├── test_data_dialog.json        # Sample dialog test data for import testing
│   ├── test_data_general.json       # Sample general test data for import testing
│   └── test_import.bat              # Quick-launch script for import testing
│
├── docs/
│   ├── migration_guide.md          # Comprehensive GPT-4 → GPT-5 migration guide
│   ├── prompt_design.md            # Prompt engineering best practices
│   └── security_guide.md           # Security & governance
│
└── logs/                           # Application log files
```

---

## 🚀 Quick Start

### 1. Setup Environment

```bash
# Create virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
copy .env.example .env        # Windows
# cp .env.example .env        # Linux/macOS
```

Edit `.env` and set your Azure OpenAI credentials:

```dotenv
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your-api-key-here
FOUNDRY_PROJECT_ENDPOINT=https://your-hub.services.ai.azure.com/api/projects/your-project  # Optional
```

### 2. Configure Models

Edit `config/settings.yaml`.  The `endpoint` and `api_key` fields use `${VAR}` syntax to read from `.env` automatically:

```yaml
azure:
  endpoint: "${AZURE_OPENAI_ENDPOINT}"
  api_key:  "${AZURE_OPENAI_API_KEY}"
  api_version: "2025-04-01-preview"

  models:
    gpt4:
      deployment_name: "gpt-4.1"          # Your GPT-4 deployment name
      model_version: "2024-08-06"
      max_tokens: 4096
      temperature: 0.1

    gpt5:
      deployment_name: "gpt-5.2"          # Your GPT-5 deployment name
      model_version: "2025-01-01"
      max_tokens: 8192
      temperature: 0.1
```

You can add as many models as you need (see [Model Configuration](#-model-configuration) below).

### 3. Launch the Web Interface

```bash
# Default: http://127.0.0.1:5000
python app.py

# Custom host/port
python app.py web --host 0.0.0.0 --port 5001 --debug
```

Open your browser and navigate to the URL shown in the terminal.

---

## 🖥️ Web Interface

The UI follows the **Microsoft Copilot Studio** visual language — a **Fluent 2** design system with a brand-blue palette (`#0F6CBD`), Segoe UI typography, and modern flat controls.

### Layout

| Element | Description |
|---------|-------------|
| **Top header bar** | 48 px, light grey (`#F0F0F0`), brand logo, app title, active topic badge, and "Azure OpenAI" environment label |
| **Left sidebar** | Icon-only rail (48 px) that expands to 220 px on hover; 5 navigation links + Settings gear; active page indicated by a 3 px blue accent bar |
| **Content area** | Offset by header + sidebar; neutral surface background (`#FAF9F8`); Fluent cards, inputs, badges, and buttons throughout |

### Tab Overview

| Tab | Icon | URL | Purpose |
|-----|------|-----|---------|
| **Dashboard** | 🏠 | `/` | Quick single-prompt evaluation — enter a prompt, pick models, see responses side-by-side |
| **Evaluate** | 📊 | `/evaluate` | Batch evaluation of a single model across all test scenarios for a given type |
| **Compare** | ⚖️ | `/compare` | Head-to-head comparison of two models with dimension-by-dimension charts |
| **Results** | 📋 | `/results` | Browse, filter, inspect, and delete all saved evaluation/comparison results |
| **Prompts** | ✏️ | `/prompts` | Full prompt lifecycle: view, edit, AI-generate, version history, and test data explorer |

### Verbose Mode

Every processing button (evaluate, compare, generate) has an optional **☑ Verbose** checkbox next to it.  When enabled, a rich **narrative feed panel** appears below the button with colour-coded entries:

| Entry Type | Colour | Usage |
|------------|--------|-------|
| **step** | Blue | Processing steps ("Sending scenario 3/20 to GPT-5…") |
| **ok** | Green | Successful outcomes ("Scenario classified correctly ✓") |
| **warn** | Yellow | Mismatches or off-target results |
| **err** | Red | Errors and failures |
| **detail** | Gray | Per-scenario narrative with metrics breakdown |
| **head** | Brand blue | Summary blocks with aggregated headline metrics |

Each entry is timestamped.  For **classification**, each scenario shows expected vs. predicted category, confidence, latency, token breakdown (prompt/completion/cached), and subcategory/priority/sentiment match status.  For **dialog**, each scenario shows category, context gaps, question count vs. expected turns (on-target ✓ / off-target ⚠), response excerpt, latency, and full token detail.  A final summary block aggregates quality, consistency, latency, cost, and throughput.

### Dashboard (`/`)

- Enter a user prompt or pick a pre-loaded test scenario.
- Select one or two models from the configured list.
- Get instant responses with latency and token usage.
- Quick health check to verify Azure connectivity.

### Evaluate (`/evaluate`)

1. Select a **model** and **evaluation type** (classification, dialog, or general).
2. Optionally enable **☑ Verbose** for detailed narrative logging and/or **☑ Include Foundry LLM-as-judge** for LLM-quality evaluation via Microsoft Foundry.
3. Click **▶ Run Evaluation**.
4. The system sends every test scenario through the model and computes metrics.
5. Results are displayed with **12 summary metric cards** per type and **auto-saved** to `data/results/`.

**Classification metric cards (12):** Accuracy, F1 Score, Avg Latency, Consistency, Subcategory Accuracy, Priority Accuracy, Sentiment Accuracy, Cost/Request, Cache Hit Rate, Reasoning Token %, Avg Confidence, Tokens/sec.

**Dialog metric cards (12):** Follow-up Quality, Context Coverage, Rule Compliance, Empathy Score, Optimal Similarity, Resolution Efficiency, Consistency, Avg Latency, P95 Latency, Cost/Request, Cache Hit Rate, Tokens/sec.

**General metric cards (4):** Format Compliance, Completeness, Avg Latency, P95 Latency.

Each metric card has an **ⓘ info tooltip** button explaining what the metric measures and how it's calculated.

### Compare (`/compare`)

1. Select **Model A** (baseline) and **Model B** (candidate), plus the evaluation type.
2. Optionally enable **☑ Verbose** and/or **☑ Include Foundry LLM-as-judge**.
3. Click **▶ Run Comparison** — the comparison runs **asynchronously** in a background thread while the UI polls for progress (avoids ACA Envoy proxy timeout limits).
4. See dimension-by-dimension results with percentage change, significance levels, and a bar chart.
5. The report includes an overall winner and actionable recommendations.
6. Comparisons are **auto-saved** to `data/results/`.

### Results (`/results`)

- Lists all saved evaluation and comparison JSON files, sorted newest first.
- **Filter** by type: Classification, Dialog, General, or Comparison.
- **Count badge** shows how many results match the current filter.
- Click any result to open a **detail modal** with:
  - For evaluations: model, type, scenario count, classification metrics (accuracy/F1/precision/recall), latency metrics (mean/median/P95/stddev).
  - For comparisons: model A vs B, dimension table (values + % change + significance), winner, recommendations.
  - Collapsible **Raw JSON** section.
- **🗑️ Delete** individual results directly from the list.

### Prompts (`/prompts`)

The Prompts page has four sub-tabs:

| Sub-Tab | Purpose |
|---------|---------|
| **View / Edit** | Read and edit the active prompt template for any model/type combination |
| **✨ AI Generate** | Generate all 4 prompts + 3 test datasets for a new topic in one click |
| **Version History** | Filter, preview, restore, or delete (single/bulk) any past prompt version |
| **Test Data** | Browse and edit raw test scenarios (classification/dialog/general) with inline JSON editor |

Additionally, the left sidebar includes an **📥 Import Topic** panel (see [Importing External Topics](#importing-external-topics) below).

---

## 🏷️ Topic Management

Topics are self-contained packages of prompts + test data.  The system supports:

- **Active topic** — The current set of prompts and test data in `prompts/` and `data/synthetic/`.
- **Archived topics** — Previous topic snapshots stored under `prompts/topics/<name>/` and `data/synthetic/topics/<name>/`.

### Topic Workflow

```
 ┌──────────────┐    archive     ┌──────────────────┐
 │  Active       │ ────────────► │  prompts/topics/  │
 │  prompts/     │               │  data/.../topics/ │
 │  data/        │ ◄──────────── │                   │
 └──────────────┘    activate    └──────────────────┘
```

1. **Generate a new topic** — Use the AI Generate panel on the Prompts page.  This replaces the active prompts and data.
2. **Import an external topic** — Upload your own GPT-4 prompt(s) + test data via the 📥 Import panel or the CLI tool.  A GPT-5 prompt is auto-generated and everything is saved as an archived topic.
3. **Archive the current topic** — Before generating a new one, the current topic is auto-archived (or you can manually archive via the API).
4. **Switch topics** — Activate any archived topic from the topic selector.  The active set is swapped out and the selected archive becomes active.
5. **Delete an archive** — Remove an old topic you no longer need.

### Data Sync Detection

When prompts and test data were generated for different topics, the system detects the mismatch.  The Evaluate page shows a warning with a button to **regenerate** test data for the current topic.

---

## ✏️ Prompt Management

### Edit Prompts on Disk

Active prompts are plain Markdown files under `prompts/`:

```
prompts/
├── gpt4/
│   ├── classification_agent_system.md   ← GPT-4 classification prompt
│   └── dialog_agent_system.md           ← GPT-4 dialog prompt
└── gpt5/
    ├── classification_agent_system.md   ← GPT-5 classification prompt
    └── dialog_agent_system.md           ← GPT-5 dialog prompt
```

The entire file content is sent as the `system` message.  Changes take effect on the next API call — no server restart needed when editing via the UI.

**Naming convention:**  `<task>_agent_system.md`, where `<task>` is `classification` or `dialog`.

### Edit Prompts from the Web UI

1. Navigate to **Prompts** → **View / Edit**.
2. Select a model and prompt type.
3. Click **✏️ Edit**, modify the content, click **💾 Save**.
4. A version snapshot is created automatically.

### AI-Powered Generation

1. Go to **Prompts** → **✨ AI Generate**.
2. Enter a **topic** (e.g. *"Soporte técnico de telecomunicaciones"*, *"Paris 7-day travel itinerary"*).
3. Select the **generator model**.
4. Click **Generate Prompts + Test Data**.

Generation runs **asynchronously** in a background thread (HTTP 202 + polling) — the UI shows an elapsed-time counter and animated progress.  This avoids ACA Envoy proxy timeout limits on long-running requests.

This generates in one go:

| Output | Description |
|--------|-------------|
| `gpt4/classification_agent_system.md` | Classification prompt optimised for GPT-4 (explicit CoT, verbose rules) |
| `gpt4/dialog_agent_system.md` | Dialog prompt optimised for GPT-4 |
| `gpt5/classification_agent_system.md` | Classification prompt optimised for GPT-5 (native reasoning, concise) |
| `gpt5/dialog_agent_system.md` | Dialog prompt optimised for GPT-5 |
| `data/synthetic/classification/*.json` | 20 classification scenarios with categories, sentiments, priorities |
| `data/synthetic/dialog/*.json` | 15 multi-turn dialog scenarios |
| `data/synthetic/general/*.json` | 15 general capability tests |

All content is domain-adapted and coherent — the test data exercises the exact categories defined in the prompts.

### Importing External Topics

If you already have your own GPT-4 system prompt and test data, you can import them directly — the framework will generate the GPT-5 optimised prompt automatically and create an archived topic ready to activate.

#### From the Web UI

1. Go to **Prompts** → sidebar → **📥 Import Topic**.
2. Enter a **topic name** (e.g. *"Insurance Claims Processing"*).
3. Upload one or both GPT-4 prompts:
   - **Classification prompt** (`.txt` / `.md`)
   - **Dialog prompt** (`.txt` / `.md`)
4. Upload one or more test data files:
   - **Classification scenarios** (`.json`)
   - **Dialog scenarios** (`.json`)
   - **General capability tests** (`.json`)
5. Select the **generator model** for GPT-5 prompt creation.
6. Click **📥 Import Topic**.

The system validates the prompt(s) and test data, generates a GPT-5 version for each prompt, and writes everything as an archived topic.  Activate it from the topic selector to start running evaluations.

#### From the CLI

Use the standalone `tools/import_topic.py` script:

```bash
# Classification prompt + three test data files
python tools/import_topic.py \
    --topic "Insurance Claims Processing" \
    --gpt4-class-prompt my_cls_prompt.txt \
    --class-test-data classification_data.json \
    --dialog-test-data dialog_data.json \
    --general-test-data general_data.json

# Dialog prompt only
python tools/import_topic.py \
    --topic "Hotel Concierge" \
    --gpt4-dialog-prompt hotel_prompt.txt \
    --dialog-test-data hotel_scenarios.json

# Both prompts
python tools/import_topic.py \
    --topic "Retail Support" \
    --gpt4-class-prompt cls.txt \
    --gpt4-dialog-prompt dlg.txt \
    --class-test-data cls.json \
    --general-test-data gen.json
```

**CLI Parameters:**

| Parameter | Required | Description |
|-----------|:--------:|-------------|
| `--topic` | ✅ | Human-readable topic name |
| `--gpt4-class-prompt` | ★ | GPT-4 classification system prompt file |
| `--gpt4-dialog-prompt` | ★ | GPT-4 dialog system prompt file |
| `--class-test-data` | ★ | Classification scenarios JSON |
| `--dialog-test-data` | ★ | Dialog scenarios JSON |
| `--general-test-data` | ★ | General capability tests JSON |
| `--generator-model` | — | Model for GPT-5 generation (default: `gpt5`) |
| `--force` | — | Overwrite if topic already exists |
| `--verbose` | — | Enable debug logging |

> ★ At least one prompt file **and** at least one test data file are required.

#### What happens during import

1. Each GPT-4 prompt is validated — if it lacks the output format block required by the evaluation pipeline, it's appended automatically.
2. A GPT-5 optimised version is generated for each prompt, preserving the same category taxonomy.
3. Test data is validated and missing optional fields are auto-filled.
4. Everything is written to the archive structure:
   - `prompts/topics/<slug>/gpt4/` and `gpt5/` — prompt files
   - `data/synthetic/topics/<slug>/` — test data by type
   - `topic.json` — metadata

#### Category Naming Convention

Generated prompts and test data always use **descriptive `snake_case` category codes** (e.g. `billing_inquiry`, `flight_operations`, `safety_compliance`).  Short acronym codes like `BILL`, `PKG`, `TECH` are never used.

Categories are **invented dynamically** for each topic — the generator creates 5-7 domain-specific categories that naturally fit the subject.  This means every topic gets its own tailored taxonomy rather than a fixed set of categories.

#### JSON Sanitisation & Retry

When generating test data, the system includes automatic JSON sanitisation (trailing commas, comments, double commas) and retry logic (up to 3 attempts with re-prompting) to handle models that occasionally return imperfect JSON.

**Minimum count validation:** If the model returns valid JSON but with fewer than 50% of the requested scenarios (e.g. 2 instead of 15), the system automatically retries with a reinforced prompt that explicitly demands the exact target count.  This prevents silently accepting under-populated datasets.

### Version History

Every prompt change creates a versioned snapshot in `prompts/history/`:

```
prompts/history/
├── versions.json                           ← Version index (JSON array)
├── gpt4__classification__20260214_1030.md  ← Snapshot files
└── ...
```

From the **Version History** tab you can:

- **Filter** by model, prompt type, or topic.
- **Preview** any version's content.
- **♻️ Restore** a version as the active prompt.
- **🗑️ Delete** individual versions or **bulk-delete** a selection.

---

## ⚙️ Model Configuration

Edit the `models` section in `config/settings.yaml`.  Each key becomes a model name used in the CLI, API, and web UI.

### Example: 3-Model Setup

```yaml
azure:
  endpoint: "${AZURE_OPENAI_ENDPOINT}"
  api_key:  "${AZURE_OPENAI_API_KEY}"
  api_version: "2025-04-01-preview"

  models:
    gpt4:
      deployment_name: "gpt-4.1"
      model_version: "2024-08-06"
      max_tokens: 4096
      temperature: 0.1

    gpt5:
      deployment_name: "gpt-5.2"
      model_version: "2025-01-01"
      max_tokens: 8192
      temperature: 0.1

    gpt5_reasoning:
      deployment_name: "gpt-5"
      model_version: "2025-01-01"
      max_tokens: 16384
      reasoning_effort: "medium"    # low, medium, high (o-series / gpt-5)
```

### Parameters

| Parameter | Description | Notes |
|-----------|-------------|-------|
| `deployment_name` | Deployment name in Azure AI Foundry | As shown in Azure Portal → Deployments |
| `model_version` | Model version string | From deployment details |
| `max_tokens` | Maximum response tokens | Model-dependent |
| `temperature` | 0.0–2.0 (lower = more deterministic) | 0.1 recommended for classification |
| `reasoning_effort` | Only for reasoning models | `low` / `medium` / `high` — GPT-5, o1, o3, o4 |

> **Auto-detection:** The client automatically uses `max_completion_tokens` instead of `max_tokens` for models that require it (GPT-5, o1, o3, o4 series).

### Adding Models

The key names are arbitrary.  Add as many as you need:

```yaml
    baseline:
      deployment_name: "gpt-4o"
      max_tokens: 4096
      temperature: 0.1

    candidate:
      deployment_name: "gpt-5.2"
      max_tokens: 16384
      temperature: 0.1
```

---
## ☁️ Microsoft Foundry Control Plane Evaluation

The framework optionally integrates with [Microsoft Foundry](https://ai.azure.com/) to complement local metrics with **LLM-as-judge** quality evaluators.  Results appear in the Foundry Control Plane dashboard alongside your other AI evaluations.

### Architecture: Dual-Write

```
 Local evaluation (fast, free)           Foundry Runtime (LLM-as-judge)
 ┌──────────────┐                        ┌──────────────────────────┐
 │ evaluator.py │──► metrics.py ──►  UI  │  Foundry Control Plane   │
 │ (sklearn,    │       │                │  ┌────────────────────┐  │
 │  numpy)      │       │                │  │ coherence          │  │
 └──────────────┘       │                │  │ fluency            │  │
                        ▼                │  │ relevance          │  │
                 foundry_evaluator.py ──►│  │ task_adherence     │  │
                  export JSONL           │  │ similarity         │  │
                  upload dataset         │  │ intent_resolution  │  │
                  create eval + run      │  │ response_complete. │  │
                                         │  └────────────────────┘  │
                                         │                          │
                                         │   📊 report_url          │
                                         └──────────────────────────┘
```

**Local metrics stay intact** — latency, cost, consistency, classification accuracy (sklearn), empathy/rule heuristics.  Foundry **adds** semantic quality metrics that an LLM evaluates (coherence, fluency, relevance, task adherence).

### Prerequisites

| Requirement | Details |
|-------------|---------|
| **Python package** | `pip install 'azure-ai-projects>=2.0.0b2'` (already in `requirements.txt`) |
| **Foundry Project** | Create a project in [Azure AI Foundry](https://ai.azure.com/) |
| **Judge model deployment** | Deploy a model (e.g. `gpt-4.1`) in the Foundry project — this model runs the LLM-as-judge evaluations |
| **Azure credentials** | `DefaultAzureCredential` — works with Azure CLI (`az login`), Managed Identity, or Service Principal (auto-created by `deploy.ps1`) |
| **RBAC** | Your identity (or the Service Principal) needs **Contributor** role on the Foundry project to upload datasets and create evaluations |

### Setup

1. **Install the SDK** (if not already done):

   ```bash
   pip install 'azure-ai-projects>=2.0.0b2'
   ```

2. **Configure `settings.yaml`** — add the `foundry` section:

   ```yaml
   foundry:
     project_endpoint: "${FOUNDRY_PROJECT_ENDPOINT}"
     judge_deployment: "gpt-5.2"   # Model used as LLM judge
     grader_model: "gpt-4.1"      # Model used for grading metrics
   ```

   Add `FOUNDRY_PROJECT_ENDPOINT` to your `.env`:

   ```dotenv
   FOUNDRY_PROJECT_ENDPOINT=https://<your-hub>.services.ai.azure.com/api/projects/<your-project>
   ```

   > **Where to find the endpoint:**  
   > Azure AI Foundry portal → your project → **Overview** → **Project endpoint**

3. **Authenticate with Azure:**

   ```bash
   az login
   ```

   Or set `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` / `AZURE_TENANT_ID` for service-principal auth.  
   > **Tip:** The `deploy.ps1` script **automatically creates** a Service Principal (`sp-model-migration-eval`) and writes these variables to `.env` — no manual setup needed for containerised deployments.

### Usage from the Web UI

1. Go to the **Evaluate** page (`/evaluate`).
2. If Foundry is configured, an **Include Foundry LLM-as-judge** toggle appears below the **Verbose** checkbox.
3. Enable the toggle, then click **Run Evaluation** as usual.
4. After the local evaluation completes, the system automatically:
   - Exports results to a JSONL file
   - Uploads the dataset to Foundry
   - Creates an evaluation with built-in LLM-as-judge evaluators
   - Polls until the run completes (up to 5 minutes)
5. A **brand-blue banner** appears with the evaluation status and a **📊 View in Control Plane** link.
6. Click the link to open the Foundry dashboard and inspect per-scenario scores.

### Usage from the API

```bash
# Check if Foundry is configured
curl http://127.0.0.1:5000/api/foundry/status

# Submit a saved result to Foundry
curl -X POST http://127.0.0.1:5000/api/foundry/submit \
  -H "Content-Type: application/json" \
  -d '{"result_filename": "gpt4_classification_2026-02-18T10-30-00.json"}'
```

### Evaluators by Type

| Evaluation Type | Built-in Evaluators Used |
|-----------------|--------------------------|
| **Classification** | `coherence`, `fluency`, `relevance`, `task_adherence`, `similarity` |
| **Dialog** | `coherence`, `fluency`, `relevance`, `intent_resolution`, `task_adherence` |
| **General** | `coherence`, `fluency`, `relevance`, `response_completeness` |

### Viewing Results in the Control Plane

1. After a successful submission, click the **📊 View in Control Plane** link in the UI, or open the `report_url` from the API response.
2. The Foundry dashboard shows:
   - **Overall scores** per evaluator (1–5 scale for most, 0–1 for some)
   - **Per-row scores** — expand any row to see the evaluator's reasoning
   - **Distribution charts** — score histograms for each evaluator
   - **Comparison view** — if you run multiple models, compare them side-by-side in the same project
3. Navigate to **Azure AI Foundry** → your project → **Evaluation** → **Runs** to see all historical evaluation runs.

### Costs

Each LLM-as-judge evaluator makes one API call per test scenario.  Approximate token usage:

| Item | Tokens |
|------|--------|
| Per evaluator per row | ~500–1,500 input + ~100–300 output |
| Classification (10 scenarios, 5 evaluators) | ~50 calls ≈ 75K tokens |
| Dialog (15 scenarios, 5 evaluators) | ~75 calls ≈ 112K tokens |

Cost depends on the judge model pricing.  With `gpt-4.1` at $2.50/M input + $10/M output, a full classification run costs approximately **$0.20–0.40 USD**.

### Graceful Degradation

If the Foundry SDK is not installed or the configuration is missing, the feature is **silently disabled** — all local evaluations continue to work normally without any error.  The **Include Foundry LLM-as-judge** toggle simply doesn't appear in the UI.

---
## � Deployment

The project includes a single PowerShell script that handles both local Docker and Azure Container Apps deployment.

### Prerequisites

| Tool | Local Docker | Azure |
|------|:---:|:---:|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | ✅ | ✅ |
| [Azure CLI](https://aka.ms/installazurecliwindows) | — | ✅ |
| `.env` file with credentials | ✅ | ✅ |

### Run the Deployment Script

```powershell
.\deploy.ps1
```

The script presents an interactive menu:

```
  1. Local Docker Desktop  (for development / testing)
  2. Azure Container Apps  (for production / demos)
```

### Option 1: Local Docker Desktop

- Builds a **timestamped image** from the `Dockerfile` (Python 3.13-slim + Flask + Azure CLI).
- Auto-creates a **Service Principal** for Foundry authentication inside the container (if not already configured).
- Validates `.env` has all required variables (Azure OpenAI + Foundry SP credentials).
- Injects credentials from `.env` via `--env-file`.
- Exposes the web UI at **http://localhost:5000**.
- Runs an automatic health check against `/api/health`.

```powershell
# After deployment, useful commands:
docker logs -f model-migration-eval    # Stream logs
docker stop model-migration-eval       # Stop
docker rm -f model-migration-eval      # Remove
```

### Option 2: Azure Container Apps

Before the deployment steps, the script auto-creates a **Service Principal** (`sp-model-migration-eval`) for Foundry authentication inside the container and writes `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET` to `.env`.

Deploys the full stack to Azure in 7 automated steps:

| Step | Action |
|------|--------|
| 1 | Create / verify Resource Group |
| 2 | Create Azure Container Registry (ACR) |
| 3 | Build Docker image & push to ACR |
| 4 | Create Container Apps Environment |
| 5 | Prepare secrets & environment variables |
| 6 | Create / update Container App (with liveness & readiness probes) |
| 7 | Retrieve the public HTTPS URL |

The script supports **skipping completed steps** — useful when re-deploying after a code change (skip to step 3 to just rebuild & push).

#### Configuration

Edit the variables at the top of `deploy.ps1`:

```powershell
$RESOURCE_GROUP     = "rg-model-migration"
$LOCATION           = "swedencentral"
$ACR_NAME           = "acrmodelmigration"      # globally unique, lowercase
$CONTAINER_APP_NAME = "model-migration-eval"
```

Secrets (like `AZURE_OPENAI_API_KEY`) are automatically stored as Container Apps secrets and injected via `secretRef` — they are never exposed in plain text in the YAML configuration.

#### Container Resources

| Setting | Value |
|---------|-------|
| CPU | 1.0 vCPU |
| Memory | 2 Gi |
| Min replicas | 0 (scales to zero when idle) |
| Max replicas | 3 |
| Scale rule | HTTP concurrent requests > 20 |

#### Post-Deployment Commands

```powershell
# View live logs
az containerapp logs show -n model-migration-eval -g rg-model-migration --follow

# Check status
az containerapp show -n model-migration-eval -g rg-model-migration --query properties.runningStatus

# List revisions
az containerapp revision list -n model-migration-eval -g rg-model-migration -o table

# Tear down everything
az group delete -n rg-model-migration --yes --no-wait
```

---

## �🖥️ CLI Commands

```bash
# Start web server (default)
python app.py
python app.py web --host 0.0.0.0 --port 5001 --debug

# Evaluate a single model
python app.py evaluate --model gpt4 --type classification
python app.py evaluate --model gpt5 --type dialog
python app.py evaluate --model gpt5 --type general

# Compare two models
python app.py compare --model-a gpt4 --model-b gpt5 --type classification

# Compare across all evaluation types at once
python app.py compare --model-a gpt4 --model-b gpt5 --type all

# Import an external topic (GPT-4 prompt + test data → archived topic with GPT-5)
python tools/import_topic.py --topic "My Topic" --gpt4-class-prompt prompt.txt --class-test-data data.json
```

Results are automatically saved to `data/results/` as JSON files.

---

## 🌐 REST API Reference

All endpoints are available at `http://127.0.0.1:<port>/api/`.

### Health & Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check & client connection status |
| `GET` | `/api/models` | List all configured model deployments |

### Test Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/data/summary` | Summary counts of test data per type |
| `GET` | `/api/data/overview` | Counts per data type for active topic + all archives |
| `GET` | `/api/data/raw/<type>` | Get raw JSON for a data type (`?topic=` reads from archive) |
| `PUT` | `/api/data/raw/<type>` | Save/overwrite raw JSON for a data type (`?topic=` writes to archive) |
| `GET` | `/api/data/sync-status` | Check if test data matches the current topic |
| `POST` | `/api/data/regenerate` | Regenerate synthetic test data — returns HTTP 202, runs asynchronously in background |
| `GET` | `/api/data/regenerate/<run_id>/status` | Poll regeneration job progress — returns result payload when complete |

### Evaluation & Comparison

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/evaluate/single` | Evaluate a single prompt against one or more models |
| `POST` | `/api/evaluate/batch` | Batch evaluation — auto-saves result to disk |
| `POST` | `/api/compare` | Compare two models — returns HTTP 202, runs asynchronously in background |
| `GET` | `/api/compare/<run_id>/status` | Poll comparison job progress — returns result payload when complete |

### Prompt Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/prompts` | List all available prompt templates |
| `GET` | `/api/prompts/<model>/<type>` | Read a specific prompt's content |
| `PUT` | `/api/prompts/<model>/<type>` | Save/update a prompt (creates version snapshot) |
| `POST` | `/api/prompts/generate` | AI-generate all prompts + matching test data — returns HTTP 202, runs asynchronously in background |
| `GET` | `/api/prompts/generate/<run_id>/status` | Poll generation job progress — returns result payload when complete |

### Version History

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/prompts/history` | List versions (`?model=`, `?type=`, `?topic=`) |
| `GET` | `/api/prompts/history/<id>` | Read a specific version's content |
| `POST` | `/api/prompts/restore` | Restore a historical version as active |
| `DELETE` | `/api/prompts/history/<id>` | Delete a single version |
| `POST` | `/api/prompts/history/bulk-delete` | Delete multiple versions at once |

### Results

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/results` | List all saved result files (sorted newest first) |
| `GET` | `/api/results/<filename>` | Read a specific result file |
| `DELETE` | `/api/results/<filename>` | Delete a saved result (path-traversal protected) |

### Foundry Control Plane

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/foundry/status` | Check if Foundry evaluation SDK is installed and configured |
| `POST` | `/api/foundry/submit` | Submit a saved evaluation result to Foundry for LLM-as-judge evaluation |

### Topic Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/topics` | List all topics (active + archived) |
| `POST` | `/api/topics/import` | Import external topic from uploaded GPT-4 prompt(s) + test data (multipart form) |
| `POST` | `/api/topics/activate` | Switch to an archived topic (restores prompts + data) |
| `POST` | `/api/topics/archive` | Archive the current active topic |
| `DELETE` | `/api/topics/<name>` | Delete an archived topic |

---

## 📊 Evaluation Dimensions

### Classification Evaluation

| Metric | Description | How it's calculated |
|--------|-------------|---------------------|
| Accuracy | Overall classification correctness | Correct / total predictions |
| F1 Score | Harmonic mean of precision and recall | Weighted average across categories |
| Precision | Correct positive predictions / all positive predictions | Per-category, then weighted |
| Recall | Correct positive predictions / all actual positives | Per-category, then weighted |
| Cohen's Kappa | Inter-rater agreement beyond chance | Agreement-adjusted metric |
| Per-category accuracy | Breakdown by each category defined in the prompt | Individual category scores |
| Confusion matrix | Full category-vs-category misclassification grid | Rendered as heatmap in UI |
| Subcategory accuracy | Match rate for sub-field classification | Case-insensitive comparison |
| Priority accuracy | Match rate for priority-level prediction | Case-insensitive comparison |
| Sentiment accuracy | Match rate for sentiment detection | Case-insensitive comparison |
| Avg confidence | Model's self-reported confidence | Average across all predictions |
| Confidence calibration | Reliability of confidence scores | Binned accuracy vs. confidence curve |

> **Category normalisation:** The metrics engine automatically normalises category codes for comparison — case-insensitive matching, legacy short-code aliases (e.g. `PKG` → `travel_packages`), and Spanish name aliases (e.g. `disponibilidad` → `availability`).  Dynamic topic-specific categories pass through unchanged.

### Dialog Evaluation

| Metric | Description | How it's calculated |
|--------|-------------|---------------------|
| Follow-up quality | Relevance and helpfulness of follow-up questions | Keyword overlap with expected context gaps |
| Context coverage | How well the model detects conversation context gaps | Gap keyword matching against model response |
| Rule compliance | Whether follow-ups respect per-topic rules | Keyword evidence overlap (~35% threshold per rule) |
| Empathy score | Conversational empathy and tone | Detects 17 empathy markers in first 250 chars (0 / 0.5 / 1.0) |
| Optimal similarity | Closeness to gold-standard optimal follow-up | Word-level Jaccard similarity, scaled ×2.5 |
| Resolution efficiency | Question count vs. expected resolution turns | Band scoring: 0.8–1.5× target = 1.0, 0.5–2.0× = 0.7, else = 0.3 |
| Consistency | Reproducibility across repeated runs | Multiple runs per scenario, response similarity |
| Avg questions generated | Average follow-up questions per scenario | Count of `?` tokens in model response |

### General Evaluation

| Metric | Description |
|--------|-------------|
| Format compliance | Follows expected output format (JSON, table, list, etc.) |
| Completeness | All required elements present in response |
| Reasoning | Logical deduction and multi-step inference |
| Structured output | JSON/table formatting correctness |
| Safety | Content filter and PII handling |
| Consistency | Response variance across repeated calls |

### Latency & Cost Metrics (all types)

| Metric | Description |
|--------|-------------|
| Mean latency | Average response time |
| Median latency | 50th percentile |
| P95 latency | 95th percentile |
| P99 latency | 99th percentile |
| Min / Max latency | Response time range |
| Std deviation | Response time variability |
| Tokens per second | Throughput (completion tokens / latency) |
| Cost per request | USD estimate based on model pricing |
| Total cost | Aggregate cost across all requests |
| Cache hit rate | % of prompt tokens served from Azure prompt cache |
| Reasoning token % | % of completion tokens used for chain-of-thought reasoning |
| Avg prompt tokens | Average input tokens per request |
| Avg completion tokens | Average output tokens per request |

### Consistency Metrics (classification & dialog)

| Metric | Description |
|--------|-------------|
| Reproducibility score | Same response across repeated runs (0–1) |
| Semantic similarity | Meaning similarity of response variations |
| Format consistency | Consistent output structure across runs |
| Response variance | Variance in key outputs |

### Comparison Report

When comparing two models, each dimension shows:
- **Model A value** and **Model B value**
- **Percentage change** (positive = improvement)
- **Significance level** — `high`, `medium`, `low`, or `negligible`
- **Statistical significance** via Welch's t-test on raw latency/score distributions
- **Overall winner** and **actionable recommendations**

**Classification dimensions:** Accuracy, F1, Precision, Recall, Subcategory/Priority/Sentiment Accuracy, Avg Confidence.

**Dialog dimensions:** Follow-up Quality, Context Coverage, Rule Compliance, Empathy Score, Optimal Similarity, Resolution Efficiency.

**Latency dimensions:** Mean Latency, P95, Std Dev, Cost/Request, Cache Hit Rate, Reasoning Token %, Tokens/sec.

**Consistency dimensions:** Reproducibility, Format Consistency.

---

## 🔧 Requirements

- Python 3.10+
- Azure AI Foundry subscription with at least one deployed model
- Model deployments accessible via Azure OpenAI API

### Dependencies

| Package | Min Version | Purpose |
|---------|-------------|---------|
| `openai` | ≥1.40.0 | Azure OpenAI SDK |
| `azure-identity` | ≥1.15.0 | Azure authentication |
| `azure-ai-projects` | ≥2.0.0b2 | Microsoft Foundry Control Plane evaluation (optional) |
| `flask` | ≥3.0.0 | Web framework |
| `flask-cors` | ≥4.0.0 | Cross-origin support |
| `scikit-learn` | ≥1.3.0 | Classification metrics (F1, accuracy, kappa) |
| `numpy` | ≥1.24.0 | Statistical calculations |
| `diskcache` | ≥5.6.3 | Response caching |
| `python-dotenv` | ≥1.0.0 | `.env` file management |
| `pyyaml` | ≥6.0.1 | YAML config parsing |
| `httpx` | ≥0.26.0 | HTTP transport for async client |
| `pytest` | ≥7.4.0 | Testing |
| `pytest-asyncio` | ≥0.21.0 | Async test support |

See [requirements.txt](requirements.txt) for the full list with version pins.

---

## 🏗️ Architecture

### Core Classes

| Class | Module | Purpose |
|-------|--------|---------|
| `AzureOpenAIClient` | `src.clients.azure_openai` | Wraps the OpenAI SDK — connection management, chat completions, streaming |
| `ModelEvaluator` | `src.evaluation.evaluator` | Runs classification/dialog/general evaluations against a single model |
| `EvaluationResult` | `src.evaluation.evaluator` | Dataclass container for evaluation output — serialises to/from JSON |
| `ModelComparator` | `src.evaluation.comparator` | Compares evaluation results between two models with significance analysis |
| `ComparisonReport` | `src.evaluation.comparator` | Dataclass for comparison output — dimensions, winner, recommendations |
| `MetricsCalculator` | `src.evaluation.metrics` | Computes classification metrics (accuracy, F1, kappa, confusion matrix, calibration), dialog quality metrics (rule compliance, empathy, optimal similarity, resolution efficiency), latency & cost analytics, and consistency scoring.  Includes case-insensitive category normalisation with alias support |
| `FoundryEvaluator` | `src.evaluation.foundry_evaluator` | Submits evaluation data to Microsoft Foundry Control Plane for LLM-as-judge quality evaluation.  Handles JSONL export, dataset upload, evaluation creation, and run polling |
| `PromptManager` | `src.utils.prompt_manager` | Prompt editing, versioning, AI generation (with JSON sanitisation & retry), topic archival, data sync |
| `PromptLoader` | `src.utils.prompt_loader` | Template loading from disk with in-memory caching |
| `DataLoader` | `src.utils.data_loader` | Loads synthetic test scenarios from JSON files |

### Technology Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+ / Flask |
| Frontend | Tailwind CSS (CDN) + Fluent 2 design system + Chart.js |
| AI API | Azure OpenAI SDK (`openai` package) |
| Config | YAML + `.env` with variable substitution |
| Storage | File-based JSON (no database required) |
| Caching | `diskcache` for API responses |

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [Migration Guide](docs/migration_guide.md) | Comprehensive GPT-4 → GPT-5 migration playbook |
| [Prompt Design](docs/prompt_design.md) | Best practices: formatting, drift avoidance, caching |
| [Security & Governance](docs/security_guide.md) | Data protection, sandbox tools, content filtering, audit |

---

## 📄 License

MIT License
