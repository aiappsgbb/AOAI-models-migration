# Evaluation Guide

The evaluation framework helps you **detect regressions** before deploying a new model in production. It works by running the same prompts through both your current (source) and target models, then comparing the outputs using automated metrics and LLM-as-Judge scoring.

> **Why evaluate?** Auto-upgrades and model swaps can subtly change behavior — a model might be "better" on benchmarks but worse for your specific prompts, schemas, or tone. Always evaluate on **your** data before switching.

> **Why LLM-as-judge, not similarity?** This repo uses LLM-as-judge (semantic correctness, groundedness, relevance) rather than similarity-based metrics (cosine similarity, BLEU, ROUGE). Similarity metrics only measure whether the output *changed* — not whether it *improved or regressed*. Two answers can be textually different but equally correct, or textually similar but one subtly wrong. For model migration, what matters is whether the new model's answer is **correct, grounded, and relevant** — not whether it's lexically identical to the old one.

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
from src.evaluate.local_eval import run_local_evaluation, get_model_config

# get_model_config() reads from env vars (AZURE_OPENAI_ENDPOINT, etc.)
model_config = get_model_config(deployment="gpt-4o")  # judge model

results = run_local_evaluation(
    data=report.to_dataframe(),
    evaluators=["groundedness", "relevance", "coherence", "fluency"],
    model_config=model_config,
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

## Custom Evaluators: Tailoring Quality Checks to Your Domain

The built-in evaluators (groundedness, relevance, etc.) cover most migration scenarios. But if your use case has **domain-specific quality criteria** — citation format compliance, regulatory language accuracy, brand tone consistency — you can create custom evaluators in minutes.

### Approach 1: LLM-as-Judge with Custom Criteria (5 minutes)

The simplest approach — write a plain-text description of what "good" looks like, and the LLM judges each response against it.

```python
from src.evaluate.custom import create_judge_evaluator

# Define your quality criteria in plain language
citation_judge = create_judge_evaluator(
    name="citation_compliance",
    criteria="""Score the response from 1 to 5 on citation compliance:
    5: Every factual claim has a citation in [Source: Name, Date] format
    4: Most claims cited, minor formatting issues
    3: Some claims cited, some missing
    2: Few or no citations present
    1: No citations at all, or fabricated sources
    """,
    # Optional: provide examples of good/bad responses
)

# Use with MigrationEvaluator
from src.evaluate.core import MigrationEvaluator

evaluator = MigrationEvaluator(
    source_model="gpt-4o",
    target_model="gpt-5.1",
    test_cases="data/golden_rag.jsonl",
    metrics=["relevance", "coherence"],  # built-in metrics
    custom_evaluators=[citation_judge],   # your custom metric
)
report = evaluator.run()
```

### Approach 2: Prompty-Based Evaluator (10 minutes)

Create a `.prompty` file that defines a structured judge prompt. This gives you more control over the scoring rubric and is reusable across evaluations.

Create `src/evaluate/prompts/my_custom_eval.prompty`:
```yaml
---
name: Brand Tone Evaluator
description: Evaluates whether responses match the company's brand voice guidelines.
model:
  api: chat
  parameters:
    temperature: 0.0
    max_tokens: 256
inputs:
  response:
    type: string
    description: The model response to evaluate
  brand_guidelines:
    type: string
    description: The brand voice guidelines to check against
---
system:
You are a brand compliance evaluator. Score the response from 1 to 5 based on
how well it adheres to the provided brand voice guidelines.

Return ONLY a JSON object: {"score": <1-5>, "reason": "<brief explanation>"}

Brand guidelines:
{{brand_guidelines}}

Response to evaluate:
{{response}}
```

Then use it:
```python
from src.evaluate.custom import load_prompty_evaluator

brand_eval = load_prompty_evaluator(
    "src/evaluate/prompts/my_custom_eval.prompty",
    brand_guidelines="Tone: professional but warm. Avoid jargon. Use active voice. Max 3 sentences per paragraph."
)
```

### Approach 3: Code-Based Evaluator (15 minutes)

For deterministic checks that don't need an LLM — regex patterns, word counts, format validation, response time limits.

```python
from src.evaluate.custom import CodeEvaluator

# Example: Check JSON schema compliance
import json

@CodeEvaluator(name="json_schema_compliance")
def check_json_output(response: str, **kwargs) -> dict:
    """Check if the response is valid JSON with required fields."""
    try:
        data = json.loads(response)
        has_category = "category" in data
        has_priority = "priority" in data
        score = 1.0 if (has_category and has_priority) else 0.0
        return {"score": score, "reason": f"category={'✓' if has_category else '✗'}, priority={'✓' if has_priority else '✗'}"}
    except json.JSONDecodeError:
        return {"score": 0.0, "reason": "Response is not valid JSON"}

# Example: Check response length constraints
@CodeEvaluator(name="response_length")
def check_length(response: str, **kwargs) -> dict:
    """Penalize responses that are too short or too verbose."""
    word_count = len(response.split())
    if 20 <= word_count <= 500:
        return {"score": 1.0, "reason": f"{word_count} words — within range"}
    elif word_count < 20:
        return {"score": 0.3, "reason": f"{word_count} words — too brief"}
    else:
        return {"score": 0.5, "reason": f"{word_count} words — too verbose"}

# Example: Check language consistency
@CodeEvaluator(name="language_check")
def check_language(response: str, expected_language: str = "it", **kwargs) -> dict:
    """Verify the response is in the expected language."""
    # Simple heuristic — for production, use Azure AI Language detection
    italian_markers = ["il", "la", "di", "che", "è", "per", "un", "sono", "non", "con"]
    words = response.lower().split()
    marker_count = sum(1 for w in words if w in italian_markers)
    ratio = marker_count / max(len(words), 1)
    is_italian = ratio > 0.1
    score = 1.0 if is_italian else 0.0
    return {"score": score, "reason": f"Italian marker ratio: {ratio:.2f}"}
```

### Comparison: When to Use Which Approach

| Approach | Effort | Best For | Requires LLM? |
|----------|--------|----------|---------------|
| **LLM-as-Judge** | 5 min | Subjective quality (tone, style, domain accuracy) | Yes |
| **Prompty file** | 10 min | Reusable rubrics, team-shared criteria | Yes |
| **Code-based** | 15 min | Deterministic checks (format, length, schema, regex) | No |

> **💡 Combine them.** Use built-in metrics (groundedness, relevance) + one LLM-as-Judge for domain quality + one code-based check for format compliance. That covers 99% of enterprise evaluation needs.

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

> 📖 **[Building Golden Datasets deep dive →](building-golden-datasets.md)** — practical, step-by-step guide to building eval datasets from Stored Completions, APIM logs, agent traces, and synthetic generation. Covers LLM-only apps, AI Gateway apps, and agent-based apps.

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
