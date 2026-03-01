# Azure OpenAI Model Migration Evaluation Framework

A comprehensive evaluation framework for migrating production systems between any Azure OpenAI model generations (e.g. GPT-4o → GPT-4.1, GPT-4.1 → GPT-5.2, or any configured pair).  Features a full web UI with multi-topic management, AI-powered prompt & test-data generation (with dynamic per-topic category taxonomies using readable `snake_case` codes), deep batch evaluation across **5 scenario types** (classification, dialog, general, RAG, and tool calling), side-by-side model comparison with statistical significance, versioned prompt history, a test-data explorer/editor, rich narrative verbose logging, token & cost analytics, consistency/reproducibility testing, and persistent results with filtering & deletion.

---

## 🎯 Overview

When you upgrade or switch a model deployment in Azure AI Foundry — from GPT-4o to GPT-4.1, or from GPT-4.1 to GPT-5.2, for example — you need to answer questions like:

- *"Does the new model still classify tickets correctly?"*
- *"Is latency better or worse?"*
- *"Do my prompts need to be rewritten?"*
- *"Does the model stay grounded against my RAG context?"*
- *"Can the new model select the right tools and extract parameters accurately?"*

This framework automates that process end-to-end:

1. **Generate** domain-specific prompts + synthetic test data for any topic via AI.
2. **Evaluate** each model independently against 5 scenario types: classification, dialog, general, RAG, and tool calling.
3. **Compare** two models head-to-head with quantified metrics and significance levels.
4. **Browse** saved results, filter by type, inspect details, and delete old runs.
5. **Manage** multiple topics — archive, switch, restore, or **import** your own prompt + data sets.

### Key Capabilities

| Area | Highlights |
|------|------------|
| **Multi-Model** | Configure unlimited models in `settings.yaml` (GPT-4o, GPT-4.1, GPT-4.1-mini, GPT-5.1, GPT-5.2, reasoning variants, etc.) — each with `model_family` for automatic API behaviour |
| **Multi-Topic** | Switch between self-contained topic archives (prompts + data) without losing anything |
| **AI Generation** | One-click generation of optimised prompts (4 task types × N models) + 5 test datasets (70 scenarios) tailored to any domain, with dynamic category taxonomy and JSON retry logic |
| **Topic Import** | Import prompts + test data from disk for any source model (web UI or CLI) — target model prompts are auto-generated and the topic is archived ready to activate |
| **Classification** | Accuracy, F1, precision, recall, subcategory/priority/sentiment accuracy, confidence calibration, confusion matrix |
| **Dialog** | Follow-up quality, context coverage, rule compliance, empathy score, optimal similarity, resolution efficiency, consistency |
| **General** | Format compliance, completeness, reasoning, safety, structured output |
| **RAG** | Groundedness, relevance, context keyword overlap, response completeness, latency & cost analytics |
| **Tool Calling** | Tool selection accuracy, parameter extraction accuracy, response correctness, latency & cost analytics |
| **Token & Cost** | Per-request token breakdown (prompt/completion/cached/reasoning), cost estimation, cache hit rate, throughput (tok/s) |
| **Consistency** | Multi-run reproducibility scoring, response variance, format consistency |
| **Model Comparison** | Dimension-by-dimension comparison with statistical significance (Welch's t-test) and actionable recommendations |
| **Prompt Versioning** | Every save creates a timestamped snapshot — preview, restore, or delete any version |
| **Test Data Editor** | View, create, and edit test scenarios via type-specific web forms (classification, dialog, general, RAG, tool calling) with auto-scroll, a JSON toggle for advanced editing, and configurable scenario counts per type |
| **Results Persistence** | Evaluations and comparisons auto-save to disk — browse, filter, inspect, and delete from the UI |
| **Verbose Logging** | Rich narrative verbose mode with colour-coded entries (step/ok/warn/err/detail/head) and timestamped progress feed |
| **Foundry Control Plane** | Optional LLM-as-judge evaluation via Microsoft Foundry Runtime — coherence, fluency, relevance, task adherence, intent resolution — with results visible in the Foundry dashboard |
| **Multi-User Auth** | Email + OTP authentication with per-user content isolation — each user gets their own prompts, test data, and results |
| **Copilot Studio UI** | Fluent 2 design system inspired by Microsoft Copilot Studio — top header bar, collapsible sidebar, brand-blue palette, flat controls, Segoe UI typography |
| **Auto-Detection** | SDK automatically uses `max_completion_tokens` for newer-generation and o-series models |

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
├── azure.yaml                      # Azure Developer CLI (azd) project definition
├── deploy.ps1                      # Alternative deployment script (Docker Desktop or Azure)
│
├── infra/                          # ⬅ Bicep infrastructure-as-code (used by azd)
│   ├── main.bicep                  #   Entry point — AVM pattern modules
│   ├── main.parameters.json        #   Parameters populated by azd environment
│   └── modules/
│       ├── acr-access.bicep        #   AcrPull role assignment
│       ├── openai-access.bicep     #   Cognitive Services OpenAI User role
│       └── foundry-access.bicep    #   Azure AI Developer role (Foundry)
│
├── config/
│   ├── settings.yaml               # Azure credentials & model definitions
│   ├── model_params.yaml           # Model parameter reference table
│   └── data_gen_prompts/           # ⬅ Externalised meta-prompt templates
│       ├── classification.txt      #   Classification test-data generation template
│       ├── dialog.txt              #   Dialog test-data generation template
│       ├── general.txt             #   General capability test-data generation template
│       ├── rag.txt                 #   RAG test-data generation template
│       ├── tool_calling.txt        #   Tool calling test-data generation template
│       └── system_message.txt      #   System message for the data-generation LLM
│
├── data/
│   ├── auth.db                      # SQLite user database (auto-created)
│   ├── users/                      # ⬅ Per-user isolated content
│   │   └── <user_id>/              #   e.g. angels_at_microsoft_com
│   │       ├── prompts/            #   User's prompt templates
│   │       ├── synthetic/          #   User's test data
│   │       └── results/            #   User's evaluation results
│   ├── synthetic/                  # Shared/seed synthetic datasets (copied to new users)
│   │   ├── classification/         #   Classification scenarios (20) — .json + optional .csv
│   │   ├── dialog/                 #   Follow-up dialog samples (15)
│   │   ├── general/               #   General capability tests (15)
│   │   ├── rag/                   #   RAG grounding & retrieval tests (10)
│   │   ├── tool_calling/          #   Tool selection & parameter tests (10)
│   │   └── topics/                #   ⬅ Archived topic datasets
│   │       ├── red_sea_diving_travel/
│   │       ├── specialized_agent_.../  # aeronautics
│   │       └── telco_customer_service/
│   └── results/                    # Legacy shared results (migrated to per-user)
│
├── prompts/                        # ⬅ Prompt templates (editable on disk or via UI)
│   ├── gpt4/                       #   GPT-4.1 optimised prompts
│   │   ├── classification_agent_system.md
│   │   └── dialog_agent_system.md
│   ├── gpt4o/                      #   GPT-4o optimised prompts
│   ├── gpt41_mini/                 #   GPT-4.1-mini optimised prompts
│   ├── gpt5/                       #   GPT-5.2 optimised prompts
│   │   ├── classification_agent_system.md
│   │   └── dialog_agent_system.md
│   ├── gpt51/                      #   GPT-5.1 optimised prompts
│   ├── gpt5_reasoning/             #   GPT-5.1 reasoning (falls back to gpt5/ prompts)
│   ├── history/                    #   Version history (auto-managed)
│   │   └── versions.json
│   └── topics/                     #   ⬅ Archived topic prompts
│       ├── red_sea_diving_travel/
│       ├── specialized_agent_.../  # aeronautics
│       └── telco_customer_service/
│
├── src/
│   ├── auth/                        # ⬅ Multi-user authentication module
│   │   ├── models.py               #   User dataclass
│   │   ├── user_store.py           #   SQLite-backed user store
│   │   ├── code_manager.py         #   OTP code generation & verification
│   │   ├── email_sender.py         #   Email backends (SMTP / console)
│   │   ├── session.py              #   Flask session middleware & public routes
│   │   └── user_context.py         #   Per-user directory layout & seeding
│   ├── clients/
│   │   └── azure_openai.py         # Azure OpenAI client (sync/async/streaming)
│   ├── evaluation/
│   │   ├── metrics.py              # MetricsCalculator — classification, dialog quality, latency, cost, consistency
│   │   ├── evaluator.py            # ModelEvaluator + EvaluationResult (classification/dialog/general/RAG/tool_calling)
│   │   ├── comparator.py           # ModelComparator + ComparisonReport with statistical significance
│   │   └── foundry_evaluator.py    # Microsoft Foundry Control Plane integration (optional)
│   ├── utils/
│   │   ├── prompt_loader.py        # PromptLoader — template loading with caching
│   │   ├── prompt_manager.py       # PromptManager — editing, versioning, AI gen, topics
│   │   └── data_loader.py          # DataLoader — synthetic scenario loading
│   └── web/
│       ├── routes.py               # Flask API routes (1500+ lines, 50+ routes)
│       └── templates/
│           ├── _fluent_head.html    # Fluent 2 design system (CSS tokens, Tailwind config, component classes)
│           ├── _sidebar.html        # Top header bar + collapsible left sidebar + user menu
│           ├── login.html           # Email + OTP two-step login page
│           ├── index.html           # Dashboard — quick single-prompt test
│           ├── evaluate.html        # Batch evaluator with verbose mode
│           ├── compare.html         # Model comparison with charts
│           ├── results.html         # Results browser with filters & delete
│           ├── prompts.html         # Prompt Manager (edit, generate, history, data explorer)
│           ├── prompts_V1.html      # Prompt Manager (previous layout version)
│           └── import_samples.html  # JSON & CSV import samples reference page
│
├── tools/
│   ├── add_model.py                 # CLI tool: add a new model (interactive or scripted)
│   ├── import_topic.py              # CLI tool: import external topic from source prompt + test data
│   ├── migrate_to_multiuser.py      # Migrate existing data to a user's namespace
│   ├── regenerate_all_topics.py     # Regenerate prompts + test data for all archived topics
│   ├── gpt4_classification_prompt.md # Sample classification prompt for import testing
│   ├── gpt4_dialog_prompt.txt       # Sample dialog prompt for import testing
│   ├── test_data_classification.json # Sample classification test data for import testing
│   ├── test_data_dialog.json        # Sample dialog test data for import testing
│   ├── test_data_general.json       # Sample general test data for import testing
│   └── test_import.bat              # Quick-launch script for import testing
│
├── docs/
│   ├── migration_guide.md          # Comprehensive model migration guide
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

# Authentication SMTP (optional — see "Authentication" section below)
SMTP_HOST=smtp.office365.com
SMTP_USERNAME=noreply@yourdomain.com
SMTP_PASSWORD=your-app-password
SMTP_FROM_EMAIL=noreply@yourdomain.com
FLASK_SECRET_KEY=          # Random hex string; auto-generated if empty
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
      deployment_name: "gpt-4.1"          # Your GPT-4.1 deployment name
      model_family: "gpt4"
      model_version: "2024-08-06"
      max_tokens: 4096
      temperature: 0.1

    gpt5:
      deployment_name: "gpt-5.2"          # Your GPT-5.2 deployment name
      model_family: "gpt5"
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
| **Top header bar** | 48 px, light grey (`#F0F0F0`), brand logo, app title, active topic badge, and **user menu** (email + dropdown with Sign out) |
| **Left sidebar** | Icon-only rail (48 px) that expands to 220 px on hover; 5 navigation links + user info + Sign out + Settings gear; active page indicated by a 3 px blue accent bar |
| **Content area** | Offset by header + sidebar; neutral surface background (`#FAF9F8`); Fluent cards, inputs, badges, and buttons throughout |

### Tab Overview

| Tab | Icon | URL | Purpose |
|-----|------|-----|---------|
| **Dashboard** | 🏠 | `/` | Quick single-prompt evaluation — enter a prompt, pick models, see responses side-by-side |
| **Evaluate** | 📊 | `/evaluate` | Batch evaluation of a single model across all test scenarios for a given type |
| **Compare** | ⚖️ | `/compare` | Head-to-head comparison of two models with dimension-by-dimension charts |
| **Results** | 📋 | `/results` | Browse, filter, inspect, and delete all saved evaluation/comparison results |
| **Prompts** | ✏️ | `/prompts` | Full prompt lifecycle: view, edit, AI-generate, version history, and test data explorer |
| **Import Samples** | 📄 | `/import-samples` | JSON & CSV sample files for all 5 task types — copyable examples with field reference (opens from the Prompts page "Samples" link) |

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

Each entry is timestamped.  For **classification**, each scenario shows expected vs. predicted category, confidence, latency, token breakdown (prompt/completion/cached), and subcategory/priority/sentiment match status.  For **dialog**, each scenario shows category, context gaps, question count vs. expected turns (on-target ✓ / off-target ⚠), response excerpt, latency, and full token detail.  For **RAG**, each scenario shows groundedness and relevance scores with context keyword matching.  For **tool calling**, each scenario shows tool selection accuracy and parameter extraction results.  A final summary block aggregates quality, consistency, latency, cost, and throughput.

### Dashboard (`/`)

- Enter a user prompt or pick a pre-loaded test scenario.
- Select one or two models from the configured list.
- Get instant responses with latency and token usage.
- Quick health check to verify Azure connectivity.

### Evaluate (`/evaluate`)

1. Select a **model** and **evaluation type** (classification, dialog, general, RAG, or tool calling).
2. Optionally enable **☑ Verbose** for detailed narrative logging and/or **☑ Include Foundry LLM-as-judge** for LLM-quality evaluation via Microsoft Foundry.
3. Click **▶ Run Evaluation**.
4. The system sends every test scenario through the model and computes metrics.
5. Results are displayed with **dynamic summary metric cards** per type and **auto-saved** to `data/results/`.

**Classification metric cards (12):** Accuracy, F1 Score, Avg Latency, Consistency, Subcategory Accuracy, Priority Accuracy, Sentiment Accuracy, Cost/Request, Cache Hit Rate, Reasoning Token %, Avg Confidence, Tokens/sec.

**Dialog metric cards (12):** Follow-up Quality, Context Coverage, Rule Compliance, Empathy Score, Optimal Similarity, Resolution Efficiency, Consistency, Avg Latency, P95 Latency, Cost/Request, Cache Hit Rate, Tokens/sec.

**General metric cards (4):** Format Compliance, Completeness, Avg Latency, P95 Latency.

**RAG metric cards (8):** Groundedness, Relevance, Format Compliance, Completeness, Avg Latency, P95 Latency, Cost/Request, Tokens/sec.

**Tool Calling metric cards (8):** Tool Selection Accuracy, Parameter Accuracy, Format Compliance, Completeness, Avg Latency, P95 Latency, Cost/Request, Tokens/sec.

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
| **✨ AI Generate** | Generate all 8 prompts (4 types × 2 models) + 5 test datasets for a new topic in one click |
| **Version History** | Filter, preview, restore, or delete (single/bulk) any past prompt version |
| **Test Data** | Browse, create, and edit test scenarios for all 5 evaluation types via **dynamic web forms** — each type gets a purpose-built form with specialised sub-editors (conversation turns, tool definitions, key-value context, tag lists). Toggle to raw JSON view for advanced editing |

Additionally, the left sidebar includes an **📥 Import Topic** panel with a **Samples** link that opens a reference page (`/import-samples`) showing copyable JSON and CSV examples for all 5 task types (see [Importing External Topics](#importing-external-topics) below).

---

## 🔐 Authentication & Multi-User Isolation

The framework supports **per-user authentication** with email + OTP (one-time password) codes. Each authenticated user gets an isolated workspace with their own prompts, test data, and evaluation results.

### How It Works

The login flow depends on the `code_verification` setting:

**With `code_verification: true`** (default — full OTP flow):

1. User enters their **email address** on the login page.
2. A **6-digit OTP code** is sent to that email (via SMTP or printed to the console in dev mode).
3. User enters the code → a **session cookie** is created (default: 8 hours).
4. On first login, the user's **isolated directory** is created and seeded with the shared prompt templates and test data.
5. All subsequent operations (evaluations, comparisons, prompt edits, topic management) read from and write to the user's own namespace.

**With `code_verification: false`** (email-only — no OTP):

1. User enters their **email address** on the login page.
2. The session is created **immediately** — no code is generated or sent.
3. This is ideal for **demos, workshops, and development** where SMTP isn't available or OTP friction is undesirable.
4. Per-user isolation still applies — each email gets its own workspace.

### Per-User Directory Layout

```
data/users/<user_id>/
├── prompts/
│   ├── gpt4/                    # Active prompt templates per model
│   ├── gpt4o/
│   ├── gpt5/
│   ├── history/                 # User's own version history (starts empty)
│   └── topics/                  # Archived topics
├── synthetic/
│   ├── classification/          # Test scenarios
│   ├── dialog/
│   ├── general/
│   ├── rag/
│   ├── tool_calling/
│   └── topics/                  # Archived topic data
└── results/                     # Evaluation & comparison results
```

The `user_id` is derived from the email: `angels@microsoft.com` → `angels_at_microsoft_com`.

### Initial Content for New Users

On first login, a new user receives:
- ✅ **All active prompt templates** (every configured model × 4 task types)
- ✅ **All archived topics** (prompts + test data)
- ✅ **All synthetic test data** (classification, dialog, general, RAG, tool calling)
- ❌ **No version history** — starts clean, only tracks the user's own changes
- ❌ **No evaluation results** — starts empty

### Configuring Authentication

Authentication is configured in `config/settings.yaml` under the `auth:` section:

```yaml
auth:
  # Email provider: "smtp" or "console" (console prints codes to stdout for dev)
  email_provider: "console"

  # Code verification: when false, users authenticate with just their email
  # (no OTP code is generated or sent). Useful for dev/demo environments.
  code_verification: true

  # Session lifetime (default: 8 hours)
  session_ttl_seconds: 28800

  # OTP code settings (only used when code_verification is true)
  code_length: 6
  code_ttl_seconds: 300   # 5 minutes
  max_attempts: 3

  # SMTP settings (used when email_provider is "smtp" and code_verification is true)
  smtp:
    host: "${SMTP_HOST}"             # e.g. smtp.office365.com
    port: 587
    username: "${SMTP_USERNAME}"     # e.g. noreply@yourdomain.com
    password: "${SMTP_PASSWORD}"     # App password or SMTP credential
    use_tls: true
    sender: "${SMTP_FROM_EMAIL}"     # Sender address (defaults to username)
```

### Development Mode (Console)

By default, `email_provider` is set to `"console"`.  OTP codes are **printed to stdout and the application log** instead of being emailed.  This is ideal for local development — no SMTP configuration needed:

```
==================================================
  [DEV] OTP code for angels@microsoft.com: 482917
==================================================
```

### Production Mode (SMTP)

To send real emails, set `email_provider: "smtp"` and configure the SMTP connection.  Add the credentials to your `.env` file:

```dotenv
SMTP_HOST=smtp.office365.com
SMTP_USERNAME=noreply@yourdomain.com
SMTP_PASSWORD=your-app-password-here
SMTP_FROM_EMAIL=noreply@yourdomain.com
```

Then update `settings.yaml`:

```yaml
auth:
  email_provider: "smtp"   # ← change from "console" to "smtp"
```

#### SMTP Provider Examples

| Provider | Host | Port | Notes |
|----------|------|------|-------|
| **Microsoft 365** | `smtp.office365.com` | 587 | Use an App Password (requires MFA) or OAuth-enabled SMTP |
| **Gmail** | `smtp.gmail.com` | 587 | Use an [App Password](https://myaccount.google.com/apppasswords) (requires 2FA enabled) |
| **SendGrid** | `smtp.sendgrid.net` | 587 | Username is `apikey`, password is your SendGrid API key |
| **Amazon SES** | `email-smtp.<region>.amazonaws.com` | 587 | Use IAM SMTP credentials |
| **Custom / On-prem** | Your SMTP server | 25/587 | Set `use_tls: false` if not supported |

#### Microsoft 365 Setup (Step by Step)

1. **Create a shared mailbox** (e.g. `noreply@yourdomain.com`) in Microsoft 365 Admin Center, or use an existing mailbox.
2. **Enable SMTP AUTH** for the mailbox: Exchange Admin → Mailbox → Mail flow → ☑ *Authenticated SMTP*.
3. If the account has **MFA enabled**, create an App Password: [https://mysignins.microsoft.com/security-info](https://mysignins.microsoft.com/security-info) → Add method → App password.
4. Set the environment variables:
   ```dotenv
   SMTP_HOST=smtp.office365.com
   SMTP_USERNAME=noreply@yourdomain.com
   SMTP_PASSWORD=<the-app-password>
   SMTP_FROM_EMAIL=noreply@yourdomain.com
   ```
5. Change `email_provider` to `"smtp"` in `settings.yaml`.
6. Restart the server and test login.

#### Gmail Setup (Step by Step)

1. Enable **2-Step Verification** on the Google account: [https://myaccount.google.com/security](https://myaccount.google.com/security).
2. Generate an **App Password**: [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) → Select *Mail* → Generate.
3. Set the environment variables:
   ```dotenv
   SMTP_HOST=smtp.gmail.com
   SMTP_USERNAME=yourname@gmail.com
   SMTP_PASSWORD=<the-16-char-app-password>
   SMTP_FROM_EMAIL=yourname@gmail.com
   ```
4. Change `email_provider` to `"smtp"` in `settings.yaml`.

### Migrating Existing Data to a User

If you have existing prompts, test data, and results from before enabling multi-user mode, use the migration tool to copy them into a user's namespace:

```bash
# Dry-run first (shows what would be copied)
python tools/migrate_to_multiuser.py --email user@example.com --dry-run

# Execute the migration
python tools/migrate_to_multiuser.py --email user@example.com
```

This copies `prompts/`, `data/synthetic/`, and `data/results/` into `data/users/<user_id>/` and creates the user record in the auth database.

### Session & Security

| Setting | Default | Description |
|---------|---------|-------------|
| `code_verification` | `true` | When `false`, users sign in with just their email (no OTP code sent) |
| `session_ttl_seconds` | 28800 (8h) | How long a session cookie remains valid |
| `code_length` | 6 | Number of digits in the OTP code |
| `code_ttl_seconds` | 300 (5 min) | How long an OTP code remains valid |
| `max_attempts` | 3 | Max OTP verification attempts before the code is invalidated |
| `FLASK_SECRET_KEY` | Auto-generated | Set via env var for persistence across restarts (recommended for production) |

OTP codes are **SHA-256 hashed** before storage.  The auth database (`data/auth.db`) uses SQLite with thread-safe per-thread connections.

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
2. **Import an external topic** — Upload your own prompt(s) + test data via the 📥 Import panel or the CLI tool.  Target model prompts are auto-generated and everything is saved as an archived topic.
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
| `gpt4/rag_agent_system.md` | RAG prompt optimised for GPT-4 |
| `gpt4/tool_calling_agent_system.md` | Tool calling prompt optimised for GPT-4 |
| `gpt5/classification_agent_system.md` | Classification prompt optimised for GPT-5 (native reasoning, concise) |
| `gpt5/dialog_agent_system.md` | Dialog prompt optimised for GPT-5 |
| `gpt5/rag_agent_system.md` | RAG prompt optimised for GPT-5 |
| `gpt5/tool_calling_agent_system.md` | Tool calling prompt optimised for GPT-5 |
| `data/synthetic/classification/*.json` | 20 classification scenarios with categories, sentiments, priorities |
| `data/synthetic/dialog/*.json` | 15 multi-turn dialog scenarios |
| `data/synthetic/general/*.json` | 15 general capability tests |
| `data/synthetic/rag/*.json` | 10 RAG scenarios with context documents and ground truth |
| `data/synthetic/tool_calling/*.json` | 10 tool calling scenarios with expected tools and parameters |

All content is domain-adapted and coherent — the test data exercises the exact categories defined in the prompts.

**Configurable scenario counts:**  The number of scenarios per type is configurable — set defaults in `config/settings.yaml` under `evaluation.test_data_counts`, or override per-run via the collapsible *🧪 Test data counts per type* panel in the Generate UI.

**Externalised meta-prompt templates:**  The meta-prompts that instruct the generator LLM how to build test data are stored as editable `.txt` files in `config/data_gen_prompts/` (one per type, plus a `system_message.txt`).  Templates use `{count}`, `{topic}`, and `{category_block}` placeholders.  If a template file is missing, a built-in fallback is used automatically.

### Importing External Topics

If you already have your own system prompt and test data, you can import them directly — the framework will generate optimised prompts for the target models automatically and create an archived topic ready to activate.

#### From the Web UI

1. Go to **Prompts** → sidebar → **📥 Import Topic**.
2. Click the **Samples** link next to the heading to see copyable JSON and CSV examples for every task type.
3. Enter a **topic name** (e.g. *"Insurance Claims Processing"*).
4. Upload one or more source model prompts:
   - **Classification prompt** (`.txt` / `.md`)
   - **Dialog prompt** (`.txt` / `.md`)
   - **RAG prompt** (`.txt` / `.md`)
   - **Tool Calling prompt** (`.txt` / `.md`)
5. Upload one or more test data files (**JSON or CSV**):
   - **Classification scenarios** (`.json` / `.csv`)
   - **Dialog scenarios** (`.json` / `.csv`)
   - **General capability tests** (`.json` / `.csv`)
   - **RAG scenarios** (`.json` / `.csv`)
   - **Tool Calling scenarios** (`.json` / `.csv`)
6. Select the **generator model** for target prompt creation.
7. Click **📥 Import Topic**.

> **CSV import:** CSV files are parsed via `csv.DictReader` and automatically converted to JSON on import.  The canonical storage format is always JSON.  See the [Import Samples page](#) (`/import-samples`) for format details and copy-ready examples.

The system validates the prompt(s) and test data, generates optimised prompts **in parallel** for all target models, and writes everything as an archived topic.  Activate it from the topic selector to start running evaluations.

#### From the CLI

Use the standalone `tools/import_topic.py` script:

```bash
# Classification prompt + three test data files (JSON)
python tools/import_topic.py \
    --topic "Insurance Claims Processing" \
    --source-class-prompt my_cls_prompt.txt \
    --class-test-data classification_data.json \
    --dialog-test-data dialog_data.json \
    --general-test-data general_data.json

# All 4 prompts + all 5 data files (mix of JSON and CSV)
python tools/import_topic.py \
    --topic "Telco Support Agent" \
    --source-class-prompt cls_prompt.txt \
    --source-dialog-prompt dlg_prompt.md \
    --source-rag-prompt rag_prompt.txt \
    --source-tool-calling-prompt tool_prompt.txt \
    --class-test-data cls.csv \
    --dialog-test-data dlg.json \
    --general-test-data gen.csv \
    --rag-test-data rag.json \
    --tool-calling-test-data tc.json

# Dialog prompt only
python tools/import_topic.py \
    --topic "Hotel Concierge" \
    --source-dialog-prompt hotel_prompt.txt \
    --dialog-test-data hotel_scenarios.json
```

**CLI Parameters:**

| Parameter | Required | Description |
|-----------|:--------:|-------------|
| `--topic` | ✅ | Human-readable topic name |
| `--source-class-prompt` | ★ | Source classification system prompt file (`.txt` / `.md`) |
| `--source-dialog-prompt` | ★ | Source dialog system prompt file |
| `--source-rag-prompt` | ★ | Source RAG system prompt file |
| `--source-tool-calling-prompt` | ★ | Source tool-calling system prompt file |
| `--class-test-data` | ★ | Classification scenarios (`.json` or `.csv`) |
| `--dialog-test-data` | ★ | Dialog scenarios (`.json` or `.csv`) |
| `--general-test-data` | ★ | General capability tests (`.json` or `.csv`) |
| `--rag-test-data` | ★ | RAG scenarios (`.json` or `.csv`) |
| `--tool-calling-test-data` | ★ | Tool calling scenarios (`.json` or `.csv`) |
| `--source-model` | — | Source model key (default: `gpt4`) |
| `--generator-model` | — | Model for target prompt generation (default: `gpt5`) |
| `--target-models` | — | Comma-separated target model keys (default: all except source) |
| `--force` | — | Overwrite if topic already exists |
| `--verbose` | — | Enable debug logging |

> ★ At least one prompt file **and** at least one test data file are required.  Legacy `--gpt4-*` aliases are still supported.

#### What happens during import

1. Each source prompt is validated — if it lacks the output format block required by the evaluation pipeline, it's appended automatically.
2. Optimised prompts are generated **in parallel** for each target model, preserving the same category taxonomy and adapting to each model family's best practices.
3. Test data is validated and missing optional fields are auto-filled.  CSV files are converted to JSON automatically.
4. Everything is written to the archive structure:
   - `prompts/topics/<slug>/<model>/` — prompt files per model
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

### Test Data Explorer

The **Test Data** sub-tab provides a full scenario editor for all 5 evaluation types.  Each type has a **purpose-built web form** instead of raw JSON editing:

| Type | Form Fields |
|------|-------------|
| **Classification** | ID, Scenario, Customer Input (textarea), Expected Category, Subcategory, Priority (dropdown), Sentiment (dropdown), Context (dynamic key-value editor), Follow-up Questions (tag list) |
| **Dialog** | ID, Scenario, Category, Conversation (multi-turn editor with role selector + message per turn), Context Gaps (tags), Optimal Follow-up (textarea), Follow-up Rules (tags), Expected Resolution Turns |
| **General** | ID, Test Type (dropdown), Complexity (dropdown), Prompt (textarea), Expected Behavior (textarea), Expected Output (JSON, optional), Run Count |
| **RAG** | ID, Scenario, Query (textarea), Context passage (textarea), Ground Truth (textarea), Expected Behavior, Complexity (dropdown) |
| **Tool Calling** | ID, Scenario, Query (textarea), Available Tools (visual tool-card editor: name, description, parameters JSON schema), Expected Tool Calls (tags), Expected Parameters (JSON), Complexity (dropdown) |

**Specialised sub-editors:**

- **Context editor** (classification) — Generic key-value pair editor. String, number, and boolean values are auto-detected and rendered as the appropriate input type; complex values (objects, arrays) use an inline JSON textarea. Add/remove properties dynamically.
- **Conversation editor** (dialog) — Add, remove, and reorder conversation turns with a role selector (`customer` / `agent`) and a message textarea per turn.
- **Tools editor** (tool calling) — Visual cards for each tool with function name, description, and a JSON Schema textarea for parameters. Add/remove tools dynamically.
- **Tags editor** — Used for string-array fields (context gaps, follow-up rules, expected tool calls, follow-up questions). Type and press Enter to add tag pills; click × to remove.

**Additional features:**

- **`{ } JSON` toggle** — Switch between the visual form and a raw JSON editor for any scenario.
- **Auto-scroll** — Clicking ✏️ Edit or ➕ Add Scenario automatically scrolls the page to the form.
- **Add / Edit / Delete** — Create new scenarios from blank type-specific templates, edit existing ones, or delete with confirmation.
- **Save Changes** — Persist all modifications back to disk with a single click.

---

## 📐 Test Data Schemas (JSON & CSV)

All test data uses a **flat schema** — no nested objects.  Complex values (conversations, tool definitions, context dictionaries) are stored as **JSON strings** within a single field.  This makes every type directly representable as a CSV row.

### Dual-Format Support

The framework reads both **JSON** and **CSV** files interchangeably:

| Feature | JSON (`.json`) | CSV (`.csv`) |
|---------|----------------|--------------|
| **File structure** | Root-level array of objects | Standard CSV with header row |
| **Complex fields** | JSON strings inside string fields | JSON strings inside quoted CSV cells |
| **List fields** | Pipe-separated strings (`"a \| b \| c"`) | Pipe-separated strings |
| **Encoding** | UTF-8 | UTF-8 with BOM (`utf-8-sig`) supported on read |
| **Priority** | Loaded first if both exist | Automatic fallback if `.json` is missing |

> **Automatic fallback:** The `DataLoader` tries the `.json` file first.  If it doesn't exist, it automatically tries the `.csv` file with the same base name — no configuration needed.

### Legacy Schema Compatibility

If an imported file uses the **old nested schema** (e.g. `context` as a dict instead of a JSON string, `conversation` as a list instead of a JSON string, legacy fields like `scenario`, `category`, `expected_output`), the `DataLoader` **auto-normalises** it to the flat format on load.  This ensures backward compatibility with older topic archives and externally created datasets.

---

### Classification — `classification_scenarios.json` / `.csv`

**Default count:** 20 scenarios

| Field | Type | Required | Default | Description |
|-------|------|:--------:|---------|-------------|
| `id` | string | ✅ | — | Unique ID (e.g. `CLASS_001`) |
| `customer_input` | string | ✅ | — | The customer message to classify |
| `expected_category` | string | ✅ | — | Expected category code (e.g. `billing_inquiry`) |
| `expected_subcategory` | string | — | `""` | Expected subcategory (e.g. `unexpected_charges`) |
| `expected_priority` | string | — | `""` | `low`, `medium`, `high`, or `critical` |
| `expected_sentiment` | string | — | `""` | Sentiment label (e.g. `frustrated`, `neutral`) |
| `context` | string | — | `""` | **JSON string** of a dict with contextual metadata |

**JSON example:**

```json
[
  {
    "id": "CLASS_001",
    "customer_input": "I just received my mobile and home internet bill and it's way higher than expected.",
    "expected_category": "billing_inquiry",
    "expected_subcategory": "unexpected_charges",
    "expected_priority": "high",
    "expected_sentiment": "frustrated",
    "context": "{\"account_tenure_months\": 18, \"billing_cycle\": \"2026-01\"}"
  }
]
```

**CSV example:**

```csv
id,customer_input,expected_category,expected_subcategory,expected_priority,expected_sentiment,context
CLASS_001,"I just received my mobile and home internet bill and it's way higher than expected.",billing_inquiry,unexpected_charges,high,frustrated,"{""account_tenure_months"": 18, ""billing_cycle"": ""2026-01""}"
```

> **Note:** In CSV, JSON strings have their inner double quotes doubled (`""`) per CSV escaping rules.

---

### Dialog — `dialog_scenarios.json` / `.csv`

**Default count:** 15 scenarios

| Field | Type | Required | Default | Description |
|-------|------|:--------:|---------|-------------|
| `id` | string | ✅ | — | Unique ID (e.g. `DLG_001`) |
| `conversation` | string | — | `""` | **JSON string** of `[{role, message}, …]` conversation turns |
| `context_gaps` | string | — | `""` | **Pipe-separated** information gaps (e.g. `"account number \| billing period"`) |
| `optimal_follow_up` | string | — | `""` | Gold-standard optimal agent follow-up response |
| `follow_up_rules` | string | — | `""` | **Pipe-separated** rules the follow-up must respect |
| `expected_resolution_turns` | int | — | `2` | Expected number of turns to resolve the issue |

**JSON example:**

```json
[
  {
    "id": "DLG_001",
    "conversation": "[{\"role\": \"customer\", \"message\": \"Hi, my mobile bill is way higher than usual.\"}]",
    "context_gaps": "customer authentication details | account or phone number | billing period in question",
    "optimal_follow_up": "I'm sorry for the confusion about your bill. To help you, I'll need to verify your account...",
    "follow_up_rules": "Begin with empathy | Request only the minimum necessary details | Do not assume the issue",
    "expected_resolution_turns": 2
  }
]
```

**CSV example:**

```csv
id,conversation,context_gaps,optimal_follow_up,follow_up_rules,expected_resolution_turns
DLG_001,"[{""role"": ""customer"", ""message"": ""Hi, my mobile bill is way higher than usual.""}]",customer authentication details | account or phone number | billing period in question,"I'm sorry for the confusion about your bill. To help you, I'll need to verify your account...",Begin with empathy | Request only the minimum necessary details | Do not assume the issue,2
```

---

### General — `capability_tests.json` / `.csv`

**Default count:** 15 scenarios

| Field | Type | Required | Default | Description |
|-------|------|:--------:|---------|-------------|
| `id` | string | ✅ | — | Unique ID (e.g. `GEN_001`) |
| `test_type` | string | — | `""` | Test category: `instruction_following`, `structured_output`, `calculation_accuracy`, `multi_language`, `safety_boundary`, `consistency`, `context_retention`, `reasoning_capability` |
| `prompt` | string | — | `""` | The test prompt (empty for `context_retention` tests) |
| `complexity` | string | — | `"medium"` | `low`, `medium`, or `high` |
| `expected_behavior` | string | — | `""` | Description of expected correct behavior |
| `conversation` | string | — | `""` | **JSON string** of `[{role, content}, …]` — used for multi-turn `context_retention` tests |
| `run_count` | int | — | `1` | Number of evaluation runs (>1 for `consistency` tests) |

**JSON example:**

```json
[
  {
    "id": "GEN_001",
    "test_type": "instruction_following",
    "prompt": "You are a telco customer support agent. A customer asks about their bill...",
    "complexity": "medium",
    "expected_behavior": "Assistant should follow all five instructions precisely.",
    "conversation": "",
    "run_count": 1
  },
  {
    "id": "GEN_008",
    "test_type": "context_retention",
    "prompt": "",
    "complexity": "medium",
    "expected_behavior": "Assistant should remember all details from the previous turns.",
    "conversation": "[{\"role\": \"user\", \"content\": \"My account is 12345.\"}, {\"role\": \"assistant\", \"content\": \"Got it.\"}, {\"role\": \"user\", \"content\": \"What's my account number?\"}]",
    "run_count": 1
  }
]
```

---

### RAG — `rag_scenarios.json` / `.csv`

**Default count:** 10 scenarios

| Field | Type | Required | Default | Description |
|-------|------|:--------:|---------|-------------|
| `id` | string | ✅ | — | Unique ID (e.g. `RAG_001`) |
| `query` | string | — | `""` | The user's question |
| `context` | string | — | `""` | Retrieved context passage (plain text) |
| `ground_truth` | string | — | `""` | Expected correct answer |

**JSON example:**

```json
[
  {
    "id": "RAG_001",
    "query": "What is the refund policy for international roaming charges?",
    "context": "Our refund policy allows refunds for roaming charges disputed within 30 days...",
    "ground_truth": "International roaming charges can be refunded within 30 days if disputed through the billing portal."
  }
]
```

**CSV example:**

```csv
id,query,context,ground_truth
RAG_001,What is the refund policy for international roaming charges?,"Our refund policy allows refunds for roaming charges disputed within 30 days...","International roaming charges can be refunded within 30 days if disputed through the billing portal."
```

---

### Tool Calling — `tool_calling_scenarios.json` / `.csv`

**Default count:** 10 scenarios

| Field | Type | Required | Default | Description |
|-------|------|:--------:|---------|-------------|
| `id` | string | ✅ | — | Unique ID (e.g. `TC_001`) |
| `query` | string | — | `""` | The user request |
| `available_tools` | string | — | `""` | **JSON string** of OpenAI-format tool definitions `[{type, function: {name, description, parameters}}, …]` |
| `expected_tool_calls` | string | — | `""` | **Pipe-separated** function names (e.g. `"search_flights \| search_hotels"`); empty = no tool should be called |
| `expected_parameters` | string | — | `""` | **JSON string** of expected parameters dict |

**JSON example:**

```json
[
  {
    "id": "TC_001",
    "query": "What's the weather like in Madrid right now?",
    "available_tools": "[{\"type\": \"function\", \"function\": {\"name\": \"get_current_weather\", \"description\": \"Get current weather\", \"parameters\": {\"type\": \"object\", \"properties\": {\"location\": {\"type\": \"string\"}, \"unit\": {\"type\": \"string\"}}}}}]",
    "expected_tool_calls": "get_current_weather",
    "expected_parameters": "{\"location\": \"Madrid, Spain\", \"unit\": \"celsius\"}"
  }
]
```

**CSV example:**

```csv
id,query,available_tools,expected_tool_calls,expected_parameters
TC_001,What's the weather like in Madrid right now?,"[{""type"": ""function"", ""function"": {""name"": ""get_current_weather"", ""description"": ""Get current weather"", ""parameters"": {""type"": ""object"", ""properties"": {""location"": {""type"": ""string""}, ""unit"": {""type"": ""string""}}}}}]",get_current_weather,"{""location"": ""Madrid, Spain"", ""unit"": ""celsius""}"
```

---

### CSV Encoding & Escaping Rules

| Rule | Details |
|------|---------|
| **Encoding** | UTF-8 (`utf-8-sig` supported on read to handle BOM) |
| **Header row** | Required — column names must match the field names exactly |
| **Quoting** | Standard CSV — fields containing commas, double quotes, or newlines are wrapped in `"…"` |
| **Embedded quotes** | Double-quote escaping — `"` inside a field becomes `""` in CSV |
| **JSON-in-CSV** | JSON strings (`context`, `conversation`, `available_tools`, `expected_parameters`) are stored as-is; the inner `"` characters become `""` |
| **Pipe-separated** | Fields like `context_gaps`, `follow_up_rules`, `expected_tool_calls` use ` \| ` (space-pipe-space) as delimiter |
| **Integers** | `expected_resolution_turns` and `run_count` are plain integers — cast on load |
| **Empty optionals** | Empty string for missing optional text fields |
| **Column order** | Must match the dataclass field order (see tables above) |

### Field-Format Summary (All Types)

| Field Name | Types | Storage Format |
|------------|-------|----------------|
| `id` | All 5 | Plain string |
| `customer_input` | Classification | Plain string |
| `expected_category` | Classification | Plain string |
| `expected_subcategory` | Classification | Plain string |
| `expected_priority` | Classification | Plain string |
| `expected_sentiment` | Classification | Plain string |
| `context` | Classification, RAG | JSON string (classification) / plain text (RAG) |
| `conversation` | Dialog, General | JSON string of array |
| `context_gaps` | Dialog | Pipe-separated |
| `optimal_follow_up` | Dialog | Plain string |
| `follow_up_rules` | Dialog | Pipe-separated |
| `expected_resolution_turns` | Dialog | Integer |
| `test_type` | General | Plain string |
| `prompt` | General | Plain string |
| `complexity` | General | Plain string |
| `expected_behavior` | General | Plain string |
| `run_count` | General | Integer |
| `query` | RAG, Tool Calling | Plain string |
| `ground_truth` | RAG | Plain string |
| `available_tools` | Tool Calling | JSON string of array |
| `expected_tool_calls` | Tool Calling | Pipe-separated |
| `expected_parameters` | Tool Calling | JSON string of dict |

### File Locations

Test data files are stored per-type and per-topic:

```
data/synthetic/
├── classification/
│   ├── classification_scenarios.json   ← Active topic (JSON)
│   └── classification_scenarios.csv    ← Active topic (CSV alternative)
├── dialog/
│   └── dialog_scenarios.json
├── general/
│   └── capability_tests.json
├── rag/
│   └── rag_scenarios.json
├── tool_calling/
│   └── tool_calling_scenarios.json
└── topics/                             ← Archived topics
    └── telco_customer_service/
        ├── classification/
        │   ├── classification_scenarios.json
        │   └── classification_scenarios.csv
        ├── dialog/
        ├── general/
        ├── rag/
        └── tool_calling/
```

> **Tip:** You can place a `.csv` file alongside (or instead of) the `.json` file for any type in any topic.  The `DataLoader` picks it up automatically.

---

## ⚙️ Model Configuration

Edit the `models` section in `config/settings.yaml`.  Each key becomes a model name used in the CLI, API, and web UI.

### Example: 6-Model Setup

```yaml
azure:
  endpoint: "${AZURE_OPENAI_ENDPOINT}"
  api_key:  "${AZURE_OPENAI_API_KEY}"
  api_version: "2025-04-01-preview"

  models:
    gpt4:
      deployment_name: "gpt-4.1"
      model_family: "gpt4"
      model_version: "2024-08-06"
      max_tokens: 4096
      temperature: 0.1

    gpt4o:
      deployment_name: "gpt-4o"
      model_family: "gpt4"
      model_version: "2024-08-06"
      max_tokens: 4096
      temperature: 0.1

    gpt41_mini:
      deployment_name: "gpt-4.1-mini"
      model_family: "gpt4"
      model_version: "2025-04-14"
      max_tokens: 16384
      temperature: 0.1

    gpt5:
      deployment_name: "gpt-5.2"
      model_family: "gpt5"
      model_version: "2025-01-01"
      max_tokens: 8192
      temperature: 0.1

    gpt51:
      deployment_name: "gpt-5.1"
      model_family: "gpt5"
      model_version: "2025-01-01"
      max_tokens: 16384
      temperature: 0.1

    gpt5_reasoning:
      deployment_name: "gpt-5.1"
      model_family: "gpt5"
      model_version: "2025-01-01"
      max_tokens: 16384
      reasoning_effort: "medium"    # low, medium, high (o-series / gpt-5)
```

### Parameters

| Parameter | Description | Notes |
|-----------|-------------|-------|
| `deployment_name` | Deployment name in Azure AI Foundry | As shown in Azure Portal → Deployments |
| `model_family` | Prompt-style family grouping | `gpt4` or `gpt5` — determines API behaviour (see below) |
| `model_version` | Model version string | From deployment details |
| `max_tokens` | Maximum response tokens | Model-dependent |
| `temperature` | 0.0–2.0 (lower = more deterministic) | 0.1 recommended for evaluation; **omitted for reasoning models** |
| `top_p` | Nucleus sampling (optional) | **Omitted for reasoning models** |
| `frequency_penalty` | Repetition penalty (optional) | **Omitted for reasoning models** |
| `presence_penalty` | Topic penalty (optional) | **Omitted for reasoning models** |
| `reasoning_effort` | Only for reasoning models | `low` / `medium` / `high` — GPT-5, o1, o3, o4 |

### Model Family Behaviour

The `model_family` field controls two automatic API-level differences:

| Behaviour | `model_family: "gpt4"` | `model_family: "gpt5"` |
|-----------|------------------------|------------------------|
| **Max tokens parameter** | `max_tokens` | `max_completion_tokens` (auto-converted) |
| **System message role** | `system` | `developer` |
| **Sampling parameters** | Always sent (temperature, top_p, penalties) | Sent unless `reasoning_effort` is present |

> **Auto-detection:** The client reads `model_family` from the config and automatically converts `max_tokens` → `max_completion_tokens` and `system` → `developer` role for `gpt5` family models.  No manual API changes needed.

### Reasoning vs. Non-Reasoning Models

Two models can share the **same Azure deployment** but behave differently depending on their configuration:

| | Non-reasoning (e.g. `gpt51`) | Reasoning (e.g. `gpt5_reasoning`) |
|-|-------------------------------|-----------------------------------|
| **`reasoning_effort`** | Not set | `low` / `medium` / `high` |
| **`temperature`** | Set (e.g. `0.1`) | Omitted automatically |
| **Sampling params** | `temperature`, `top_p`, `frequency_penalty`, `presence_penalty` sent | All omitted (API rejects them for reasoning) |
| **UI label** | Model key as-is (e.g. "gpt51") | Model key + " (reasoning)" suffix |
| **Usage** | Standard completions | Extended chain-of-thought reasoning with dedicated reasoning tokens |

Example — both use the `gpt-5.1` deployment but with different parameters:

```yaml
    gpt51:                          # ← Standard mode
      deployment_name: "gpt-5.1"
      model_family: "gpt5"
      temperature: 0.1

    gpt5_reasoning:                 # ← Reasoning mode (same deployment)
      deployment_name: "gpt-5.1"
      model_family: "gpt5"
      reasoning_effort: "medium"
```

### Adding a New Model — Step by Step

The fastest way is the **`add_model.py` CLI tool** — it handles everything (YAML config + prompt copy) in one command:

#### Option A — Interactive CLI tool (recommended)

```bash
python tools/add_model.py
```

The tool walks you through each step with prompts, shows existing models, recommends the best prompt source, and asks for confirmation before applying changes.

#### Option B — Non-interactive (scriptable)

```bash
# Standard model
python tools/add_model.py \
    --key gpt45 \
    --deployment "gpt-4.5" \
    --family gpt4 \
    --max-tokens 4096 \
    --temperature 0.1

# Reasoning model (omits temperature, adds reasoning_effort)
python tools/add_model.py \
    --key o4_mini \
    --deployment "o4-mini" \
    --family gpt5 \
    --max-tokens 16384 \
    --reasoning-effort medium

# Specify which model to copy prompts from
python tools/add_model.py \
    --key gpt45 \
    --deployment "gpt-4.5" \
    --family gpt4 \
    --copy-prompts-from gpt4o

# Skip prompt copy entirely (rely on fallback chain)
python tools/add_model.py \
    --key gpt45 \
    --deployment "gpt-4.5" \
    --family gpt4 \
    --no-prompts
```

**CLI Parameters:**

| Parameter | Required | Description |
|-----------|:--------:|-------------|
| `--key` | ✅ | Internal model key (e.g. `gpt45`, `o4_mini`) — becomes API/CLI name and prompt directory |
| `--deployment` | ✅ | Azure deployment name as shown in AI Foundry portal |
| `--family` | ✅ | `gpt4` or `gpt5` — determines API behaviour (see Model Family table above) |
| `--model-version` | — | Model version string from deployment details |
| `--max-tokens` | — | Max response tokens (default: 4096 for gpt4, 16384 for gpt5) |
| `--temperature` | — | 0.0–2.0 (default: 0.1; omitted automatically for reasoning models) |
| `--reasoning-effort` | — | `low` / `medium` / `high` — makes it a reasoning model |
| `--copy-prompts-from` | — | Model key to copy prompt files from (auto-detected if omitted) |
| `--no-prompts` | — | Skip prompt directory creation entirely |
| `--force` | — | Overwrite if the model key already exists |

**What the tool does:**

1. **Validates** the key is unique and well-formed.
2. **Inserts** the YAML block into `config/settings.yaml` — positioned after the last model of the same family.
3. **Auto-detects** the best prompt source (same family, non-reasoning, with existing prompt files).
4. **Copies** the 4 prompt files (`classification_agent_system.md`, `dialog_agent_system.md`, `rag_agent_system.md`, `tool_calling_agent_system.md`) into `prompts/<key>/`.
5. **Prints** a summary of all changes.

#### Option C — Manual setup

If you prefer to configure manually, add a model in **2 steps**:

**Step 1 — Add the entry in `config/settings.yaml`:**

```yaml
    my_new_model:
      deployment_name: "gpt-4o-mini"   # Your deployment name in Azure AI Foundry
      model_family: "gpt4"             # "gpt4" or "gpt5"
      model_version: "2024-07-18"
      max_tokens: 4096
      temperature: 0.1
```

The key name (`my_new_model`) is arbitrary — it becomes the model identifier in the CLI, API, and web UI.  It also determines the prompt directory name.

**Step 2 — Create a prompt directory (recommended):**

Create `prompts/<model_key>/` and add one `.md` file per task type:

```
prompts/my_new_model/
├── classification_agent_system.md
├── dialog_agent_system.md
├── rag_agent_system.md
└── tool_calling_agent_system.md
```

> **Tip:** Copy from an existing model with the same family.  For example, if your new model is GPT-4-family, copy from `prompts/gpt4/`.

If you skip this step, the **prompt fallback chain** kicks in automatically:

```
prompts/{model_key}/          ← first try (e.g. prompts/my_new_model/)
prompts/{base_model}/         ← if key ends with _reasoning, strip suffix (e.g. gpt5_reasoning → gpt5)
prompts/templates/            ← final fallback (generic templates)
```

#### What happens automatically

Once you add the YAML entry (via the tool or manually) and restart the server:

- **`register_models_from_config()`** reads `settings.yaml` and creates a `ModelConfig` for each entry.
- The model appears in the **web UI** dropdown (Evaluate, Compare, Dashboard).
- The **`/api/models`** endpoint includes it (with a `(reasoning)` suffix if the key ends with `_reasoning`).
- The SDK uses the correct API parameters based on `model_family` and `reasoning_effort` — no code changes needed.

#### Quick reference

| What | Where | Required? |
|------|-------|:---------:|
| Model configuration | `config/settings.yaml` → `azure.models.<key>` | ✅ |
| Prompt templates | `prompts/<key>/` → `{type}_agent_system.md` | Recommended |
| Code changes | None — registration is automatic | — |
| Server restart | Required to pick up new YAML entries | ✅ |

> **⚠️ Restart required:** Model registration happens **once at startup** — `register_models_from_config()` reads `settings.yaml` when the app initialises and is **not hot-reloaded**.  After adding or modifying a model entry, stop the server and run `python app.py` again.  On restart, the new model will appear in the logs and in every UI dropdown automatically.

### Acceptance Thresholds & Migration Readiness

The comparison report includes a **migration readiness** verdict (`PASS` / `FAIL` / `NOT_CONFIGURED`) based on configurable acceptance thresholds in `settings.yaml`:

```yaml
evaluation:
  acceptance_thresholds:
    classification:
      accuracy: 0.90
      consistency: 0.85
      max_latency_ms: 3000
    dialog:
      quality_score: 0.80
      consistency: 0.80
      max_latency_ms: 5000
    rag:
      groundedness: 0.85
      relevance: 0.80
      max_latency_ms: 5000
    tool_calling:
      tool_selection_accuracy: 0.90
      parameter_accuracy: 0.85
      max_latency_ms: 4000
    general:
      quality_score: 0.75
      max_latency_ms: 5000
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
| **RBAC roles** | Your identity (or the Service Principal) needs the roles listed below on the **AI Services resource** (or its resource group) that backs the Foundry project |

#### Required RBAC Roles for Foundry Evaluations

| Role | Why it's needed |
|------|-----------------|
| **Azure AI Developer** | Create evaluations, create runs, upload datasets to the Foundry project |
| **Cognitive Services OpenAI User** | Call the judge/grader model deployments used by the LLM-as-judge evaluators |
| **Storage Blob Data Contributor** | Upload evaluation datasets (JSONL files) to the project's backing storage |

> **Assign roles with Azure CLI:**
>
> ```bash
> # Replace <SP_OR_USER_OBJECT_ID> with the Service Principal appId or user objectId
> # Replace <SCOPE> with the AI Services resource ID or resource group ID
> az role assignment create --assignee <SP_OR_USER_OBJECT_ID> \
>     --role "Azure AI Developer" --scope <SCOPE>
> az role assignment create --assignee <SP_OR_USER_OBJECT_ID> \
>     --role "Cognitive Services OpenAI User" --scope <SCOPE>
> az role assignment create --assignee <SP_OR_USER_OBJECT_ID> \
>     --role "Storage Blob Data Contributor" --scope <SCOPE>
> ```
>
> **Tip:** `deploy.ps1` assigns these roles automatically when creating or verifying the Service Principal.

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
| **Classification** | `coherence`, `fluency`, `relevance`, `task_adherence`, `similarity`, `safety_violence`*, `safety_hate_unfairness`* |
| **Dialog** | `coherence`, `fluency`, `relevance`, `intent_resolution`, `task_adherence`, `safety_violence`*, `safety_hate_unfairness`* |
| **General** | `coherence`, `fluency`, `relevance`, `response_completeness`, `safety_violence`*, `safety_hate_unfairness`* |
| **RAG** | `coherence`, `fluency`, `relevance`, `groundedness`, `similarity`, `response_completeness`, `safety_violence`*, `safety_hate_unfairness`* |
| **Tool Calling** | `coherence`, `fluency`, `relevance`, `task_adherence`, `response_completeness`, `safety_violence`*, `safety_hate_unfairness`* |

> \* Safety evaluators are optional — controlled by `include_safety_evaluators` in `settings.yaml`. If a Foundry run fails with safety evaluators, the system automatically retries without them.

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
| Classification (20 scenarios, 7 evaluators) | ~140 calls ≈ 210K tokens |
| Dialog (15 scenarios, 7 evaluators) | ~105 calls ≈ 157K tokens |
| RAG (10 scenarios, 8 evaluators) | ~80 calls ≈ 120K tokens |
| Tool Calling (10 scenarios, 7 evaluators) | ~70 calls ≈ 105K tokens |

Cost depends on the judge model pricing.  With `gpt-4.1` at $2.50/M input + $10/M output, a full classification run costs approximately **$0.50–0.80 USD**.  A full 5-type evaluation costs approximately **$1.50–2.50 USD**.

### Graceful Degradation

If the Foundry SDK is not installed or the configuration is missing, the feature is **silently disabled** — all local evaluations continue to work normally without any error.  The **Include Foundry LLM-as-judge** toggle simply doesn't appear in the UI.

---
## ☁️ Deployment to Azure

The project uses **[Azure Developer CLI (azd)](https://learn.microsoft.com/azure/developer/azure-developer-cli/)** together with **Bicep** infrastructure-as-code templates to provision and deploy all Azure resources in a single command.

### Infrastructure Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Resource Group  (rg-<environmentName>)                             │
│                                                                     │
│  ┌──────────────┐  ┌──────────────────────┐  ┌───────────────────┐ │
│  │  Log         │  │  Application         │  │  Container Apps   │ │
│  │  Analytics   │  │  Insights            │  │  Environment      │ │
│  │  Workspace   │  │  + Dashboard         │  │  (cae-…)          │ │
│  └──────┬───────┘  └──────────┬───────────┘  └────────┬──────────┘ │
│         │                     │                       │            │
│         └─────────────────────┼───────────────────────┘            │
│                               │                                    │
│  ┌──────────────┐  ┌──────────┴───────────┐  ┌───────────────────┐ │
│  │  Azure       │  │  Container App       │  │  User-Assigned    │ │
│  │  Container   │  │  (web service)       │  │  Managed Identity │ │
│  │  Registry    │  │  Flask on port 5000  │  │  (id-web-…)       │ │
│  └──────────────┘  └──────────────────────┘  └───────────────────┘ │
│                                                                     │
│  RBAC role assignments (optional, if resource IDs provided):        │
│  • Cognitive Services OpenAI User → Azure OpenAI account            │
│  • Azure AI Developer → AI Foundry project                          │
│  • AcrPull → Container Registry                                     │
└─────────────────────────────────────────────────────────────────────┘
```

The Bicep templates are located in the `infra/` folder:

| File | Purpose |
|------|---------|
| `infra/main.bicep` | Entry point — orchestrates all resources using [Azure Verified Modules (AVM)](https://azure.github.io/Azure-Verified-Modules/) |
| `infra/main.parameters.json` | Parameter file — values are populated from `azd` environment variables |
| `infra/modules/acr-access.bicep` | Assigns the **AcrPull** role to the managed identity on the Container Registry |
| `infra/modules/openai-access.bicep` | Assigns **Cognitive Services OpenAI User** role on the Azure OpenAI account |
| `infra/modules/foundry-access.bicep` | Assigns **Azure AI Developer** role on the AI Foundry project |

### What Gets Deployed

| Resource | Details |
|----------|---------|
| **Resource Group** | `rg-<environmentName>` |
| **Log Analytics Workspace** | Centralized logging |
| **Application Insights + Dashboard** | Monitoring, telemetry, and pre-built dashboard |
| **Azure Container Registry (Basic)** | Hosts the Docker image |
| **Container Apps Environment** | Serverless container host |
| **User-Assigned Managed Identity** | Keyless authentication — no API keys needed |
| **Container App** | Flask web service (1 vCPU, 2 Gi memory, scale 0–3 replicas) |
| **RBAC Role Assignments** | Automatic role binding for Azure OpenAI and AI Foundry (if resource IDs provided) |

### Authentication Model

The deployment uses a **User-Assigned Managed Identity** instead of API keys or Service Principal credentials:

- The identity's `AZURE_CLIENT_ID` is injected as an environment variable into the Container App.
- `DefaultAzureCredential` in the SDK automatically picks it up.
- Bicep assigns the required RBAC roles (`Cognitive Services OpenAI User`, `Azure AI Developer`, `AcrPull`) to the identity.
- **No secrets are stored** in environment variables or Container Apps secrets.

### Prerequisites

| Tool | Required | Install |
|------|:--------:|---------|
| [Azure Developer CLI (`azd`)](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) | ✅ | `winget install Microsoft.Azd` |
| [Azure CLI (`az`)](https://aka.ms/installazurecliwindows) | ✅ | `winget install Microsoft.AzureCLI` |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | ✅ | Required to build the container image |
| Azure subscription with **Contributor** role | ✅ | — |
| Azure OpenAI resource with model deployments | ✅ | Any supported models (GPT-4o, GPT-4.1, GPT-5.2, etc.) |

### Step 1 — Authenticate

```powershell
# Log in to Azure Developer CLI (opens browser)
azd auth login

# Log in to Azure CLI (needed for RBAC operations)
az login
```

### Step 2 — Initialize the Environment

```powershell
# Create a new azd environment (choose a unique name)
azd env new my-migration-eval
```

### Step 3 — Configure Environment Variables

Set the required and optional parameters that the Bicep templates consume:

```powershell
# Required — Azure OpenAI endpoint
azd env set AZURE_OPENAI_ENDPOINT "https://<your-openai-resource>.openai.azure.com"

# Optional — AI Foundry project endpoint (for LLM-as-judge evaluation)
azd env set FOUNDRY_PROJECT_ENDPOINT "https://<your-hub>.services.ai.azure.com/api/projects/<your-project>"

# Optional — Automatic RBAC assignment for Azure OpenAI
# Provide the full resource ID so Bicep assigns "Cognitive Services OpenAI User" automatically
azd env set AZURE_OPENAI_ACCOUNT_RESOURCE_ID "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account-name>"

# Optional — Automatic RBAC assignment for AI Foundry project
# Provide the full resource ID so Bicep assigns "Azure AI Developer" automatically
azd env set AI_FOUNDRY_PROJECT_RESOURCE_ID "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>"
```

> **Tip:** If you omit the resource ID parameters, everything still works — you just need to assign the RBAC roles manually in the Azure Portal.

### Step 4 — Provision & Deploy

```powershell
# Provision infrastructure AND deploy the application in one command
azd up
```

`azd up` performs the following steps automatically:

1. **Provision** — Deploys all Bicep templates (`infra/main.bicep`) to create the Azure resources.
2. **Build** — Builds the Docker image from the `Dockerfile`.
3. **Push** — Pushes the image to the Azure Container Registry.
4. **Deploy** — Updates the Container App with the new image.

You will be prompted to select:
- **Azure subscription** — the subscription to deploy into.
- **Azure location** — the region for all resources (e.g. `swedencentral`, `eastus2`).

The deployment takes approximately 5–8 minutes on the first run.

### Step 5 — Access the Application

Once deployment completes, `azd` outputs the public URL:

```
SERVICE_WEB_ENDPOINT_URL = https://ca-mymigrationeval-xxxxxx.niceocean-xxxxxxxx.swedencentral.azurecontainerapps.io
```

Open this URL in your browser to access the web interface.

### Environment Variables Reference

| Variable | Required | Description |
|----------|:--------:|-------------|
| `AZURE_OPENAI_ENDPOINT` | ✅ | Azure OpenAI endpoint URL |
| `FOUNDRY_PROJECT_ENDPOINT` | — | AI Foundry project endpoint (enables LLM-as-judge) |
| `AZURE_OPENAI_ACCOUNT_RESOURCE_ID` | — | Full resource ID of the OpenAI account (enables automatic RBAC) |
| `AI_FOUNDRY_PROJECT_RESOURCE_ID` | — | Full resource ID of the AI Foundry project (enables automatic RBAC) |

### Subsequent Deployments

```powershell
# Redeploy code only (after changing app code, no infra changes)
azd deploy

# Re-provision infrastructure + redeploy code
azd up

# Preview what infrastructure changes would be applied
azd provision --preview
```

### Container App Configuration

| Setting | Value |
|---------|-------|
| CPU | 1.0 vCPU |
| Memory | 2 Gi |
| Min replicas | 0 (scale-to-zero when idle — cost savings) |
| Max replicas | 3 |
| Scale rule | HTTP concurrent requests > 20 |
| Ingress | External HTTPS (port 5000), HTTP→HTTPS redirect |
| Health probes | Liveness (`/api/health`, every 30 s) + Readiness (`/api/health`, every 10 s) |

### Monitoring

The deployment includes **Application Insights** and a pre-built **dashboard** automatically:

```powershell
# View live Container App logs
az containerapp logs show -n <container-app-name> -g rg-<environment-name> --follow

# Check running status
az containerapp show -n <container-app-name> -g rg-<environment-name> --query properties.runningStatus

# List revisions
az containerapp revision list -n <container-app-name> -g rg-<environment-name> -o table
```

You can also view telemetry in the Azure Portal → Application Insights resource created in the resource group.

### Tear Down

```powershell
# Remove ALL Azure resources created by azd (Resource Group + everything inside)
azd down

# Or with force (no confirmation prompt)
azd down --force --purge
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

# Import an external topic (source prompt + test data → archived topic with target prompts)
python tools/import_topic.py --topic "My Topic" --source-class-prompt prompt.txt --class-test-data data.json

# Add a new model (interactive — asks questions step by step)
python tools/add_model.py

# Add a new model (non-interactive — all parameters on the command line)
python tools/add_model.py --key gpt45 --deployment "gpt-4.5" --family gpt4
python tools/add_model.py --key o4_mini --deployment "o4-mini" --family gpt5 --reasoning-effort medium
python tools/add_model.py --key gpt45 --deployment "gpt-4.5" --family gpt4 --copy-prompts-from gpt4o
```

> **Note:** The CLI `evaluate` and `compare` subcommands currently support `classification`, `dialog`, `general`, and `all`.  RAG and tool calling evaluations are available via the **web UI** and **REST API** only.

Results are automatically saved to `data/results/` as JSON files.

---

## 🌐 REST API Reference

All endpoints are available at `http://127.0.0.1:<port>/api/`.

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/login` | Login page (email + OTP) |
| `POST` | `/api/auth/login` | Step 1: send OTP code to email |
| `POST` | `/api/auth/verify` | Step 2: verify OTP code, create session |
| `POST` | `/api/auth/logout` | Clear session |
| `GET` | `/api/auth/me` | Get current authenticated user info |

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
| `GET` | `/api/data/classification` | Classification scenarios |
| `GET` | `/api/data/dialog` | Dialog scenarios |
| `GET` | `/api/data/general` | General scenarios |
| `GET` | `/api/data/rag` | RAG scenarios |
| `GET` | `/api/data/tool_calling` | Tool calling scenarios |
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
| `GET` | `/api/prompts/health` | Prompt health analysis — checks consistency and completeness |

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
| `POST` | `/api/foundry/scores` | Retrieve Foundry LLM-as-judge scores for an evaluation run |

### Topic Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/topics` | List all topics (active + archived) |
| `POST` | `/api/topics/import` | Import external topic from uploaded prompt(s) + test data (multipart form) |
| `POST` | `/api/topics/activate` | Switch to an archived topic (restores prompts + data) |
| `POST` | `/api/topics/archive` | Archive the current active topic |
| `DELETE` | `/api/topics/<name>` | Delete an archived topic |

### Log Streaming

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/logs` | Fetch backend logs with offset pagination |

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

### RAG Evaluation

| Metric | Description | How it's calculated |
|--------|-------------|---------------------|
| Groundedness | Whether the response is grounded in provided context | Context keyword overlap in model response |
| Relevance | How well the response addresses the ground truth | Ground truth keyword overlap in model response |
| Format compliance | Correct output format | Structural validation |
| Completeness | All required response elements present | Content coverage check |
| Context utilisation | How effectively the model uses the provided documents | Keyword extraction and matching |

### Tool Calling Evaluation

| Metric | Description | How it's calculated |
|--------|-------------|---------------------|
| Tool selection accuracy | Whether the correct tool(s) are selected | Expected tool names found in response |
| Parameter extraction accuracy | Whether parameters are correctly extracted | Expected parameter values found in response |
| Format compliance | Correct output format | Structural validation |
| Completeness | All required tool call elements present | Content coverage check |

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

**RAG dimensions:** Groundedness, Relevance, Format Compliance, Completeness.

**Tool Calling dimensions:** Tool Selection Accuracy, Parameter Accuracy, Format Compliance, Completeness.

**Latency dimensions:** Mean Latency, P95, Std Dev, Cost/Request, Cache Hit Rate, Reasoning Token %, Tokens/sec.

**Consistency dimensions:** Reproducibility, Format Consistency.

**Foundry LLM-as-judge dimensions (1–5 scale):** Coherence, Fluency, Relevance, Similarity, Task Adherence, Intent Resolution, Response Completeness, Groundedness, Safety: Violence, Safety: Hate/Unfairness.

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
| `UserStore` | `src.auth.user_store` | SQLite-backed user store — get_or_create, email-to-slug conversion |
| `CodeManager` | `src.auth.code_manager` | OTP generation (SHA-256 hashed), verification with TTL and attempt limits |
| `EmailSender` | `src.auth.email_sender` | Abstract email backend — `SmtpEmailSender` (production) + `ConsoleEmailSender` (dev) |
| `UserContext` | `src.auth.user_context` | Per-user directory layout, path resolution, and first-login seeding |
| `AzureOpenAIClient` | `src.clients.azure_openai` | Wraps the OpenAI SDK — connection management, chat completions, streaming |
| `ModelEvaluator` | `src.evaluation.evaluator` | Runs classification/dialog/general/RAG/tool_calling evaluations against a single model |
| `EvaluationResult` | `src.evaluation.evaluator` | Dataclass container for evaluation output — serialises to/from JSON |
| `ModelComparator` | `src.evaluation.comparator` | Compares evaluation results between two models with significance analysis |
| `ComparisonReport` | `src.evaluation.comparator` | Dataclass for comparison output — dimensions, winner, recommendations |
| `MetricsCalculator` | `src.evaluation.metrics` | Computes classification metrics (accuracy, F1, kappa, confusion matrix, calibration), dialog quality metrics (rule compliance, empathy, optimal similarity, resolution efficiency), RAG metrics (groundedness, relevance), tool calling metrics (tool selection accuracy, parameter accuracy), latency & cost analytics, and consistency scoring.  Includes case-insensitive category normalisation with alias support |
| `FoundryEvaluator` | `src.evaluation.foundry_evaluator` | Submits evaluation data to Microsoft Foundry Control Plane for LLM-as-judge quality evaluation.  Handles JSONL export (with type-specific converters for all 5 eval types), dataset upload, evaluation creation, run polling, and automatic retry with safety evaluator fallback |
| `PromptManager` | `src.utils.prompt_manager` | Prompt editing, versioning, AI generation (8 prompts + 5 datasets with JSON sanitisation & retry), topic archival, data sync, synthetic data regeneration |
| `PromptLoader` | `src.utils.prompt_loader` | Template loading from disk with in-memory caching |
| `DataLoader` | `src.utils.data_loader` | Loads synthetic test scenarios from JSON files |

### Technology Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+ / Flask |
| Frontend | Tailwind CSS (CDN) + Fluent 2 design system + Chart.js |
| AI API | Azure OpenAI SDK (`openai` package) |
| Config | YAML + `.env` with variable substitution |
| Storage | File-based JSON for data + SQLite for user auth (`data/auth.db`) |
| Caching | `diskcache` for API responses |

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [Migration Guide](docs/migration_guide.md) | Comprehensive model migration playbook |
| [Prompt Design](docs/prompt_design.md) | Best practices: formatting, drift avoidance, caching |
| [Security & Governance](docs/security_guide.md) | Data protection, sandbox tools, content filtering, audit |

---

## 📄 License

MIT License

---

*Last Updated: March 2026*
