# Migrating Multi-Step Applications

Single-call migrations—swap the model, run an A/B eval, ship—work well when your application makes one LLM call per request. Most production systems don't. A RAG pipeline chains four or more model calls. An agent workflow may loop through a planner, tool-caller, and summarizer. When you migrate these systems, you need a methodology that tells you not just *whether* quality changed, but *where* the change originated.

This guide covers that methodology.

## Why Single-Call Evaluation Isn't Enough

Consider a standard RAG pipeline:

```
User query → Rephrase (LLM) → Embed (embedding model) → Retrieve (vector DB) → Generate (LLM)
```

You upgrade the embedding model from `text-embedding-ada-002` to `text-embedding-3-large`. The new model produces better embeddings—objectively, a win. But "better" embeddings surface *different* documents. The generation model now receives context it has never seen during your testing. The final answer changes. Your end-to-end quality score drops 8%.

Where did it break? Not in the embedding step—that improved. Not in the generation step—it produced a reasonable answer given its context. The regression is *emergent*: it only appears when the steps interact.

Single-call evaluation would miss this entirely. You'd test the embedding model in isolation (better scores), test the generation model in isolation (same scores), and ship—only to discover the regression in production.

**You need two layers of evaluation: one that catches regressions, and one that localizes them.**

## The Hybrid Evaluation Methodology

### End-to-End Evaluation (Monitoring Layer)

Run the full pipeline against a set of golden test cases and score the **final output only**.

**What to measure:**
- Groundedness (does the answer stick to the retrieved context?)
- Relevance (does the answer address the original query?)
- Correctness (does the answer match the expected reference answer?)
- Latency (did the total pipeline time change significantly?)

**How to run it:**
```python
from azure.ai.evaluation import GroundednessEvaluator, RelevanceEvaluator

# Run the full pipeline for each test case
for test_case in golden_dataset:
    result = pipeline.run(test_case["query"])

    # Score the final output
    groundedness = groundedness_evaluator(
        query=test_case["query"],
        context=result["retrieved_context"],
        response=result["answer"]
    )
    relevance = relevance_evaluator(
        query=test_case["query"],
        response=result["answer"]
    )
```

**When to run:** Every deployment, every nightly build, after any model or config change. This is your smoke test.

**Cost:** For 15 golden test cases comparing two model configurations: ~30 pipeline runs + ~30 LLM-as-judge calls. Under $1 in API costs.

**What it tells you:** "Overall quality changed." It does **not** tell you which step caused the change.

### Task-Level Evaluation (Diagnostic Layer)

Test each step in isolation by feeding it known-good inputs and scoring its output independently.

**Retrieval evaluation:**
```python
# Feed the retriever a known query with known relevant documents
retrieved_docs = retriever.retrieve(query="What is the refund policy?")

# Compare against expected documents
precision_at_k = len(expected_docs & retrieved_docs) / len(retrieved_docs)
recall_at_k = len(expected_docs & retrieved_docs) / len(expected_docs)
```
No LLM calls needed. Pure computation against your golden dataset's expected document IDs.

**Generation evaluation (isolated):**
```python
# Feed the generator the CORRECT context (bypass retrieval)
answer = generator.generate(
    query=test_case["query"],
    context=test_case["expected_context"]  # known-good context
)

# Score the answer
groundedness = groundedness_evaluator(
    query=test_case["query"],
    context=test_case["expected_context"],
    response=answer
)
```
By providing the expected context directly, you isolate the generation model from retrieval quality. If the score drops here, the generation model is the problem.

**Rephrase evaluation:**
```python
# Compare rephrased queries for intent preservation
original_query = "How do I get my money back?"
rephrased = rephraser.rephrase(original_query)

# Score: does the rephrased query preserve the original intent?
# Use semantic similarity or LLM-as-judge
intent_score = judge.evaluate(
    original=original_query,
    rephrased=rephrased,
    criteria="Does the rephrased query preserve the user's intent?"
)
```

**When to run:** Only when end-to-end evaluation detects a regression. Task-level evaluation is your debugging tool, not your monitoring tool.

**Cost:** Retrieval evaluation is free (no LLM calls). Generation and rephrase evaluation costs ~$0.50 per 15-case comparison.

### When to Use Which

| Scenario | End-to-End | Task-Level |
|----------|-----------|------------|
| Routine model update | ✅ Primary | Only if regression found |
| Critical or regulated use case | ✅ Required | ✅ Required |
| Debugging a regression | Initial detection | ✅ Root cause analysis |
| New model family (e.g., GPT-4o → GPT-5.1) | ✅ Required | ✅ Recommended |
| Embedding model change | ✅ Required | ✅ Required (retrieval focus) |
| Prompt template change only | ✅ Required | Targeted at changed step |

**Rule of thumb:** If your end-to-end scores hold, you don't need task-level evaluation. If they don't, task-level evaluation tells you where to look.

## Migration Strategy: One Model at a Time

Never swap all models simultaneously. You'll have no way to attribute regressions. Follow this sequence:

### Step 1: Inventory

List every model your pipeline uses and its role.

| Step | Current Model | Role | Migration Priority |
|------|--------------|------|--------------------|
| Rephrase | gpt-4o-mini | Query rewriting | Low |
| Embed | text-embedding-ada-002 | Vector embedding | Medium |
| Generate | gpt-4o | Answer generation | High |
| Judge | gpt-4o | Evaluation scoring | Do last |

### Step 2: Prioritize

Start with the model that has the **biggest impact on output quality**—usually the generator. Migrate lower-impact models (rephraser, classifier) afterward.

Exception: if a model is approaching its retirement date, prioritize it regardless of impact. Check [Retirement Timeline](retirement-timeline.md) for current dates.

### Step 3: Baseline

Run end-to-end evaluation with **all current models**. Save every score. This is your comparison point.

```bash
# Example: baseline run
python -m samples.rag_pipeline.evaluate_pipeline \
    --config baseline \
    --output results/baseline_scores.json
```

### Step 4: Swap One Model

Change exactly one model deployment. Run end-to-end evaluation again. Compare.

```bash
# Example: swap generator from gpt-4o to gpt-4.1
python -m samples.rag_pipeline.evaluate_pipeline \
    --config generator_gpt41 \
    --output results/generator_gpt41_scores.json
```

### Step 5: Diagnose if Needed

If end-to-end scores dropped, run task-level evaluation on the swapped step only.

- **If task-level scores also dropped:** The new model underperforms for this task. Adjust the prompt, tune parameters, or choose a different target model.
- **If task-level scores are fine:** The regression is an interaction effect. The swapped step now produces slightly different outputs that downstream steps handle poorly. Adjust the downstream prompt or consider migrating the downstream model at the same time.

### Step 6: Lock and Move On

Once the swapped model passes both end-to-end and task-level evaluation (if needed), lock in that change. Update your baseline. Move to the next model.

### Step 7: Final Regression Suite

After all models are migrated, run the full end-to-end evaluation one more time against the original baseline (all old models vs. all new models). This catches interaction effects that only appear when multiple models change together.

## Common Multi-Step Patterns

### RAG Pipeline

```
Query → Rephrase → Embed → Retrieve → Generate → Answer
```

**Migration order:**
1. **Generator** (highest impact on answer quality)
2. **Rephraser** (low risk—intent preservation is easy to validate)
3. **Embedding model** (migrate last—changes retrieval results, which affects everything downstream)

**Watch out for:** Embedding model changes require re-indexing your entire vector store. Plan for the indexing cost and time. A partial re-index is worse than no re-index.

**Task-level metrics:**
- Rephrase: intent preservation score (LLM-as-judge)
- Retrieval: precision@5, recall@5, MRR
- Generation: groundedness, relevance, correctness

### Agent Workflow

```
User request → Planner (decides steps) → Tool Caller (executes) → Summarizer (formats response)
```

**Migration order:**
1. **Planner** (determines the entire execution path—highest risk)
2. **Tool Caller** (must produce valid function calls—test with deterministic accuracy)
3. **Summarizer** (lowest risk—mostly formatting)

**Watch out for:** Planner regressions are subtle. A new model might choose different tool sequences that still produce correct results but with different latency or cost profiles. Include tool-call sequence comparison in your evaluation, not just final output quality.

**Task-level metrics:**
- Planner: plan correctness (does it choose the right tools in the right order?)
- Tool Caller: function call accuracy, parameter accuracy (see [Evaluation Guide](evaluation-guide.md))
- Summarizer: coherence, completeness

### Classification + Generation

```
User input → Classifier (route/categorize) → Generator (produce output)
```

**Migration order:**
1. **Generator** (produces the user-visible output)
2. **Classifier** (usually a smaller, cheaper model—migrate second)

**Watch out for:** Classifier changes can shift the distribution of inputs reaching the generator. If your classifier starts routing 20% more queries to the "complex" category, your generator sees harder inputs. End-to-end scores drop even though both models individually improved.

**Task-level metrics:**
- Classifier: accuracy, F1 score, confusion matrix vs. golden labels
- Generator: per-category quality scores (evaluate each category separately)

## Practical Example

The `samples/rag_pipeline/` directory contains a working implementation of this methodology:

```python
from samples.rag_pipeline.knowledge_base import KnowledgeBase

# 1. Build or load your knowledge base
kb = KnowledgeBase()

# 2. Run end-to-end evaluation (monitoring layer)
# See samples/rag_pipeline/ for the full pipeline implementation

# 3. If regression detected, run task-level evaluation (diagnostic layer)
# Isolate retrieval: compare retrieved docs against expected docs
# Isolate generation: feed known-good context, score the answer
```

See `samples/rag_pipeline/` for the full walkthrough, including golden test data and evaluation scripts.

For the evaluation methodology itself—building golden datasets, choosing metrics, setting regression thresholds—see:
- [Building Golden Datasets](building-golden-datasets.md)
- [Evaluation Guide](evaluation-guide.md)

## Cost Estimation

For a typical RAG pipeline with **15 golden test cases**, here's what a full A/B evaluation cycle costs:

| Evaluation Layer | What It Does | LLM Calls | Estimated Cost |
|-----------------|-------------|-----------|----------------|
| End-to-end eval | 15 test cases × 2 configs (old + new) | 30 pipeline runs | < $1.00 |
| Task-level: Retrieval | Precision/recall vs. expected docs | 0 (pure computation) | $0.00 |
| Task-level: Generation | 15 isolated generation calls × 2 models | 30 generation calls | < $0.50 |
| LLM-as-judge scoring | 30 judgments × ~500 tokens each (~15K tokens) | 30 judge calls | < $0.50 |
| **Total** | | | **< $2.00** |

At this cost, there is no reason to skip evaluation. Run it on every model change, every prompt update, every config tweak.

**Scaling note:** Costs scale linearly with golden dataset size. A 50-case dataset costs roughly 3× more (~$6 per cycle). A 100-case dataset costs ~$12. For most applications, 15–30 cases provide sufficient signal—see [Building Golden Datasets](building-golden-datasets.md) for guidance on dataset sizing.

## Checklist

Before declaring a multi-step migration complete:

- [ ] All models in the pipeline inventoried with roles and current versions
- [ ] Golden test cases cover representative queries for each pipeline path
- [ ] Baseline end-to-end scores recorded with all original models
- [ ] Each model migrated individually with end-to-end eval after each swap
- [ ] Task-level evaluation run for any step that showed regression
- [ ] Final end-to-end eval comparing all-old vs. all-new configuration
- [ ] Scores documented and stored for future reference
- [ ] Rollback plan in place (can revert to previous model deployments)

## See Also

- [Building Golden Datasets](building-golden-datasets.md) — How to create test data for your pipeline
- [Evaluation Guide](evaluation-guide.md) — Metrics, thresholds, and scoring methodology
- [Migration Paths](migration-paths.md) — Choosing target models for each step
- [API Changes by Model](api-changes-by-model.md) — Parameter differences across model families
- [Retirement Timeline](retirement-timeline.md) — Deadlines that may force your migration order
