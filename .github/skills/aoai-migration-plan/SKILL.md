---
name: aoai-migration-plan
description: >
  Generate a phased migration execution plan for Azure OpenAI model transitions.
  Covers assessment, testing, pilot, gradual rollout, validation, and cleanup phases
  with checklists, gate criteria, risk registers, and rollback strategies.
  USE FOR: migration plan, project plan, rollout plan, migration timeline, migration checklist,
  phased migration, canary deployment, rollback strategy, migration risk, cutover plan,
  migration steps, migration template.
  DO NOT USE FOR: choosing a target model (use aoai-model-lifecycle),
  code-level API changes (use aoai-model-migration),
  evaluation setup (use aoai-migration-evaluation).
---

# Azure OpenAI Migration Execution Plan Skill

> **⚠️ Retirement dates and model availability change frequently.** Always verify against the **[official Azure OpenAI Model Retirements page](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements)** before finalizing any migration timeline.

## Purpose

Generate structured, phased migration execution plans for Azure OpenAI model transitions. Use this skill when the user already knows the source and target models and needs the operational rollout plan: scope, owners, phases, gates, risks, rollback criteria, and cleanup activities.

## When to Use

- Creating a project plan for an Azure OpenAI model migration
- Building a rollout checklist for production model cutover
- Planning a phased canary or blue-green migration
- Translating retirement deadlines into an execution timeline
- Preparing stakeholder-ready migration plans with go/no-go gates
- Defining rollback criteria before production rollout
- Coordinating app, platform, operations, and business owners during migration

## Decision Logic

Choose the migration plan shape based on time remaining before the source model retirement date.

| Time until retirement | Planning mode | How to adapt the plan |
|---|---|---|
| **Less than 30 days** | **Accelerated** | Compress phases, run assessment and testing in parallel where possible, shorten approval windows, and skip the pilot only if risk is understood and rollback is proven. |
| **30-90 days** | **Standard** | Use the full six phases with abbreviated timelines, one pilot window, and shorter document review cycles. |
| **More than 90 days** | **Full** | Use the full six phases with comfortable buffers, broader stakeholder review, multiple evaluation rounds, and time for prompt tuning or code remediation. |

> **📝 Note:** If the source model has already retired or auto-upgrade is imminent, switch immediately to an accelerated plan and treat rollback readiness as mandatory.

## What to Produce

When using this skill, generate a migration plan document with:

- A clear **source model**, **target model**, **deadline**, and **owner**
- A **six-phase plan** with tasks, dates, owners, dependencies, and gate criteria
- A **risk register** with mitigations and contingency actions
- A **cost impact table** comparing current and projected run rates
- A **rollback strategy** with explicit triggers and verification steps
- A final **decision summary** with next milestone and blockers

Use this structure:

```markdown
# Azure OpenAI Migration Plan

## Migration Summary
- Application / workload:
- Environment(s):
- Source model:
- Target model:
- Retirement date:
- Planning mode:
- Executive owner:
- Technical owner:
- Change window:

## Phase 1: Assessment
...

## Phase 2: Testing
...
```

## Six-Phase Plan Template

### Phase 1: Assessment

**Goal:** Validate feasibility, confirm capacity, identify all impacted deployments, and estimate cost impact.

**Checklist**

- [ ] Confirm the retirement date and replacement path
- [ ] Inventory all Azure OpenAI deployments using the source model
- [ ] Identify every application, environment, workflow, and region using the deployment
- [ ] Confirm quota, capacity, and regional availability for the candidate model
- [ ] Identify required code, prompt, configuration, or infrastructure changes
- [ ] Estimate expected cost impact and budget implications
- [ ] Identify owners, approvers, support contacts, and change windows
- [ ] Decide whether the migration can use pilot plus progressive rollout or needs an accelerated path

**Outputs**

- Deployment inventory
- Scope and dependency list
- Initial cost impact estimate
- Draft timeline and owner matrix

**Gate — Go/No-Go**

Proceed only when feasibility is confirmed, candidate capacity is available, and stakeholders agree on the migration path.

### Phase 2: Testing

**Goal:** Prove that the target model meets quality, compatibility, and performance expectations in non-production.

**Checklist**

- [ ] Create a test deployment for the candidate model
- [ ] Run the evaluation suite against representative scenarios
- [ ] Compare quality metrics for source versus target
- [ ] Test prompt compatibility and required prompt updates
- [ ] Validate parameter compatibility and API behavior
- [ ] Run load and latency tests under expected traffic patterns
- [ ] Confirm monitoring, alerting, and dashboards are ready for pilot
- [ ] Record known issues, accepted deviations, and follow-up actions

**Outputs**

- Test report and comparison summary
- Prompt or configuration adjustments
- Performance and reliability results
- Acceptance criteria baseline for pilot

**Gate — Quality and Performance**

Proceed only when quality, reliability, and latency meet acceptance criteria for the workload.

> **💡 Tip:** Use the sibling `aoai-migration-evaluation` skill to define the evaluation approach, datasets, and quality thresholds used in this phase.

### Phase 3: Pilot

**Goal:** Validate the candidate model on a small slice of real production traffic before full rollout.

**Checklist**

- [ ] Route **5-10%** of production traffic to the candidate deployment
- [ ] Monitor error rates, latency, safety signals, and user feedback
- [ ] Keep the source deployment fully available for immediate rollback
- [ ] Run the pilot for a minimum of **7 days** unless an accelerated plan is required
- [ ] Review support tickets, product feedback, and business KPIs
- [ ] Compare pilot behavior with baseline production metrics
- [ ] Hold a pilot review meeting with go/no-go stakeholders

**Outputs**

- Pilot scorecard
- Production-observed issue log
- Updated risk register
- Recommendation to expand, hold, or roll back

**Gate — Pilot Stability**

Proceed only when pilot metrics are stable, within agreed tolerance, and no unresolved high-severity issues remain.

> **⚠️ Important:** In an accelerated migration, skipping the pilot is a risk decision, not a default. Only skip it if rollback is proven, monitoring is strong, and the retirement deadline leaves no safe alternative.

### Phase 4: Migration

**Goal:** Gradually shift all traffic to the target deployment while preserving rollback capability.

**Checklist**

- [ ] Shift traffic to **25%** and monitor for **24-48 hours**
- [ ] Shift traffic to **50%** and monitor for **24-48 hours**
- [ ] Shift traffic to **75%** and monitor for **24-48 hours**
- [ ] Shift traffic to **100%** and continue heightened monitoring
- [ ] Keep the source deployment active until validation is complete
- [ ] Reconfirm incident response contacts at each increment
- [ ] Pause progression immediately if rollback criteria are met

**Outputs**

- Rollout checkpoint log
- Traffic split record
- Incident and mitigation log
- Final cutover timestamp

**Gate — Controlled Cutover**

Proceed to the next traffic increment only when the current increment is stable and within tolerance.

### Phase 5: Validation

**Goal:** Confirm the migration is complete, stable, and accepted by technical and business stakeholders.

**Checklist**

- [ ] Verify all production traffic is on the target deployment
- [ ] Run the full evaluation suite after cutover
- [ ] Confirm error rate, latency, and safety metrics are within expected range
- [ ] Verify projected versus actual cost impact is acceptable
- [ ] Confirm no hidden dependencies still reference the source deployment
- [ ] Collect stakeholder sign-off from engineering, operations, and business owners

**Outputs**

- Post-cutover validation report
- Final cost review
- Stakeholder sign-off record
- Decision to decommission source deployment

**Gate — Sign-Off**

Proceed only when all traffic is stable on the target deployment, post-cutover checks pass, and stakeholders approve closure.

### Phase 6: Cleanup

**Goal:** Remove obsolete assets and capture lessons learned.

**Checklist**

- [ ] Decommission the source deployment after the rollback window closes
- [ ] Archive migration evidence, reports, and approvals
- [ ] Update runbooks, dashboards, and operational documentation
- [ ] Update infrastructure-as-code, environment variables, and inventory records
- [ ] Close the migration ticket or change record
- [ ] Document lessons learned for the next model transition

**Outputs**

- Updated runbooks and inventory
- Archived migration package
- Closed change record
- Retrospective notes

**Gate — Closure**

Close the migration only after source assets are retired, documentation is updated, and ownership is clear for steady-state operations.

## Timeline Guidance

Use the six phases in all modes, but compress them differently.

| Phase | Accelerated (<30 days) | Standard (30-90 days) | Full (>90 days) |
|---|---|---|---|
| Assessment | 1-3 days | 3-7 days | 1-2 weeks |
| Testing | 3-7 days | 1-2 weeks | 2-4 weeks |
| Pilot | 0-3 days or skipped if necessary | 1 week | 1-2 weeks |
| Migration | 2-5 days | 1-2 weeks | 2-3 weeks |
| Validation | 1-3 days | 3-5 days | 1 week |
| Cleanup | 1-2 days | 2-5 days | 1 week |

> **📝 Note:** These are planning ranges, not guarantees. Adapt them to business criticality, approval cycles, and regional deployment complexity.

## Risk Register Template

Populate and maintain this table throughout the migration.

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Quality regression in production responses | Medium | High | Run representative evaluations early, pilot on real traffic, and define rollback triggers before cutover. |
| Capacity shortage or quota unavailability | Medium | High | Validate quota and regional availability during assessment; request increases before testing completes. |
| Prompt incompatibility with the target model | Medium | Medium | Test prompts and parameters in non-production; isolate and version prompt changes. |
| Latency increase during peak traffic | Medium | High | Run load tests, monitor pilot latency, and pause rollout at the first unstable checkpoint. |
| Cost overrun after migration | Medium | Medium | Estimate projected usage up front, compare pilot cost signals, and verify actual run rate after cutover. |

## Cost Impact Template

Do not hard-code pricing in the plan. Use placeholders and attach the latest pricing or FinOps estimate separately.

| Cost Category | Current State | Projected State | Variance | Notes |
|---|---|---|---|---|
| Model deployment / token usage | TBD | TBD | TBD | Compare current source-model spend versus expected target-model spend. |
| Supporting infrastructure | TBD | TBD | TBD | Include any changes to caching, gateways, monitoring, or storage. |
| Evaluation and test environments | TBD | TBD | TBD | Include temporary test or pilot environments if relevant. |
| Operational support | TBD | TBD | TBD | Include added on-call coverage or change-management effort if tracked. |
| **Total** | TBD | TBD | TBD | Validate after pilot and after full rollout. |

## Rollback Strategy

Define rollback before any production traffic moves.

### Rollback Triggers

Roll back immediately if any of the following occur:

- Error rate exceeds the agreed tolerance for the workload
- Latency materially exceeds the approved range during rollout or pilot
- Quality regression is observed in user-facing outputs or evaluation results
- Safety, compliance, or policy violations increase after cutover
- A Sev 1 or Sev 2 production incident is attributed to the migration
- Business stakeholders reject pilot or rollout outcomes

### Rollback Procedure

1. Stop any further traffic progression.
2. Route traffic back to the source deployment.
3. Verify the source deployment is healthy and serving expected volume.
4. Freeze candidate rollout changes until root cause is understood.
5. Capture logs, metrics, prompts, and traffic split state for analysis.
6. Open or update the incident/change record.
7. Decide whether to retry, remediate, or select a different target model.

### Rollback Verification Checklist

- [ ] Traffic has returned to the source deployment
- [ ] Error rate and latency have returned to baseline
- [ ] Customer-facing impact has stabilized
- [ ] Incident owner and remediation plan are assigned
- [ ] Next decision point is scheduled

## Cross-References

Use sibling skills together rather than overloading this one.

- Use **`aoai-model-lifecycle`** for retirement dates, lifecycle status, and target model selection
- Use **`aoai-model-migration`** for code-level API changes, SDK updates, and parameter adaptation
- Use **`aoai-migration-evaluation`** for evaluation methodology, golden datasets, A/B comparisons, and acceptance criteria

## Plan Generation Guidance

When drafting the final plan for a user:

- Prefer a phased checklist over narrative paragraphs
- Include named owners for each phase when the user provides teams or roles
- Convert vague requests into concrete milestones, gates, and rollback triggers
- Call out assumptions explicitly if the user did not provide traffic, region, or deadline details
- Keep the source deployment active until validation is complete
- Use fictional examples such as **Contoso** if an example organization is needed
- Avoid specific benchmark numbers or pricing claims unless the user supplies them

## Must Not

- Choose a target model without validating retirement status and replacement options
- Treat testing as optional before production rollout
- Decommission the source deployment before rollback readiness is confirmed
- Collapse assessment, testing, and rollout into one undocumented change window unless an emergency retirement forces it
- Publish pricing or benchmark claims without fresh authoritative sources
- Use real customer names or identifiable production details in the plan

## Repository Resources

> **💡 Tip:** This skill provides rollout planning guidance. For the latest model dates, migration paths, and evaluation details, always check the repo documentation — it is updated more frequently than this skill.

- [`docs/retirement-timeline.md`](../../../docs/retirement-timeline.md) — retirement dates and urgency planning
- [`docs/migration-paths.md`](../../../docs/migration-paths.md) — target model decision guidance
- [`docs/evaluation-guide.md`](../../../docs/evaluation-guide.md) — evaluation methodology and metrics
- [`docs/llm-upgrade-lifecycle-best-practices.md`](../../../docs/llm-upgrade-lifecycle-best-practices.md) — rollout and governance best practices
- [`docs/getting-started.md`](../../../docs/getting-started.md) — setup and migration checklist
- [`README.md`](../../../README.md) — repo overview and related guides

## References

- [Azure OpenAI Model Retirements](https://learn.microsoft.com/azure/ai-foundry/openai/concepts/model-retirements) — authoritative retirement dates
- [Azure OpenAI Models Overview](https://learn.microsoft.com/azure/ai-services/openai/concepts/models) — model capabilities and availability
- [API Version Lifecycle](https://learn.microsoft.com/azure/ai-foundry/openai/api-version-lifecycle) — API lifecycle guidance
- [What's New in Azure OpenAI](https://learn.microsoft.com/azure/ai-foundry/openai/whats-new) — latest platform changes
- [Azure Service Health Alerts](https://learn.microsoft.com/azure/service-health/alerts-activity-log-service-notifications-portal) — notification setup
