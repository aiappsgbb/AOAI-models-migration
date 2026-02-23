# Azure OpenAI Model Migration Guide
## GPT-4o to GPT-5.x Migration Best Practices

This comprehensive guide covers all aspects of migrating live systems from GPT-4o to GPT-5.x on Azure AI Foundry.

---

## Table of Contents

1. [General Model Differences](#1-general-model-differences)
2. [Classification Agent Best Practices](#2-classification-agent-best-practices)
3. [Dialog & Follow-up Questions](#3-dialog--follow-up-questions)
4. [Prompt Design Best Practices](#4-prompt-design-best-practices)
5. [Latency Optimization](#5-latency-optimization)
6. [Security & Azure Tools](#6-security--azure-tools)
7. [Conversational & Voice AI](#7-conversational--voice-ai)
8. [Strategic Perspective](#8-strategic-perspective)
9. [UI/UX Evaluation Experience](#9-uiux-evaluation-experience)

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

## 6. Security & Azure Tools

### 6.1 Azure Sandbox Tools

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

### 6.2 Content Filtering

```python
# Azure content filter categories
content_filter_config = {
    "hate": {"enabled": True, "severity": "high"},
    "sexual": {"enabled": True, "severity": "high"},
    "violence": {"enabled": True, "severity": "medium"},
    "self_harm": {"enabled": True, "severity": "high"}
}
```

### 6.3 Data Protection

| Data Type | Protection Method |
|-----------|-------------------|
| PII in prompts | Redact before sending |
| Customer IDs | Hash or tokenize |
| Conversation logs | Encrypt at rest |
| Model outputs | Audit logging |

---

## 7. Conversational & Voice AI

### 7.1 GPT Realtime API

**Voice Agent Architecture:**
```
Customer Call → Speech-to-Text → GPT-5 → Text-to-Speech → Customer
                     ↓                    ↑
              Context Manager ←→ Tool Execution
```

### 7.2 Custom Voice Considerations

**Securing Exclusive Voice:**
- Work with Azure/OpenAI account team
- Enterprise agreement required
- Custom voice training possible
- Latency considerations for real-time

### 7.3 Customer Identification Methods

| Method | Security | User Experience | Recommendation |
|--------|----------|-----------------|----------------|
| PIN/Password | Medium | Poor | Legacy only |
| Passkey/FIDO2 | High | Good | Recommended |
| Voice Biometrics | Medium-High | Excellent | With fallback |
| SMS OTP | Medium | Medium | Backup method |
| Knowledge-based | Low | Poor | Avoid |

### 7.4 MCP Server Security

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

## 8. Strategic Perspective

### 8.1 Manual vs. Automated Prompting (Next 9 Months)

| Aspect | Current (2025) | Near Future (9 months) |
|--------|----------------|------------------------|
| Prompt Engineering | Manual refinement | Assisted optimization |
| A/B Testing | Manual setup | Automated suggestions |
| Quality Control | Human review | AI-assisted QC |
| Iteration Speed | Days | Hours |

**Recommendation:** Focus on building robust evaluation frameworks that can assess both manually-crafted and auto-generated prompts.

### 8.2 Key Technologies for Telephone Service Agents (12-24 Months)

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

### 8.3 Evaluation Framework Importance

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

## 9. UI/UX Evaluation Experience

### 9.1 Copilot Studio Fluent 2 Design

The evaluation framework's web interface follows the **Microsoft Copilot Studio** visual language — a **Fluent 2** design system that provides:

| Element | Implementation |
|---------|----------------|
| **Colour palette** | Brand-blue family (`#0F6CBD`) replacing previous purple accents |
| **Layout** | Top header bar (48 px) + collapsible left sidebar (48→220 px on hover) |
| **Typography** | Segoe UI with Fluent 2 type ramp |
| **Components** | Flat buttons, rounded cards (`.fluent-card`), styled inputs, badge variants |
| **Icons** | Fluent UI System Icons via CDN |
| **Design tokens** | CSS custom properties (`--brand-primary`, `--surface-bg`, `--border-default`, etc.) |

### 9.2 Template Architecture

All pages share two partials that centralise the design system:

| Partial | Purpose |
|---------|---------|
| `_fluent_head.html` | Tailwind config extension, CSS custom properties, Fluent 2 component classes, icon CDN |
| `_sidebar.html` | Top header bar with logo/title/topic badge, left navigation rail with hover expansion |

Design changes are made in the partials and automatically propagate to all five pages.

### 9.3 Evaluation Dashboard

The dashboard and evaluation pages display:
- **12 metric cards** per evaluation type inside Fluent cards with info tooltips
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

*Last Updated: June 2025*
