# ADR-0001: One Project Control Plane

- **Status:** accepted
- **Date:** 2026-08-14
- **Decision owner:** user
- **Approval record:** explicit user approval of GOVERNANCE-RESET-001 on 2026-08-14.

## Context

The repository contains multiple documents that historically described current state, execution rules, and handoff information. Their coexistence can create conflicting implementation authority. The approved governance reset requires one current control document while preserving history for audit.

## Decision

Use the following authority chain:

```text
AGENTS.md -> PROJECT_CONTROL.md -> stable specification/contract/standard/ADRs -> approved work orders -> completion reports and task history
```

`PROJECT_CONTROL.md` is the only current control document and remains short. Work orders are the sole implementation authorization. Completion reports and history record evidence only.

## Consequences

- `task_plan.md` becomes a compatibility pointer, not a competing plan.
- `docs/交接文档.md` and `STATE.md` become compatibility or historical records unless a later ADR explicitly promotes or supersedes their content.
- `README.md` points to current control and durable product documents without asserting production readiness.
- Work orders must link to stable contracts and state bounded authorization; reports must distinguish facts, historical claims, unmeasured items, and user decisions.
- Existing historical documents are preserved rather than silently repaired or reinterpreted.

## Alternatives

- Keep several current control documents: rejected because conflicts are difficult to resolve during recovery.
- Replace all history immediately: rejected because it risks altering evidence and expands scope beyond governance reset.
- Use branch state as current authority: rejected because branch presence does not prove maturity, approval, or gate closure.

## Evidence

Observed in GOVERNANCE-RESET-001: existing `task_plan.md` contained historical and current-looking material, and the repository had multiple registered worktrees. Historical reports and implementation results were not re-run. User approval is recorded in the governing work order.

## Affected Contracts

- `AGENTS.md`
- `PROJECT_CONTROL.md`
- `docs/spec/PROJECT_SPECIFICATION.md`
- `docs/architecture/SUPPLY_DELIVERY_CONTRACT.md`
- `docs/governance/ENGINEERING_STANDARD.md`
- All future work orders and completion reports

## Rollback or Supersession Conditions

Only a later user-approved ADR may supersede this control plane. It must preserve audit links, name the replacement authority, describe migration for compatibility records, and state the implementation and verification work required.
