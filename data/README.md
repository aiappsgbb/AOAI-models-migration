# Golden Test Datasets

Pre-built evaluation datasets for Azure OpenAI model migration testing. Each `.jsonl` file is directly compatible with `MigrationEvaluator` and `load_test_cases()` from `src/evaluate/core.py`.

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

## Format

Each line is a JSON object matching the `TestCase` dataclass:

```json
{
  "prompt": "User question or input",
  "system_prompt": "Detailed system instructions",
  "expected_output": "Reference answer for evaluation",
  "context": "Retrieved documents (RAG scenarios)",
  "tools": [{"type": "function", "function": {"name": "...", "parameters": {...}}}],
  "ground_truth_label": "correct_label",
  "metadata": {"category": "billing", "complexity": "medium"}
}
```

Only `prompt` is required. All other fields are optional.

### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | ✅ | The user message / query |
| `system_prompt` | string | Optional | System instructions (defaults to "You are a helpful assistant.") |
| `expected_output` | string | Optional | Reference answer for comparison |
| `context` | string | Optional | Retrieved documents (RAG) |
| `tools` | list[dict] | Optional | OpenAI function schemas (tool calling) |
| `ground_truth_label` | string | Optional | Correct label (classification) |
| `metadata` | dict | Optional | Tags for filtering (`category`, `complexity`, `domain`) |

## Quick Start

### Load and inspect test cases

```python
from src.evaluate.core import load_test_cases

cases = load_test_cases("data/golden_rag.jsonl")
print(f"Loaded {len(cases)} test cases")
print(cases[0].prompt[:100])
```

### Run A/B evaluation with MigrationEvaluator

```python
from src.evaluate.core import MigrationEvaluator

evaluator = MigrationEvaluator(
    source_model="gpt-4o",
    target_model="gpt-4.1",
    test_cases="data/golden_rag.jsonl",      # pass file path directly
    metrics=["coherence", "fluency", "relevance", "groundedness"],
)
report = evaluator.run()
report.print_report()
```

### Evaluate specific scenario types

```python
# Classification with ground-truth comparison
evaluator = MigrationEvaluator(
    source_model="gpt-4o",
    target_model="gpt-4.1",
    test_cases="data/golden_classification.jsonl",
    metrics=["relevance"],
)

# Tool calling
evaluator = MigrationEvaluator(
    source_model="gpt-4o",
    target_model="gpt-4.1",
    test_cases="data/golden_tool_calling.jsonl",
    metrics=["relevance", "coherence"],
)
```

### Combine multiple files

```python
from src.evaluate.core import load_test_cases

all_cases = []
for f in ["data/golden_rag.jsonl", "data/golden_classification.jsonl", "data/golden_translation.jsonl"]:
    all_cases.extend(load_test_cases(f))

evaluator = MigrationEvaluator(
    source_model="gpt-4o",
    target_model="gpt-4.1",
    test_cases=all_cases,
    metrics=["coherence", "fluency", "relevance"],
)
```

### Filter by metadata

```python
cases = load_test_cases("data/golden_rag.jsonl")
high_complexity = [c for c in cases if c.metadata.get("complexity") == "high"]
billing_cases = load_test_cases("data/golden_classification.jsonl")
billing_only = [c for c in billing_cases if c.metadata.get("category") == "billing"]
```

## Creating Your Own Golden Datasets

For production evaluation, build datasets from **your actual traffic**:

### Option 1: Stored Completions (Recommended)

Enable `"store": true` on API calls, then export:

```python
import json
from openai import AzureOpenAI

client = AzureOpenAI(...)
completions = client.chat.completions.list(limit=100)

with open("data/my_golden_set.jsonl", "w") as f:
    for c in completions:
        test_case = {
            "prompt": c.messages[-1]["content"],
            "system_prompt": c.messages[0]["content"],
            "expected_output": c.choices[0].message.content,
            "metadata": {"source": "stored_completions", "model": c.model},
        }
        f.write(json.dumps(test_case) + "\n")
```

See also [Building Golden Datasets](../docs/building-golden-datasets.md) for a full walkthrough.

### Option 2: APIM Request Logs

If you route traffic through Azure API Management with GenAI diagnostic logging:

```python
import json
from azure.monitor.query import LogsQueryClient
from azure.identity import DefaultAzureCredential

client = LogsQueryClient(DefaultAzureCredential())
result = client.query_workspace(
    workspace_id="<your-workspace-id>",
    query="""
    ApiManagementGatewayLogs
    | where OperationName == "ChatCompletions_Create"
    | project TimeGenerated, RequestBody, ResponseBody
    | take 100
    """,
    timespan="P7D",
)

with open("data/from_apim.jsonl", "w") as f:
    for row in result.tables[0].rows:
        req = json.loads(row[1])
        resp = json.loads(row[2])
        test_case = {
            "prompt": req["messages"][-1]["content"],
            "system_prompt": req["messages"][0]["content"],
            "expected_output": resp["choices"][0]["message"]["content"],
            "metadata": {"source": "apim", "timestamp": str(row[0])},
        }
        f.write(json.dumps(test_case) + "\n")
```

### Option 3: Convert Existing JSON Samples

Convert from the repo's sample JSON files in `model_migration_eval/tools/import_test/samples_json/`:

```python
import json

with open("model_migration_eval/tools/import_test/samples_json/test_data_rag.json") as f:
    samples = json.load(f)

with open("data/converted_rag.jsonl", "w") as f:
    for s in samples:
        test_case = {
            "prompt": s["user_message"],        # adjust field names to your format
            "system_prompt": s.get("system_message", "You are a helpful assistant."),
            "expected_output": s.get("expected_response"),
            "context": s.get("context"),
            "metadata": {"source": "converted", "original_id": s.get("id")},
        }
        f.write(json.dumps(test_case) + "\n")
```

### Option 4: Manual Curation

Create a `.jsonl` file with one test case per line:

```jsonl
{"prompt": "What is our refund policy?", "system_prompt": "You are a customer service agent.", "expected_output": "Our refund policy allows...", "context": "Policy: Returns within 30 days..."}
{"prompt": "Classify: I need to reset my password", "system_prompt": "Classify into: billing, technical, account.", "expected_output": "account", "ground_truth_label": "account"}
```

## PII Redaction

If your golden dataset contains real production data with personally identifiable information, use the built-in PII scrubbing tool before evaluation:

```bash
python -c "from src.pii import redact_jsonl_file; redact_jsonl_file('data/my_export.jsonl', 'data/my_export_clean.jsonl', language='it')"
```

See [`docs/building-golden-datasets.md`](../docs/building-golden-datasets.md#pii-redaction-scrubbing-production-data-for-evaluation) for details and selective category redaction.
