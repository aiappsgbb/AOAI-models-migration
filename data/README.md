# Golden Test Datasets

Pre-built evaluation datasets for Azure OpenAI model migration testing. Each `.jsonl` file is directly compatible with `MigrationEvaluator` and `load_test_cases()`.

## Available Datasets

| File | Cases | Scenario | Key Fields |
|------|-------|----------|------------|
| `golden_rag.jsonl` | 10 | RAG / grounded Q&A | prompt, context, expected_output |
| `golden_classification.jsonl` | 10 | Intent & sentiment classification | prompt, expected_output, ground_truth_label |
| `golden_tool_calling.jsonl` | 8 | Function calling & tool selection | prompt, tools, expected_output |
| `golden_translation.jsonl` | 6 | EN→IT, EN→DE, EN→ES translation | prompt, expected_output |
| `golden_summarization.jsonl` | 6 | Meeting notes, emails, incidents | prompt, expected_output |
| `golden_agent.jsonl` | 8 | Multi-step agent reasoning | prompt, tools, expected_output |
| `golden_multiturn.jsonl` | 6 | Multi-turn conversation context | prompt (with history), expected_output |

**Total: 54 test cases** across 7 scenarios.

## Quick Start

```python
from src.evaluate.core import MigrationEvaluator

# Run A/B comparison on RAG scenarios
evaluator = MigrationEvaluator(
    source_model="gpt-4o",
    target_model="gpt-4.1",
    test_cases="data/golden_rag.jsonl",   # accepts file path
    metrics=["coherence", "fluency", "relevance", "groundedness"],
)
report = evaluator.run()
report.print_report()
```

Or load test cases manually:

```python
from src.evaluate.core import load_test_cases

cases = load_test_cases("data/golden_classification.jsonl")
print(f"Loaded {len(cases)} classification test cases")
print(cases[0].prompt[:100])
```

## Creating Your Own Golden Dataset

For production evaluation, build datasets from **your actual traffic**:

### Option 1: Stored Completions (Recommended)

Enable `"store": true` on API calls, then export. See [Building Golden Datasets](../docs/building-golden-datasets.md).

### Option 2: APIM Logs

If using Azure API Management, enable GenAI diagnostic logging and query Log Analytics.

### Option 3: Manual Curation

Create a `.jsonl` file with one test case per line:

```jsonl
{"prompt": "What is our refund policy?", "system_prompt": "You are a customer service agent.", "expected_output": "Our refund policy allows...", "context": "Policy: Returns within 30 days..."}
{"prompt": "Classify: I need to reset my password", "system_prompt": "Classify into: billing, technical, account.", "expected_output": "account", "ground_truth_label": "account"}
```

### Required Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | ✅ | The user message / query |
| `system_prompt` | string | ✅ | System instructions |
| `expected_output` | string | Optional | Reference answer for comparison |
| `context` | string | Optional | Retrieved documents (RAG) |
| `tools` | list[dict] | Optional | OpenAI function schemas (tool calling) |
| `ground_truth_label` | string | Optional | Correct label (classification) |
| `metadata` | dict | Optional | Tags for filtering (category, complexity, etc.) |
