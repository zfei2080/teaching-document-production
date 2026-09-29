# Engineering Standard

## Directory and Code Boundaries

- Root legacy scripts are not the target architecture and may only be changed by an approved migration or baseline work order.
- `docs/spec/` owns durable product scope; `docs/architecture/` owns cross-system contracts; `docs/governance/` owns engineering and maturity rules; `docs/adr/` owns durable decisions.
- `docs/work_orders/` authorizes bounded implementation; `docs/completion_reports/` records completed work; task history is audit material, not current authority.
- New production code, schema, migration, test, and operational locations require an approved work order and must follow the directory structure it defines. Do not create parallel production paths ad hoc.

## Maturity Levels

- `legacy`: retained for audit or migration reference; not a formal path.
- `experimental`: isolated proof with no production claim.
- `candidate_only`: evidence exists but cannot enter approval, selection, delivery, or usage history.
- `integration_candidate`: contract-scoped implementation awaiting required reconciliation and test evidence.
- `controlled`: promoted by an approved work order with required gate, test, and release evidence.

## Database and Evidence Policy

- Direct SQLite reads/writes are prohibited outside an approved work order and an explicit schema authority. Scripts may not create alternate authoritative stores.
- Formal inventory writes require canonical QSR, Parse-Import Evidence, and gate evidence defined by the Supply and Delivery Contract.
- Temporary or isolated databases must be labelled and kept separate from formal inventory. Historical databases are not formal inventory by default.

## Test Policy (L0-L4)

| Level | Purpose |
| --- | --- |
| L0 | Formatting, UTF-8/readability, static syntax, and scoped document checks. |
| L1 | Unit tests for deterministic components and contract validation. |
| L2 | Isolated integration tests, including schema and bridge-manifest reconciliation. |
| L3 | Controlled end-to-end tests using approved non-production inputs and explicit gate evidence. |
| L4 | Release evidence for formal delivery, including rendering/review and delivery/usage effects. |

Tests may not claim formal delivery readiness unless the relevant L4 evidence exists. `python run_offline_regression.py` remains the existing offline regression entry point where applicable; its historical coverage claims must be re-measured before promotion decisions.

## Dependencies, Tooling, and CI

- Prefer existing, maintained dependencies and official tooling after evaluating fit; do not add dependencies or complex architecture without a work-order need.
- Pin or record introduced tooling versions where reproducibility matters. Network, browser, API, and source-processing actions require explicit work-order authorization.
- CI progresses from L0/L1 first, then contract-scoped L2, then approved controlled L3/L4 checks. A green lower-level check is not a promotion decision.

## Git, Worktree, and Release Rules

- Inspect scoped status and diff before changes, staging, and release. Preserve unrelated changes.
- One implementation session executes one approved work order, writes its report, commits only its allowed files, and pushes its branch.
- Do not merge, reset, clean, delete, or repurpose worktrees without a dedicated approved work order. Promotion or merge requires the register's named reconciliation work order.
- Commit messages describe actual changes and verification. Push failures, divergence, or policy blocks must be recorded factually.

## Sensitive Data

Do not commit or report source-question text, source-artifact paths, credentials, cookies, tokens, raw browser DOM, API keys, or configuration secrets. Use bounded scans on changed files only; never scan or expose source artifacts merely to validate documentation.

## Required Work-Order and Report Evidence

Work orders must state objective, authorized and prohibited operations, allowed files, acceptance criteria, verification commands, stop conditions, data/API/source boundaries, and publication expectations. Completion reports must distinguish observed facts, historical claims not re-run, unmeasured items, and user-reserved decisions; include changed files, commands actually run, results, metrics when applicable, commit, push, and final status.

## Completion-Report Publication Protocol

- A completion report records the implementation commit(s), their push result, the final `git status -sb` observed immediately before the report-publication commit, and any known remote head at that point.
- The commit that first publishes the completion report is the **completion-report publication commit**. It is not required to appear inside that same report.
- `PROJECT_CONTROL.md`, not the self-published report, records the final publication head after the report-publication push, the completed work-order status, and the next authorization state.
- A later correction is warranted only for a false historical fact, not merely because a report cannot contain the hash of its own publication commit.
- This protocol applies prospectively and resolves the recursive-record ambiguity found in the two completed governance tasks, `GOVERNANCE-RESET-001` and `GOVERNANCE-REPORT-CORRECTION-001`.

## Fact and Uncertainty Boundary

This standard is a governance decision made in GOVERNANCE-RESET-001. It does not prove that any existing code, database, dependency, test, or CI system meets these rules. Such compliance must be measured and implemented by future approved work orders.
