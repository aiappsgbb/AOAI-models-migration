# Copilot Instructions

## Confidentiality — No Real Customer Names

**CRITICAL: NEVER use real customer names, company names, or identifiable references in any code, documentation, commit messages, comments, or generated content.**

This repository is **public**. All examples, scenarios, and references must use:

- **Contoso**, **Fabrikam**, **Northwind**, **Adventure Works**, or other [Microsoft fictional company names](https://learn.microsoft.com/en-us/style-guide/a-z-word-list-term-collections/f/fictitious-names)
- Generic roles like "a large enterprise customer", "an energy company", "a financial services organization"
- Fictional email addresses using `@contoso.com`, `@fabrikam.com`, etc.

This applies to: source code, documentation, git commit messages, PR descriptions, code comments, golden datasets, test fixtures, and presentation content.

When referencing real customer feedback or scenarios, **always generalize**: "Enterprise customers commonly report..." not "Customer X said...".

---

## Repository Structure

```
README.md                     # Concise index + quick-start (deep content → docs/)
docs/                         # Standalone deep-dive guides, cross-linked
data/                         # Pre-built golden datasets (JSONL)
src/                          # Reusable Python modules
  config.py                   #   Model family helpers (is_v1, is_reasoning, etc.)
  clients.py                  #   Client factory, call_model() with auto-adaptation
  evaluate/                   #   Evaluation framework (core, foundry, local, scenarios)
samples/
  rag_pipeline/               # Self-contained RAG migration demo
model_migration_eval/         # Web UI for visual comparison (OUT OF SCOPE for most changes)
.github/skills/               # GitHub Copilot Skills (SKILL.md + references/)
```

---

## Python Conventions

- **Type hints everywhere**: `def func(model: str, data: list[dict]) -> dict[str, float]:`
- **Union syntax**: `str | None` (not `Optional[str]`)
- **Dataclasses** for structured data: `@dataclass` with `field(default_factory=...)` for mutable defaults; add `@property` for computed values
- **Docstrings**: module-level summary at top of every file; Google-style for functions where needed
- **Imports**: stdlib → third-party → local, separated by blank lines; defer expensive imports inside functions if optional
- **Naming**: `_private_helper()` prefix for internal functions; `UPPER_SNAKE_CASE` for constants; descriptive variable names
- **Error handling**: catch specific exceptions, provide actionable error messages with context
- **Auth**: default to Entra ID (`DefaultAzureCredential`); API key only as fallback
- **Client selection**: `AzureOpenAI` for legacy models (GPT-4o), `OpenAI` with `/openai/v1/` base URL for v1 models (GPT-4.1+)

---

## Documentation Conventions

- **README** is a concise index; all deep-dive content lives under `docs/`
- **Cross-linking**: README → `docs/file.md` → `samples/` → back to `docs/`
- **Callout boxes** use blockquotes:
  - `> **⚠️ Important:**` — breaking changes, urgent info
  - `> **💡 Tip:**` — best practices
  - `> **📝 Note:**` — clarifications
- **Diagrams**: use Mermaid (renders on GitHub) — no ASCII art
- **Dates**: always include "Last verified: Month YYYY" at the top; link to [official Azure OpenAI retirements page](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements) as authoritative source
- **No bold cost claims or specific pricing** — these change frequently; link to official pricing pages instead

---

## Data & Evaluation Conventions

- **Golden datasets**: JSONL format (one JSON object per line), named `golden_{scenario}.jsonl`
- **Required field**: `prompt`; optional: `system_prompt`, `expected_output`, `context`, `tools`, `metadata`
- **Evaluation approach**: LLM-as-judge (semantic correctness) — **not** similarity scoring (BLEU, ROUGE, cosine)
- **Metrics**: coherence, fluency, relevance, groundedness as primary; similarity only as supplementary
- **Dual-layer evaluation**: end-to-end quality + task-level scoring for multi-step pipelines
- **JSON audit trails**: export timestamped results to `data/results/` for traceability

---

## Environment & Config

- **`.env.template`**: user-facing setup template (concise)
- **`.env_example`**: exhaustive reference with all possible vars
- **`.env`**: local config, gitignored — never committed
- **Env var naming**: `AZURE_OPENAI_ENDPOINT`, `{MODEL}_DEPLOYMENT`, `RAG_SOURCE_MODEL`, `EVAL_MODEL_DEPLOYMENT`
- **No secrets in code or docs** — only placeholders like `YOUR_RESOURCE_NAME`

---

## Git Conventions

- **Commit format**: `<type>: <concise description>` (under 72 chars)
- **Types**: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`
- **Co-author trailer**: always include `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`
- **No customer names or internal references in commit messages** — ever
