# Azure OpenAI Model Migration Guide
## GPT-4.1 to GPT-5.x Migration Best Practices

This comprehensive guide covers all aspects of migrating live systems from GPT-4.1 to GPT-5.x on Azure AI Foundry.

---

## Table of Contents

1. [General Model Differences](#1-general-model-differences)
2. [Classification Agent Best Practices](#2-classification-agent-best-practices)
3. [Dialog & Follow-up Questions](#3-dialog--follow-up-questions)
4. [Prompt Design Best Practices](#4-prompt-design-best-practices)
5. [Latency Optimization](#5-latency-optimization)
6. [RAG (Retrieval-Augmented Generation)](#6-rag-retrieval-augmented-generation)
7. [Tool Calling & Function Calling](#7-tool-calling--function-calling)
8. [Security & Azure Tools](#8-security--azure-tools)
9. [Conversational & Voice AI](#9-conversational--voice-ai)
10. [Strategic Perspective](#10-strategic-perspective)
11. [UI/UX Evaluation Experience](#11-uiux-evaluation-experience)

---

## 1. General Model Differences

### 1.1 Parameter Changes

| Parameter | GPT-4o | GPT-5.x | Migration Action |
|-----------|--------|---------|------------------|
| `max_tokens` | 4,096 | 32,768 | Update limits if needed |
| `temperature` | 0-2 | 0-2 | Same - keep low for reproducibility |
| `seed` | Supported | Enhanced | Better consistency in GPT-5 |
| `reasoning_effort` | N/A | low/medium/high | NEW - controls reasoning depth |
| `prediction` | N/A | Supported | NEW - speculative decoding |
| `store` | N/A | Supported | NEW - conversation storage |

### 1.2 Reasoning Capabilities

**GPT-4o Approach:**
```python
# Explicit chain-of-thought prompting required
system_prompt = """
Think step by step:
1. First, identify the category
2. Then, determine the priority
3. Finally, assess sentiment
"""
```

**GPT-5.x Approach:**
```python
# Native reasoning - no explicit CoT needed
system_prompt = """
Classify the customer message.
"""
# Use reasoning_effort parameter instead
response = client.chat.completions.create(
    model="gpt-5",
    messages=messages,
    reasoning_effort="medium"  # Let model reason internally
)
```

### 1.3 Response Variance & Reproducibility

**Achieving Consistency:**

| Method | GPT-4o Effect | GPT-5.x Effect |
|--------|---------------|----------------|
| `temperature=0` | ~90% consistent | ~95% consistent |
| `temperature=0` + `seed` | ~95% consistent | ~98% consistent |
| Deterministic mode | N/A | ~99% consistent |

**Best Practice:**
```python
# For production classification systems
response = client.chat.completions.create(
    model="gpt-5",
    messages=messages,
    temperature=0,
    seed=42,  # Use consistent seed
    response_format={"type": "json_object"}
)
```

---

## 2. Classification Agent Best Practices

### 2.1 TELCO Classification Architecture

```
Customer Message → Pre-processing → Classification → Routing
                                         ↓
                              Category + Subcategory
                              Priority + Sentiment
                              Confidence Score
                              Follow-up Questions
```

### 2.2 Key Optimizations for GPT-5

1. **Simplify System Prompts**: Remove explicit reasoning instructions
2. **Use Structured Output**: Always specify `response_format`
3. **Confidence Calibration**: GPT-5 provides better-calibrated confidence scores
4. **Entity Extraction**: More accurate with less prompting

### 2.3 Reproducibility for Production

```python
# Production-ready classification call
def classify_customer_message(message: str, client) -> dict:
    response = client.chat.completions.create(
        model="gpt-5",
        messages=[
            {"role": "system", "content": CLASSIFICATION_PROMPT},
            {"role": "user", "content": message}
        ],
        temperature=0,
        seed=42,
        response_format={"type": "json_object"},
        max_tokens=500
    )
    return json.loads(response.choices[0].message.content)
```

---

## 3. Dialog & Follow-up Questions

### 3.1 When to Ask Follow-up Questions

| Condition | Action |
|-----------|--------|
| Confidence < 0.7 | Always ask clarification |
| Missing critical info | Ask specific question |
| Ambiguous intent | Offer options |
| Customer frustrated | Minimize questions |

### 3.2 Follow-up Question Rules by Category

**Billing:**
- Always get: charge date, amount, description
- Never ask: account number (you have it)

**Technical:**
- Always get: service type, symptoms, scope
- Start with: "Is this affecting all devices?"

**Sales:**
- Always get: budget, usage needs, timeline
- Offer: concrete options with prices

### 3.3 GPT-5 Follow-up Generation

GPT-5 generates better contextual follow-ups with less prompting:

```yaml
# GPT-4 prompt needed explicit rules
follow_up_rules:
  billing: "Ask about charge date and description"
  technical: "Ask about service type first"
  
# GPT-5 can infer from examples
examples:
  - input: "My bill is wrong"
    follow_up: "What charge seems incorrect?"
```

---

## 4. Prompt Design Best Practices

### 4.1 Format Comparison

| Format | Best For | GPT-4 | GPT-5 |
|--------|----------|-------|-------|
| **Markdown** | Instructions, examples | ✅ Preferred | ✅ Good |
| **YAML** | Structured rules | ✅ Good | ✅ Excellent |
| **JSON** | Output schemas | ✅ Required | ✅ Native |
| **Mixed** | Complex agents | ⚠️ Can confuse | ✅ Better parsing |

### 4.2 Avoiding Prompt Drift

**Anti-Patterns:**
- ❌ Extremely long system prompts (>4000 tokens)
- ❌ Contradictory instructions
- ❌ Too many examples (>10)
- ❌ Mixing multiple tasks in one prompt

**Best Practices:**
- ✅ Structured sections with clear headers
- ✅ Priority order for conflicting rules
- ✅ Concise examples (3-5 maximum)
- ✅ Single responsibility per prompt

### 4.3 GPT-5 Prompt Structure

```markdown
# ROLE (brief)
Expert TELCO classification agent.

# OUTPUT FORMAT (explicit)
Return JSON with: category, priority, sentiment

# RULES (prioritized)
1. Single category per classification
2. Confidence below 0.7 triggers follow-up
3. Extract amounts and dates as entities

# EXAMPLES (minimal)
<example>Input: "Bill issue" → Category: billing</example>
```

---

## 5. Latency Optimization

### 5.1 Prompt Caching

**Requirements for Maximum Cache Benefit:**
- Minimum prefix length: 1,024 tokens
- Static system prompt at message start
- Consistent message structure

```python
# Structure for optimal caching
messages = [
    {"role": "system", "content": LONG_SYSTEM_PROMPT},  # Cached
    {"role": "user", "content": customer_message}       # Variable
]
```

**Cache Hit Benefits:**
- 50% latency reduction for cached tokens
- 50% cost reduction for cached tokens

### 5.2 Predicted Outputs (GPT-5)

For structured responses where format is predictable:

```python
response = client.chat.completions.create(
    model="gpt-5",
    messages=messages,
    prediction={
        "type": "content",
        "content": '{"category": "billing", "priority": "'  # Predicted prefix
    }
)
```

### 5.3 Streaming for Perceived Latency

```python
# Stream for user-facing applications
stream = client.chat.completions.create(
    model="gpt-5",
    messages=messages,
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        yield chunk.choices[0].delta.content
```

### 5.4 Latency Comparison

| Optimization | GPT-4o | GPT-5 | Notes |
|--------------|--------|-------|-------|
| Baseline | 500ms | 600ms | GPT-5 initially slower |
| + Caching | 300ms | 350ms | Both benefit similarly |
| + Streaming (TTFT) | 200ms | 250ms | Perceived improvement |
| + Predicted Output | N/A | 200ms | Significant GPT-5 benefit |

---

## 6. RAG (Retrieval-Augmented Generation)

### 6.1 RAG Architecture for Migration

When migrating RAG pipelines from GPT-4.1 to GPT-5.x, the core challenge is ensuring the model stays **grounded** in the provided context while leveraging improved reasoning capabilities.

```
User Query → Retriever → Context Documents → GPT Model → Grounded Response
                                                ↓
                                     Groundedness Check
                                     Relevance Check
```

### 6.2 GPT-4 vs GPT-5 RAG Differences

| Aspect | GPT-4.1 | GPT-5.x | Migration Impact |
|--------|---------|---------|------------------|
| Context window | 128K tokens | 1M+ tokens | More documents per query |
| Groundedness | Good with explicit instructions | Better native grounding | Simpler prompts |
| Hallucination risk | Moderate | Lower | Still requires validation |
| Long-context recall | Degrades >64K | Better sustained attention | Larger context batches |
| Reasoning over context | Explicit CoT needed | Native reasoning | Remove CoT instructions |

### 6.3 RAG Prompt Design for GPT-5

```markdown
# RAG AGENT

## Task
Answer the user's question using ONLY the provided context documents.

## Rules
1. Base your answer exclusively on the provided context
2. If the context doesn't contain the answer, say so explicitly
3. Cite the relevant document(s) in your response
4. Never fabricate information not in the context

## Context Documents
{context}

## User Question
{query}
```

### 6.4 RAG Evaluation Metrics

The framework evaluates RAG scenarios with these metrics:

| Metric | Description | Threshold |
|--------|-------------|-----------|
| **Groundedness** | Context keyword overlap in model response | ≥ 0.85 |
| **Relevance** | Ground truth keyword overlap in response | ≥ 0.80 |
| **Format compliance** | Correct output structure | — |
| **Completeness** | All required elements present | — |

### 6.5 RAG Test Data Format

```json
{
  "query": "What is the cancellation policy for premium plans?",
  "context": "Premium plan subscribers can cancel within 30 days for a full refund...",
  "ground_truth": "Premium plans can be cancelled within 30 days for a full refund.",
  "expected_format": "text",
  "difficulty": "medium",
  "metadata": {"topic": "billing", "source": "policy_docs"}
}
```

---

## 7. Tool Calling & Function Calling

### 7.1 Migration from GPT-4 to GPT-5 Tool Calling

GPT-5 brings significant improvements to tool calling: better parameter extraction, more reliable tool selection, and native support for complex multi-tool workflows.

### 7.2 GPT-4 vs GPT-5 Tool Calling Differences

| Aspect | GPT-4.1 | GPT-5.x | Migration Impact |
|--------|---------|---------|------------------|
| Tool selection | Good | Excellent | More reliable |
| Parameter extraction | Requires explicit schemas | Better inference from descriptions | Simpler schemas possible |
| Multi-tool chains | Manual orchestration needed | Native multi-step | Simplify orchestration code |
| Parallel tool calls | Supported | Enhanced | Better throughput |
| Error recovery | Limited | Better self-correction | Fewer retries needed |

### 7.3 Tool Definition Best Practices

```python
# GPT-5 optimised tool definitions
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_account_balance",
            "description": "Get current balance for a customer account",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "Customer ID (format: CUS-XXXXXX)"
                    },
                    "include_pending": {
                        "type": "boolean",
                        "description": "Include pending transactions",
                        "default": False
                    }
                },
                "required": ["customer_id"]
            }
        }
    }
]
```

### 7.4 Tool Calling Evaluation Metrics

| Metric | Description | Threshold |
|--------|-------------|-----------|
| **Tool selection accuracy** | Correct tool(s) selected | ≥ 0.90 |
| **Parameter extraction accuracy** | Correct parameter values | ≥ 0.85 |
| **Format compliance** | Valid tool call format | — |
| **Latency** | Response time | ≤ 4000ms |

### 7.5 Tool Calling Test Data Format

```json
{
  "user_message": "Check my account balance for CUS-123456",
  "available_tools": ["get_account_balance", "get_billing_history", "update_profile"],
  "expected_tool_calls": ["get_account_balance"],
  "expected_parameters": {"customer_id": "CUS-123456"},
  "expected_format": "json",
  "difficulty": "easy",
  "metadata": {"category": "account_inquiry"}
}
```

---

## 8. Security & Azure Tools

### 8.1 Azure Sandbox Tools

**Code Interpreter for Exact String Matching:**

```python
# Use Code Interpreter for precise operations
tools = [
    {
        "type": "code_interpreter"
    }
]

# Prompt instructs exact matching
system_prompt = """
Use code interpreter to:
1. Extract exact account numbers (pattern: XXX-XXXX-XXXX)
2. Validate against customer records
3. Return exact match status
"""
```

**Security Considerations:**
- Code Interpreter runs in sandboxed environment
- No network access from sandbox
- Files are ephemeral
- Enable only when needed

### 8.2 Content Filtering

```python
# Azure content filter categories
content_filter_config = {
    "hate": {"enabled": True, "severity": "high"},
    "sexual": {"enabled": True, "severity": "high"},
    "violence": {"enabled": True, "severity": "medium"},
    "self_harm": {"enabled": True, "severity": "high"}
}
```

### 8.3 Data Protection

| Data Type | Protection Method |
|-----------|-------------------|
| PII in prompts | Redact before sending |
| Customer IDs | Hash or tokenize |
| Conversation logs | Encrypt at rest |
| Model outputs | Audit logging |

---

## 9. Conversational & Voice AI

### 9.1 GPT Realtime API

**Voice Agent Architecture:**
```
Customer Call → Speech-to-Text → GPT-5 → Text-to-Speech → Customer
                     ↓                    ↑
              Context Manager ←→ Tool Execution
```

### 9.2 Custom Voice Considerations

**Securing Exclusive Voice:**
- Work with Azure/OpenAI account team
- Enterprise agreement required
- Custom voice training possible
- Latency considerations for real-time

### 9.3 Customer Identification Methods

| Method | Security | User Experience | Recommendation |
|--------|----------|-----------------|----------------|
| PIN/Password | Medium | Poor | Legacy only |
| Passkey/FIDO2 | High | Good | Recommended |
| Voice Biometrics | Medium-High | Excellent | With fallback |
| SMS OTP | Medium | Medium | Backup method |
| Knowledge-based | Low | Poor | Avoid |

### 9.4 MCP Server Security

```python
# MCP Server access control
mcp_config = {
    "authentication": "oauth2",
    "authorization": "rbac",
    "data_encryption": "tls1.3",
    "audit_logging": True,
    "rate_limiting": {
        "requests_per_minute": 100,
        "tokens_per_minute": 50000
    }
}
```

---

## 10. Strategic Perspective

### 10.1 Manual vs. Automated Prompting (Next 9 Months)

| Aspect | Current (2025) | Near Future (9 months) |
|--------|----------------|------------------------|
| Prompt Engineering | Manual refinement | Assisted optimization |
| A/B Testing | Manual setup | Automated suggestions |
| Quality Control | Human review | AI-assisted QC |
| Iteration Speed | Days | Hours |

**Recommendation:** Focus on building robust evaluation frameworks that can assess both manually-crafted and auto-generated prompts.

### 10.2 Key Technologies for Telephone Service Agents (12-24 Months)

1. **Multimodal Real-time Processing**
   - Simultaneous voice + screen sharing
   - Visual context understanding
   - Real-time sentiment from voice

2. **Agentic Workflows**
   - Multi-step task completion
   - Tool orchestration
   - Autonomous problem resolution

3. **Personalization at Scale**
   - Customer history integration
   - Predictive needs assessment
   - Proactive service

4. **Edge Processing**
   - Reduced latency
   - Privacy preservation
   - Offline capability

### 10.3 Evaluation Framework Importance

```
┌─────────────────────────────────────────────────────────┐
│                  Future State                           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   Auto-Generated    ─→  Evaluation   ─→  Quality Gate  │
│      Prompts             Framework        (Human+AI)   │
│         ↑                    │                  │      │
│         └────────────────────┴──────────────────┘      │
│                    Feedback Loop                        │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Invest in:**
- Comprehensive test datasets
- Automated evaluation pipelines
- Metrics beyond accuracy (latency, cost, consistency)
- Human-in-the-loop validation

---

## 11. UI/UX Evaluation Experience

### 11.1 Copilot Studio Fluent 2 Design

The evaluation framework's web interface follows the **Microsoft Copilot Studio** visual language — a **Fluent 2** design system that provides:

| Element | Implementation |
|---------|----------------|
| **Colour palette** | Brand-blue family (`#0F6CBD`) replacing previous purple accents |
| **Layout** | Top header bar (48 px) + collapsible left sidebar (48→220 px on hover) |
| **Typography** | Segoe UI with Fluent 2 type ramp |
| **Components** | Flat buttons, rounded cards (`.fluent-card`), styled inputs, badge variants |
| **Icons** | Fluent UI System Icons via CDN |
| **Design tokens** | CSS custom properties (`--brand-primary`, `--surface-bg`, `--border-default`, etc.) |

### 11.2 Template Architecture

All pages share two partials that centralise the design system:

| Partial | Purpose |
|---------|---------|
| `_fluent_head.html` | Tailwind config extension, CSS custom properties, Fluent 2 component classes, icon CDN |
| `_sidebar.html` | Top header bar with logo/title/topic badge, left navigation rail with hover expansion |

Design changes are made in the partials and automatically propagate to all five pages.

### 11.3 Evaluation Dashboard

The dashboard and evaluation pages display:
- **Dynamic metric cards** per evaluation type (12 for classification, 12 for dialog, 4 for general, 8 for RAG, 8 for tool calling) inside Fluent cards with info tooltips
- **Chart.js** bar/radar charts for model comparison dimensions
- **Verbose narrative feed** with colour-coded entries (step/ok/warn/err/detail/head)
- **Fluent badges** for status indicators (success, warning, error, info, neutral)

---

## Migration Checklist

### Pre-Migration
- [ ] Inventory all production prompts
- [ ] Document current performance baselines
- [ ] Set up parallel evaluation environment
- [ ] Prepare synthetic test data

### Testing Phase
- [ ] Run classification accuracy tests
- [ ] Measure latency differences
- [ ] Test consistency/reproducibility
- [ ] Validate edge cases
- [ ] Run RAG groundedness and relevance tests
- [ ] Run tool calling selection and parameter accuracy tests
- [ ] Submit to Foundry LLM-as-judge for semantic quality evaluation

### Prompt Updates
- [ ] Remove explicit CoT instructions (if using reasoning models)
- [ ] Update deprecated parameters
- [ ] Implement prompt caching
- [ ] Test predicted outputs for structured responses

### Deployment
- [ ] Canary deployment (5% traffic)
- [ ] Monitor error rates
- [ ] Gradual rollout (25% → 50% → 100%)
- [ ] Prepare rollback procedures

### Post-Migration
- [ ] Compare production metrics
- [ ] Document learnings
- [ ] Update runbooks
- [ ] Plan optimization iterations

---

## Resources

- [Azure OpenAI Documentation](https://learn.microsoft.com/azure/ai-services/openai/)
- [Prompt Engineering Guide](https://platform.openai.com/docs/guides/prompt-engineering)
- [Azure AI Foundry](https://azure.microsoft.com/products/ai-foundry/)

---

*Last Updated: February 2026*
