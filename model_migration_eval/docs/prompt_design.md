# Prompt Design Best Practices
## Optimizing Prompts for GPT-4.1 and GPT-5.x Models

This guide covers prompt engineering best practices for migrating from GPT-4.1 to GPT-5.x, with focus on stability, reproducibility, and performance.

---

## 1. Prompt Structure

### 1.1 Format Comparison

#### Markdown Format (Best for Instructions)

```markdown
# Role
You are a customer service classification agent.

## Task
Classify customer messages into categories.

## Rules
1. Select exactly one category
2. Provide confidence score
3. Extract key entities

## Output
Return JSON with category, confidence, entities.
```

**When to use:** Human-readable prompts, documentation-style instructions

#### YAML Format (Best for Structured Rules)

```yaml
role: classification_agent
task: classify_customer_messages

categories:
  - billing_inquiry
  - technical_support
  - sales
  - retention

rules:
  - single_category_only: true
  - confidence_required: true
  - entity_extraction: true

output_format: json
```

**When to use:** Configuration-style prompts, complex rule sets

#### JSON Format (Best for Schemas)

```json
{
  "output_schema": {
    "type": "object",
    "properties": {
      "category": {"type": "string", "enum": ["billing", "tech", "sales"]},
      "confidence": {"type": "number", "minimum": 0, "maximum": 1},
      "entities": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["category", "confidence"]
  }
}
```

**When to use:** Output specifications, API-style definitions

### 1.2 GPT-4 vs GPT-5 Structure Differences

| Aspect | GPT-4 Recommendation | GPT-5 Recommendation |
|--------|---------------------|---------------------|
| Length | Detailed (2000-4000 tokens) | Concise (500-1500 tokens) |
| Examples | 5-10 examples | 2-5 examples |
| CoT | Explicit step-by-step | Minimal (native reasoning) |
| Format | Markdown preferred | YAML/JSON acceptable |
| Rules | Exhaustive | Key rules only |

---

## 2. Avoiding Prompt Drift

### 2.1 Common Drift Causes

1. **Prompt Overload**: Too many instructions competing
2. **Contradictory Rules**: Conflicting guidance
3. **Context Dilution**: Important instructions buried
4. **Example Bias**: Examples overshadowing rules
5. **Instruction Decay**: Later instructions forgotten

### 2.2 Anti-Patterns

❌ **Bad: Overloaded Prompt**
```markdown
You are a helpful assistant. Be friendly but professional. 
Always greet the customer. Make sure to be empathetic. 
Don't use jargon. Classify their issue. Also provide solutions.
If they're angry, calm them down. Upsell when appropriate.
But don't be pushy. Remember to verify their identity...
[continues for 3000 more tokens]
```

✅ **Good: Focused Prompt**
```markdown
# Role
TELCO classification agent.

# Task
Classify customer message → single category.

# Output (JSON only)
{"category": "...", "priority": "...", "confidence": 0.0-1.0}
```

### 2.3 Drift Prevention Techniques

```python
# 1. Use priority markers
"""
CRITICAL: Always return valid JSON.
IMPORTANT: Single category only.
GUIDELINE: Prefer billing category when ambiguous.
"""

# 2. Place most important rules first and last (primacy/recency)
"""
# FIRST: Most critical rule
Return JSON format always.

# MIDDLE: Supporting details
[Categories, examples, etc.]

# LAST: Reinforce critical rule  
Remember: JSON output required.
"""

# 3. Use explicit section boundaries
"""
=== RULES (MUST FOLLOW) ===
1. ...
2. ...

=== PREFERENCES (SHOULD FOLLOW) ===
1. ...

=== OUTPUT FORMAT (REQUIRED) ===
{json schema}
"""
```

---

## 3. Classification Agent Prompts

### 3.1 GPT-4 Optimized Classification Prompt

```markdown
# CLASSIFICATION AGENT - GPT-4o

You are an expert TELCO customer service classification agent.

## YOUR TASK
Analyze customer messages and classify them accurately.

## CLASSIFICATION PROCESS
Think through this step by step:
1. Read the customer message carefully
2. Identify the primary intent
3. Match to the most appropriate category
4. Determine priority based on urgency indicators
5. Extract relevant entities (amounts, dates, etc.)
6. Assess customer sentiment
7. Decide if follow-up questions are needed

## CATEGORIES

### Primary Categories
| Code | Name | Description |
|------|------|-------------|
| BILL | Billing | Charges, payments, invoices |
| TECH | Technical | Devices, connectivity, troubleshooting |
| SALE | Sales | New services, upgrades, plans |
| ACCT | Account | Profile, settings, transfers |
| RETN | Retention | Cancellation, competitor mentions |
| SECU | Security | Fraud, privacy, access |

### Subcategories
BILL: disputed_charge, payment_arrangement, billing_explanation
TECH: service_outage, device_issue, performance_issue
[etc.]

## PRIORITY RULES
- CRITICAL: Service down + work impact, Security breach
- HIGH: Billing dispute, Escalation request
- MEDIUM: Standard inquiries
- LOW: General questions

## OUTPUT FORMAT
You MUST respond with valid JSON only. No other text.

```json
{
  "classification": {
    "category": "CODE",
    "category_name": "Full Name",
    "subcategory": "subcategory_name",
    "confidence": 0.85
  },
  "priority": "medium",
  "sentiment": "neutral",
  "key_entities": ["entity1", "entity2"],
  "requires_follow_up": false,
  "suggested_follow_up_questions": [],
  "reasoning": "Brief explanation"
}
```

## EXAMPLES

### Example 1: Clear Billing Issue
**Input:** "My bill is $50 higher than usual this month"
**Output:**
```json
{
  "classification": {
    "category": "BILL",
    "category_name": "Billing",
    "subcategory": "billing_explanation",
    "confidence": 0.92
  },
  "priority": "medium",
  "sentiment": "concerned",
  "key_entities": ["$50", "higher than usual"],
  "requires_follow_up": true,
  "suggested_follow_up_questions": ["Which specific charges seem unexpected?"],
  "reasoning": "Customer inquiring about bill increase, needs explanation"
}
```

[Additional examples...]

## IMPORTANT REMINDERS
- Return ONLY valid JSON
- Select exactly ONE category
- Confidence: 0.9+ for clear cases, 0.5-0.7 for ambiguous
- Always extract monetary amounts as entities
```

### 3.2 GPT-5 Optimized Classification Prompt

```yaml
# CLASSIFICATION AGENT - GPT-5
# Optimized for native reasoning capabilities

system_config:
  response_format: json_object
  reasoning_effort: medium

role: TELCO classification agent

task: Classify customer messages into structured output

categories:
  BILL: [disputed_charge, payment_arrangement, billing_explanation]
  TECH: [service_outage, device_issue, performance_issue]
  SALE: [plan_upgrade, add_line, new_service]
  ACCT: [contract_inquiry, address_change, account_transfer]
  RETN: [cancellation_price, cancellation_service, cancellation_relocation]
  SECU: [fraud_report, data_privacy_request, account_security]

priority_rules:
  critical: [service_down_work_impact, security_breach]
  high: [billing_dispute, escalation_request, retention_risk]
  medium: [standard_requests]
  low: [general_questions]

output_schema:
  type: object
  required: [classification, priority, sentiment]
  properties:
    classification:
      category: string
      subcategory: string
      confidence: number[0-1]
    priority: enum[critical, high, medium, low]
    sentiment: string
    key_entities: array[string]
    requires_follow_up: boolean
    reasoning_summary: string[max_50_words]

examples:
  - input: "My bill is $50 higher"
    output:
      classification: {category: BILL, subcategory: billing_explanation, confidence: 0.92}
      priority: medium
      sentiment: concerned
      key_entities: ["$50"]

constraints:
  - Single category per classification
  - Valid JSON only
  - Confidence calibration: 0.9+ clear, 0.7-0.9 likely, <0.7 uncertain
```

---

## 4. Dialog & Follow-up Prompts

### 4.1 Follow-up Question Generation

```markdown
# DIALOG AGENT

## When to Ask Follow-up Questions

| Condition | Action |
|-----------|--------|
| Confidence < 0.7 | Ask clarifying question |
| Missing critical info | Ask for specific detail |
| Multiple interpretations | Offer options |
| Customer frustrated | Minimize questions |

## Follow-up Question Banks

### Billing
- "Which charge on your bill seems incorrect?"
- "What date does the charge appear?"
- "What's the amount in question?"

### Technical
- "Which service is affected - mobile, internet, or TV?"
- "Is this happening on all devices or just one?"
- "When did you first notice this issue?"

### Sales
- "What's your monthly budget for this service?"
- "How much data do you typically use?"
- "Do you need any specific features?"

## Question Rules
1. Never ask for information already provided
2. Maximum 2 questions per response
3. Batch related questions together
4. Provide options when possible
```

---

## 5. RAG Prompt Design

### 5.1 RAG-Specific Prompt Structure

RAG prompts must enforce **groundedness** — the model should only use information from the provided context documents.

#### GPT-4 RAG Prompt (verbose, explicit grounding instructions)

```markdown
# RAG AGENT - GPT-4

## YOUR TASK
Answer the user's question using ONLY the provided context documents.

## CRITICAL RULES
Think step by step:
1. Read all context documents carefully
2. Identify which documents are relevant to the question
3. Extract the specific information that answers the question
4. Formulate your answer using ONLY information from the context
5. If the context doesn't contain the answer, say "I don't have enough information"

## GROUNDING RULES
- NEVER use information not in the context
- ALWAYS cite which document(s) you used
- If uncertain, say so explicitly
- Do not hallucinate or infer beyond the context

## OUTPUT FORMAT
Return a clear, grounded answer with document citations.
```

#### GPT-5 RAG Prompt (concise, native reasoning)

```yaml
# RAG AGENT - GPT-5
role: Grounded retrieval agent

task: Answer questions using ONLY provided context

rules:
  - Use exclusively the provided context documents
  - Cite relevant document sources
  - State clearly when context is insufficient
  - Never fabricate information

output: Grounded answer with citations
```

### 5.2 RAG Test Data Structure

Each RAG test scenario includes:

| Field | Type | Description |
|-------|------|-------------|
| `query` | string | The user's question |
| `context` | string | Retrieved document(s) provided to the model |
| `ground_truth` | string | Expected correct answer for evaluation |
| `expected_format` | string | `text`, `json`, `list`, etc. |
| `difficulty` | string | `easy`, `medium`, `hard` |
| `metadata` | object | Additional context (topic, source, etc.) |

### 5.3 Groundedness Best Practices

- Place context documents **before** the question in the prompt
- Use clear delimiters between documents (e.g. `---` or `[Document 1]`)
- Keep system prompt > 1,024 tokens for **prompt caching** benefits
- Instruct the model to quote or paraphrase from context, not generate new facts

---

## 6. Tool Calling Prompt Design

### 6.1 Tool Definition Best Practices

Well-defined tool schemas improve both **tool selection accuracy** and **parameter extraction accuracy**.

```python
# Example tool definitions for a customer service agent
tools = [
    {
        "type": "function",
        "function": {
            "name": "check_order_status",
            "description": "Check the current status of a customer order by order ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "Order ID (format: ORD-XXXXXXXX)"
                    }
                },
                "required": ["order_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_billing_history",
            "description": "Retrieve billing history for a customer account",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string", "description": "Customer ID"},
                    "months": {"type": "integer", "description": "Number of months to look back", "default": 3}
                },
                "required": ["customer_id"]
            }
        }
    }
]
```

### 6.2 GPT-4 vs GPT-5 Tool Calling Differences

| Aspect | GPT-4.1 | GPT-5.x |
|--------|---------|---------|
| Schema detail needed | Verbose descriptions | Concise descriptions sufficient |
| Parameter inference | Requires explicit examples | Infers from context |
| Multi-tool chains | Manual orchestration | Native multi-step |
| Error recovery | Manual retry logic | Self-correcting |

### 6.3 Tool Calling Test Data Structure

| Field | Type | Description |
|-------|------|-------------|
| `user_message` | string | The user's request |
| `available_tools` | array | List of tool names available |
| `expected_tool_calls` | array | Tool(s) that should be selected |
| `expected_parameters` | object | Expected parameter values |
| `expected_format` | string | Expected output format |
| `difficulty` | string | `easy`, `medium`, `hard` |
| `metadata` | object | Additional context |

---

## 7. Output Consistency Techniques

### 7.1 Structured Output Enforcement

```python
# GPT-4: Use explicit formatting instructions
gpt4_prompt = """
OUTPUT FORMAT RULES:
1. Start response with ```json
2. End response with ```
3. No text outside JSON block
4. All string values in double quotes
5. Numbers without quotes
"""

# GPT-5: Use response_format parameter
response = client.chat.completions.create(
    model="gpt-5",
    messages=messages,
    response_format={"type": "json_object"}
)
```

### 7.2 Reproducibility Settings

```python
# Maximum reproducibility configuration
reproducibility_config = {
    "temperature": 0,
    "seed": 42,
    "top_p": 1,  # Don't use with temperature=0
    "frequency_penalty": 0,
    "presence_penalty": 0
}
```

### 7.3 Output Validation

```python
from pydantic import BaseModel, Field
from typing import List, Optional

class ClassificationOutput(BaseModel):
    """Validate model outputs against expected schema"""
    
    class Classification(BaseModel):
        category: str = Field(..., pattern="^(BILL|TECH|SALE|ACCT|RETN|SECU)$")
        subcategory: str
        confidence: float = Field(..., ge=0, le=1)
    
    classification: Classification
    priority: str = Field(..., pattern="^(critical|high|medium|low)$")
    sentiment: str
    key_entities: List[str] = []
    requires_follow_up: bool = False
    reasoning_summary: Optional[str] = Field(None, max_length=200)

# Validate response
def validate_classification(response: str) -> ClassificationOutput:
    data = json.loads(response)
    return ClassificationOutput(**data)
```

---

## 8. Prompt Caching Optimization

### 8.1 Cache-Friendly Structure

```python
# Structure prompts for maximum cache benefit
# Static prefix (cached) + Dynamic suffix (variable)

STATIC_SYSTEM_PROMPT = """
[Long system prompt with all rules, examples, etc.]
[Must be > 1024 tokens for cache benefit]
[This part gets cached]
"""  # ~2000 tokens

def create_messages(customer_input: str) -> list:
    return [
        # Static system prompt - cached
        {"role": "system", "content": STATIC_SYSTEM_PROMPT},
        
        # Dynamic user input - not cached
        {"role": "user", "content": f"Classify: {customer_input}"}
    ]
```

### 8.2 Cache Hit Requirements

| Requirement | Value |
|-------------|-------|
| Minimum prefix | 1,024 tokens |
| Exact match | Yes (character-level) |
| Same model | Yes |
| Same deployment | Yes |

---

## 9. Testing & Validation

### 9.1 Prompt Testing Checklist

- [ ] Test with clear cases (expect >0.9 confidence)
- [ ] Test with ambiguous cases (expect follow-up request)
- [ ] Test with edge cases (empty input, very long input)
- [ ] Test reproducibility (same input → same output)
- [ ] Test all categories (coverage)
- [ ] Test format compliance (valid JSON)
- [ ] Measure latency impact

### 9.2 A/B Testing Framework

```python
class PromptABTest:
    def __init__(self, prompt_a: str, prompt_b: str):
        self.prompts = {"A": prompt_a, "B": prompt_b}
        self.results = {"A": [], "B": []}
        
    def run_test(self, test_cases: list, client) -> dict:
        for case in test_cases:
            for variant in ["A", "B"]:
                result = self._evaluate(
                    self.prompts[variant], 
                    case, 
                    client
                )
                self.results[variant].append(result)
                
        return self._analyze_results()
        
    def _analyze_results(self) -> dict:
        return {
            "A": {
                "accuracy": self._calc_accuracy(self.results["A"]),
                "avg_latency": self._calc_avg_latency(self.results["A"]),
                "consistency": self._calc_consistency(self.results["A"])
            },
            "B": {
                # Same metrics for B
            },
            "winner": self._determine_winner()
        }
```

---

## 10. Migration Prompt Conversion

### 10.1 Converting GPT-4 → GPT-5

**Before (GPT-4):**
```markdown
Think step by step:
1. First, read the customer message
2. Then, identify keywords indicating intent
3. Next, match keywords to categories
4. Then, determine confidence based on keyword strength
5. Finally, format your response as JSON
```

**After (GPT-5):**
```yaml
task: Classify customer message
output: JSON with category and confidence
# Remove: explicit reasoning steps (native reasoning handles this)
```

### 10.2 Common Conversions

| GPT-4 Pattern | GPT-5 Replacement |
|---------------|-------------------|
| "Think step by step" | Remove (use reasoning_effort) |
| Long example lists | 2-3 representative examples |
| Detailed format rules | JSON schema definition |
| Multiple reminders | Single clear instruction |
| Explicit edge cases | Trust model generalization |

---

## 11. Web-Based Prompt Editor

The evaluation framework includes a full **Prompts** page (`/prompts`) with a Copilot Studio–style Fluent 2 interface for managing prompts without leaving the browser:

| Sub-Tab | Capability |
|---------|------------|
| **View / Edit** | Read and edit the active prompt template for any model/type; changes take effect on the next API call |
| **✨ AI Generate** | Enter a topic and generate all 8 prompts (4 task types × 2 models) + 5 test datasets (70 scenarios) in one click (async with progress) |
| **Version History** | Filter, preview, restore, or delete (single/bulk) any past prompt version |
| **Test Data** | Browse and edit raw test scenarios (classification/dialog/general/RAG/tool calling) with inline JSON editor |

The editor uses **Fluent 2 styled inputs** (`.fluent-input`), **brand-blue action buttons** (`.fluent-btn-primary`), and **Fluent cards** (`.fluent-card`) for a consistent Copilot Studio look and feel.  All prompt saves automatically create a versioned snapshot in `prompts/history/`.

---

*Prompt Design Guide v2.0 | Last Updated: February 2026*
