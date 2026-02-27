# Data Generation Prompt Templates

This directory contains the **meta-prompts** sent to the LLM when generating
synthetic test data for each of the 5 evaluation types.

## Files

| File | Evaluation type | Placeholders |
|------|----------------|--------------|
| `system_message.txt` | All types | `{count}` |
| `classification.txt` | Classification | `{count}`, `{topic}`, `{category_block}` |
| `dialog.txt` | Dialog / Follow-up | `{count}`, `{topic}`, `{category_block}` |
| `general.txt` | General capabilities | `{count}`, `{topic}` |
| `rag.txt` | RAG (Retrieval-Augmented Generation) | `{count}`, `{topic}` |
| `tool_calling.txt` | Tool / Function calling | `{count}`, `{topic}` |

## Placeholders

- **`{count}`** — Number of scenarios to generate (e.g. `10`, `20`).
  Set via the UI or `config/settings.yaml` → `evaluation.test_data_counts`.
- **`{topic}`** — The domain topic (e.g. *"Soporte técnico de telecomunicaciones"*).
- **`{category_block}`** — Auto-generated block that either lists mandatory
  categories extracted from classification prompts, or asks the LLM to invent
  categories. This is built dynamically by `PromptManager` and injected into
  the template at runtime.

## How to customise

1. Edit any `.txt` file in this directory.
2. Keep all `{placeholders}` — the code replaces them at runtime.
3. Restart the app (or regenerate data) to pick up changes.
4. The system message is shared by all 5 types; per-type prompts are separate.

> **Tip:** If you add new placeholders, you must also update the corresponding
> `_build_*_data_prompt` method in `src/utils/prompt_manager.py`.
