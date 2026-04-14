# Using the AI Agent Skills in This Repo

This repository includes **three AI agent skills** that provide contextual, up-to-date guidance when you're working on Azure OpenAI model migrations. Skills are activated automatically when your question matches their domain — you don't need to memorize commands.

## Quick Install (Any Coding Agent)

Install our skills into your own project or globally using [`npx skills`](https://github.com/vercel-labs/skills) — the universal skill installer that supports **40+ coding agents** including GitHub Copilot, Claude Code, Cursor, Codex, and more.

```bash
# Install all 3 skills into your current project
npx skills add aiappsgbb/AOAI-models-migration

# Install globally (available across all your projects)
npx skills add aiappsgbb/AOAI-models-migration -g

# Install a specific skill only
npx skills add aiappsgbb/AOAI-models-migration --skill aoai-model-migration

# Target specific agents
npx skills add aiappsgbb/AOAI-models-migration -a github-copilot -a claude-code

# List available skills before installing
npx skills add aiappsgbb/AOAI-models-migration --list
```

> **💡 Tip:** No need to clone this repo — `npx skills add` fetches and installs skills directly from GitHub. After installation, your coding agent automatically discovers and activates them based on your questions.

### Manage Installed Skills

```bash
npx skills list          # See what's installed
npx skills update        # Pull latest versions
npx skills remove        # Uninstall skills
```

## What Are Agent Skills?

[Agent skills](https://github.com/vercel-labs/skills) are markdown-based knowledge files (`SKILL.md`) that coding agents read when activated. They give your agent domain-specific expertise — in this case, Azure OpenAI model migration patterns and evaluation techniques.

Skills work natively in:
- **GitHub Copilot** (VS Code, CLI, GitHub.com)
- **Claude Code**, **Cursor**, **Codex**, **Windsurf**, and [40+ other agents](https://github.com/vercel-labs/skills#supported-agents)

## Available Skills

| Skill | Domain | Example Questions |
|-------|--------|------------------|
| **[aoai-model-migration]** | API changes, client config, parameter adaptation | "How do I migrate from GPT-4o to GPT-5.4-mini?", "What parameters change for reasoning models?" |
| **[aoai-model-lifecycle]** | Retirement dates, governance, planning | "When does GPT-4.1 retire?", "How do I set up retirement notifications?" |
| **[aoai-migration-evaluation]** | A/B testing, LLM-as-Judge, quality metrics | "How do I evaluate model quality?", "Set up cloud-based evaluation in Foundry" |

[aoai-model-migration]: ../.github/skills/aoai-model-migration/SKILL.md
[aoai-model-lifecycle]: ../.github/skills/aoai-model-lifecycle/SKILL.md
[aoai-migration-evaluation]: ../.github/skills/aoai-migration-evaluation/SKILL.md

## How to Use

### In VS Code (GitHub Copilot Chat)

1. Open this repo in VS Code (or install skills into your project with `npx skills add`)
2. Open **Copilot Chat** (`Ctrl+Shift+I` or click the Copilot icon)
3. Ask a question — Copilot automatically activates the relevant skill based on your query

**Example prompts:**
```
How do I migrate from GPT-4o to GPT-5.4-mini?
What's the reasoning_effort default for GPT-5.4-mini?
When does GPT-4.1 retire and what should I migrate to?
How do I run an A/B comparison between GPT-4o and GPT-5.4-mini?
```

> **💡 Tip:** You don't need to mention the skill by name. Your agent matches your question to the right skill automatically based on keywords.

### In GitHub Copilot CLI

1. Open a terminal in a directory where skills are installed
2. Launch `copilot`
3. Ask migration-related questions — skills activate automatically

```
copilot
> When does GPT-4.1 retire and what should I replace it with?
```

### In Other Coding Agents (Claude Code, Cursor, Codex, etc.)

After installing with `npx skills add`, skills are automatically available in whichever agents you selected during installation. Just ask a migration-related question and the agent will activate the relevant skill.

### On GitHub.com

Skills are also available when using Copilot in:
- **Pull request reviews** — ask Copilot about migration patterns while reviewing code
- **Issues** — get guidance on planning model migrations

## Which Skill Should I Use?

```
I need to...
│
├── Change my code to use a new model
│   └── aoai-model-migration
│       "migrate", "switch model", "v1 API", "parameter changes"
│
├── Know when my model retires / plan ahead
│   └── aoai-model-lifecycle
│       "retirement date", "deprecation", "planning", "notifications"
│
├── Test if the new model is good enough
│   └── aoai-migration-evaluation
│       "evaluate", "compare", "A/B test", "quality metrics"
```

## Skills vs. Documentation

Skills and docs serve different purposes:

| | Skills | Documentation (`docs/`) |
|---|---|---|
| **Updated** | Periodically (may lag) | More frequently |
| **Format** | Optimized for Copilot consumption | Optimized for human reading |
| **Use when** | Asking Copilot for help | Reading guides yourself |
| **Depth** | Quick guidance + code patterns | Full walkthroughs + context |

> **📝 Note:** Skills reference the repo documentation for the latest data. If a skill mentions a retirement date or model, always cross-check with [`docs/retirement-timeline.md`](retirement-timeline.md) for the most current information.

## Keeping Skills Current

Skills in this repo are updated alongside the documentation. If you notice a skill has stale information:

1. Check [`docs/retirement-timeline.md`](retirement-timeline.md) for the latest dates
2. Check [`docs/migration-paths.md`](migration-paths.md) for the latest model recommendations
3. File an issue or PR to update the skill

---

## Next Steps

- **[Getting Started](getting-started.md)** — set up the repo and run your first migration
- **[Migration Paths](migration-paths.md)** — choose your target model
- **[Evaluation Guide](evaluation-guide.md)** — validate quality before deploying
- **[Retirement Timeline](retirement-timeline.md)** — know when your models retire
