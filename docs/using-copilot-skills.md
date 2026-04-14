# Using the Copilot Skills in This Repo

This repository includes **five GitHub Copilot Skills** that provide contextual, up-to-date guidance when you're working on Azure OpenAI model migrations. Skills are activated automatically when your question matches their domain — you don't need to memorize commands.

## What Are Copilot Skills?

[GitHub Copilot Skills](https://docs.github.com/en/copilot/customizing-copilot/adding-custom-skills-for-copilot) are markdown-based knowledge files that Copilot reads when activated. They give Copilot domain-specific expertise about your project — in this case, Azure OpenAI model migration patterns, evaluation techniques, and agent development.

Skills work in:
- **GitHub Copilot Chat** (VS Code, Visual Studio, JetBrains)
- **GitHub Copilot CLI** (terminal)
- **GitHub.com** (Copilot in pull requests, issues)

## Available Skills

| Skill | Domain | Example Questions |
|-------|--------|------------------|
| **[aoai-model-migration]** | API changes, client config, parameter adaptation | "How do I migrate from GPT-4o to GPT-5.4-mini?", "What parameters change for reasoning models?" |
| **[aoai-model-lifecycle]** | Retirement dates, governance, planning | "When does GPT-4.1 retire?", "How do I set up retirement notifications?" |
| **[aoai-migration-evaluation]** | A/B testing, LLM-as-Judge, quality metrics | "How do I evaluate model quality?", "Set up cloud-based evaluation in Foundry" |
| **[agent-framework-azure-ai-py]** | Agent Framework SDK, hosted tools, MCP | "Create an agent with code interpreter", "How do I use MCP servers?" |
| **[agents-v2-py]** | Container-based Foundry Agents | "Create a hosted agent with custom container", "How do I use ImageBasedHostedAgentDefinition?" |

[aoai-model-migration]: ../.github/skills/aoai-model-migration/SKILL.md
[aoai-model-lifecycle]: ../.github/skills/aoai-model-lifecycle/SKILL.md
[aoai-migration-evaluation]: ../.github/skills/aoai-migration-evaluation/SKILL.md
[agent-framework-azure-ai-py]: ../.github/skills/agent-framework-azure-ai-py/SKILL.md
[agents-v2-py]: ../.github/skills/agents-v2-py/SKILL.md

## How to Use

### In VS Code (GitHub Copilot Chat)

1. Open this repo in VS Code
2. Open **Copilot Chat** (`Ctrl+Shift+I` or click the Copilot icon)
3. Ask a question — Copilot automatically activates the relevant skill based on your query

**Example prompts:**
```
How do I switch from AzureOpenAI client to the v1 API?
What's the reasoning_effort default for GPT-5.4-mini?
When does GPT-4.1 retire and what should I migrate to?
How do I run an A/B comparison between GPT-4o and GPT-5.4-mini?
Create an agent with file search and code interpreter tools
```

> **💡 Tip:** You don't need to mention the skill by name. Copilot matches your question to the right skill automatically based on keywords.

### In GitHub Copilot CLI

1. Open a terminal in this repo directory
2. Use the `ghcp` command or your configured Copilot CLI
3. Ask migration-related questions — skills activate automatically

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
│
├── Build an AI agent (SDK-based)
│   └── agent-framework-azure-ai-py
│       "agent", "hosted tools", "MCP", "streaming"
│
└── Build a container-based hosted agent
    └── agents-v2-py
        "container agent", "ImageBasedHostedAgentDefinition"
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
