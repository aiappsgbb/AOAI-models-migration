# Evaluation Guide

The evaluation framework helps you **detect regressions** before deploying a new model in production. It works by running the same prompts through both your current (source) and target models, then comparing the outputs using automated metrics and LLM-as-Judge scoring.

> **Why evaluate?** Auto-upgrades and model swaps can subtly change behavior — a model might be "better" on benchmarks but worse for your specific prompts, schemas, or tone. Always evaluate on **your** data before switching.

---

## Evaluation Approaches

This repo supports three evaluation approaches, from lightweight to enterprise:

| Approach | Where It Runs | Best For |
|----------|--------------|----------|
| **Built-in LLM-as-Judge** | Local (Python) | Quick A/B comparisons, no extra dependencies |
| **Azure AI Evaluation SDK** | Local (Python) | Pre-built quality metrics (groundedness, fluency, etc.) |
| **Azure AI Foundry Cloud Eval** | Cloud (Foundry portal) | Tracking metrics across model generations, team dashboards, CI/CD |

### 1. Built-in LLM-as-Judge

The `MigrationEvaluator` class runs the same prompts through both models and uses an LLM to judge the outputs. No extra Azure resources needed beyond your OpenAI deployments.

```python
from src.evaluate.scenarios import create_rag_evaluator

evaluator = create_rag_evaluator(
    source_model="gpt-4o",
    target_model="gpt-4.1",
)
report = evaluator.run()
report.print_report()
```

**What it does:**
1. Sends each test case to both the source and target models
2. Collects the responses
3. Uses an LLM judge to score each response on scenario-specific metrics
4. Generates a comparison report with pass/fail per metric

### 2. Azure AI Evaluation SDK (Local)

The `azure-ai-evaluation` SDK provides pre-built evaluators for quality metrics. It runs locally but uses Azure OpenAI as the judge model.

```python
from src.evaluate.local_eval import run_local_evaluation

results = run_local_evaluation(
    data=report.to_dataframe(),
    evaluators=["groundedness", "relevance", "coherence", "fluency"],
    model_config={
        "azure_endpoint": AZURE_OPENAI_ENDPOINT,
        "azure_deployment": "gpt-4o",  # judge model
    }
)
```

**Supported evaluators:**
- `groundedness` — Is the response grounded in the provided context?
- `relevance` — Does the response address the question?
- `coherence` — Is the response logically structured?
- `fluency` — Is the language natural and readable?
- `similarity` — How similar is the response to a reference answer?
- `f1_score` — Token-level overlap with reference

### 3. Azure AI Foundry Cloud Evaluation

Upload your eval data to Azure AI Foundry and run evaluations in the cloud. Results are stored persistently, enabling you to **compare runs across model generations** in the Foundry portal.

```python
from src.evaluate.foundry import FoundryEvalsClient

foundry = FoundryEvalsClient()
results = foundry.evaluate_cloud(
    report,
    evaluators=["coherence", "fluency", "relevance"]
)
```

> 📖 **[Cloud Eval Tracking deep dive →](cloud-eval-tracking-across-models.md)** — how to create reusable eval definitions, compare runs side-by-side in the Foundry portal with statistical significance testing, and integrate into CI/CD pipelines.

---

## Pre-built Scenarios

Each scenario includes sample test data and adapted metrics — ready to run out of the box. Use them as-is for a quick smoke test, or replace the test data with your own production samples.

### RAG (Retrieval-Augmented Generation)

**Metrics:** Groundedness, Relevance, Coherence

Tests whether the model generates accurate answers from provided context documents without hallucinating.

```python
from src.evaluate.scenarios import create_rag_evaluator

evaluator = create_rag_evaluator(
    source_model="gpt-4o",
    target_model="gpt-5.1",
)
report = evaluator.run()
```

**Included test cases (8):**
- Company policy Q&A
- Financial report analysis
- Legal document interpretation
- Technical documentation lookup
- Multi-document synthesis

### Tool Calling

**Metrics:** Tool Accuracy, Parameter Accuracy

Tests whether the model selects the correct tool and passes the right parameters.

```python
from src.evaluate.scenarios import create_tool_calling_evaluator

evaluator = create_tool_calling_evaluator(
    source_model="gpt-4o",
    target_model="gpt-4.1",
)
report = evaluator.run()
```

**Included test cases (8):**
- Weather API calls
- Calendar event creation
- Email composition
- Database queries
- Multi-step tool chains

### Translation

**Metrics:** Fluency, Coherence, Relevance

Tests translation quality across language pairs and domains.

```python
from src.evaluate.scenarios import create_translation_evaluator

evaluator = create_translation_evaluator(
    source_model="gpt-4o",
    target_model="gpt-5.2",
)
report = evaluator.run()
```

**Included test cases (10):**
- French ↔ English (business)
- English → German (technical)
- Legal/regulatory translation
- Marketing copy localization

### Classification

**Metrics:** Accuracy, Consistency, Relevance

Tests classification tasks across multiple domains.

```python
from src.evaluate.scenarios import create_classification_evaluator

evaluator = create_classification_evaluator(
    source_model="gpt-4o",
    target_model="gpt-4.1",
)
report = evaluator.run()
```

**Included test cases (16):**
- Sentiment analysis (positive/negative/neutral)
- Support ticket categorization
- Intent detection
- Priority assignment

---

## Using Your Own Test Data

Replace the built-in test cases with your production data for a realistic evaluation:

```python
from src.evaluate.core import MigrationEvaluator

# Define your test cases
my_test_cases = [
    {
        "input": "What is our refund policy for premium subscribers?",
        "context": "Premium subscribers can request a full refund within 30 days...",
        "expected_output": "Premium subscribers are eligible for a full refund within 30 days of purchase."
    },
    # ... more cases from your production logs
]

evaluator = MigrationEvaluator(
    source_model="gpt-4o",
    target_model="gpt-5.1",
    test_cases=my_test_cases,
    metrics=["groundedness", "relevance", "coherence"],
    system_prompt="You are a customer support assistant. Answer based on the provided context only."
)

report = evaluator.run()
report.print_report()
```

**Tips for building your test set:**
- Sample 50-100 representative prompts from production logs
- Include edge cases and failure modes you've seen
- Include the full context (system prompt, tools, documents) — not just the user message
- If you have expected outputs, include them for reference-based metrics

---

## Interpreting Results

### Pass/Fail Thresholds

By default, the evaluator uses these acceptance thresholds:

| Metric | Threshold | Meaning |
|--------|-----------|---------|
| Groundedness | ≥ 4.0 / 5.0 | Response stays within provided context |
| Relevance | ≥ 4.0 / 5.0 | Response addresses the question |
| Coherence | ≥ 4.0 / 5.0 | Response is logically structured |
| Fluency | ≥ 4.0 / 5.0 | Language is natural and readable |
| Tool Accuracy | ≥ 0.9 | Correct tool selected 90%+ of the time |
| Parameter Accuracy | ≥ 0.85 | Correct parameters passed 85%+ of the time |

### What to Do When a Metric Drops

1. **Small drop (< 5%)** — Usually acceptable. Verify on a larger test set.
2. **Moderate drop (5-15%)** — Investigate specific failing test cases. May need prompt tuning for the new model.
3. **Large drop (> 15%)** — Consider a different target model or significantly rework prompts.

> **Prompt tuning tip:** Reasoning models (GPT-5 family) often respond better to concise, direct instructions rather than verbose chain-of-thought prompting. If you're migrating from GPT-4o, try simplifying your system prompt.

---

## Interactive Notebooks

- **[azure_openai_evaluation_guide.ipynb](../azure_openai_evaluation_guide.ipynb)** — Step-by-step walkthrough of all evaluation approaches with live code cells
- **[azure_openai_migration_technical.ipynb](../azure_openai_migration_technical.ipynb)** — Technical migration guide covering API changes

---

## Next Steps

- **[Cloud Eval Tracking](cloud-eval-tracking-across-models.md)** — track metrics across model generations in Azure AI Foundry
- **[Lifecycle Best Practices](llm-upgrade-lifecycle-best-practices.md)** — continuous evaluation as part of the upgrade lifecycle
- **[API Changes](api-changes-by-model.md)** — understand the code changes needed before evaluating
