# ADR-0002: Isolated Schema and Migration Authority

- **Status:** accepted
- **Date:** 2026-08-14
- **Decision owner:** user
- **Approval record:** explicit user approval of `SCHEMA-AUTHORITY-001` on 2026-08-14.
- **Supersedes:** the implicit ambiguity among `schema.sql`, `schema_v2.sql`, numbered `schema_v2_*` files, and ad hoc application scripts for future isolated candidate-intake databases. It does not supersede ADR-0001.

## Context

The repository contains several schema families and dozens of migration artifacts with no recorded rule for which one a future implementation session must use to create an isolated candidate-intake database. Without a decision, a later session (for example `SUPPLY-BRIDGE-001`) could choose between `schema.sql` (legacy question-bank family used by `database.py`), the unified baseline `schema_v2.sql`, the 39 numbered migrations `schema_v2_1.sql`…`schema_v2_39.sql`, the temporary `schema_p1_2c_temporary.sql`, dev-bound apply wrappers, and ad hoc SQL by guesswork. A static audit (SCHEMA-AUTHORITY-001) inventoried the approved artifacts without opening any database.

## Decision

For **future isolated candidate-intake databases only**, the authoritative schema path is **conditionally selected**:

- **Baseline:** `schema_v2.sql`, which creates `schema_migrations(version TEXT PRIMARY KEY, applied_at ...)` and records `v2-initial-2026-07-25`.
- **Migrations:** `schema_v2_1.sql` … `schema_v2_39.sql` applied strictly in numeric order, honoring the wrapper-declared prerequisites of `apply_schema_v2_28.py`…`apply_schema_v2_39.py`.
- **Version truth:** the `schema_migrations` ledger; a database whose ledger does not match the expected 40 versions is not in the authoritative state.
- **Idempotence/transactions:** check-ledger-then-apply via the wrappers (16–39 early-return `MIGRATION_ALREADY_APPLIED`); migrations 29 and 31–39 declare their own transaction statements; v2.27/v2.29 toggle foreign keys around rebuilds; `ALTER TABLE` steps are protected only by wrapper guards.
- **Bootstrap entrypoint:** none exists as a complete, path-generic static artifact. A later bridge work order must prove the bootstrap prerequisite — a path-generic runner creating a fresh database from the baseline plus the ordered chain (or explicit user approval to promote a controlled baseline snapshot), with the v2.37 rebuild, v2.39 emptiness rule, and v2.24/v2.25/v2.17 replacement order proven on the fresh database — before any bootstrap path is used.

No existing database (development, production, backup, historical, or test snapshot) is declared authoritative. `schema.sql` + `database.py`, `schema_p1_2c_temporary.sql`, the dev-bound wrappers 5–15, and the binary snapshot test fixture are legacy/reference for this path and must not be altered or used as the bootstrap.

The v2 chain statically provides source/version provenance, import/extraction evidence, candidate-only status, answer/analysis/asset evidence, and delivery/usage state machines, but lacks canonical QSR identity, Bridge Manifest records, and cross-question duplicate-review signal storage; those are explicit future migration requirements for `SUPPLY-BRIDGE-001` or later, never silent schema mutation.

## Consequences

- A later session can no longer choose a schema family by guesswork; it must use the conditionally selected chain and first prove the bootstrap prerequisite.
- Any future migration must extend the chain in numeric order with a ledger version; silent schema mutation is forbidden.
- The supply-bridge work order, if proposed, must include the section-4 bootstrap contract and the missing-capability migrations; nothing is authorized automatically by this ADR.

## Alternatives Considered

- **Selected outright (name the bootstrap path now):** rejected because the complete path-generic bootstrap entrypoint is absent from the static artifacts; naming a path would have required either the dev-bound wrappers (wrong target) or the binary snapshot fixture (a database file, not static authority).
- **Blocked (no authority can be selected):** rejected because the static artifacts do determine the baseline, the ordered chain, the ledger, and the transaction/idempotence model; only the complete bootstrap runner is missing, which is a bounded, provable prerequisite rather than an unresolvable gap.
- **Adopt `schema.sql`/`database.py` as the isolated baseline:** rejected because that family has no ledger, no versioning, and none of the intake/evidence capabilities the supply contract requires; it is retained as legacy/reference.
- **Declare the development database or a test snapshot authoritative:** rejected because it violates the engineering standard (no formal database is authoritative without promotion evidence) and the work order's explicit prohibition.

## Evidence

Observed statically in SCHEMA-AUTHORITY-001 (see `docs/governance/SCHEMA_MIGRATION_INVENTORY.md`): 42 tracked SQL files at root; 35 apply wrappers; no wrappers for baseline or migrations 1–4; wrappers 5–15 hard-code `data/dev/teaching_docs_dev.db`; wrappers 28–39 declare prerequisites; `schema_manager.py` does not exist; importer modules and `collection/import_bridge.py` declare an already-initialized project-schema database; approved tests bootstrap fresh databases only through v2.16 and otherwise copy a binary snapshot fixture; `LEGACY_MODULE_MANIFEST.json` classifies all root modules including the schema tooling as legacy. No migration was run, no test executed, and no database opened.

## Affected Contracts

- `docs/architecture/SUPPLY_DELIVERY_CONTRACT.md` (gate G2 bridge binding now has a named schema home)
- `docs/governance/ENGINEERING_STANDARD.md` (explicit schema authority for isolated databases)
- `docs/architecture/SCHEMA_AUTHORITY.md` (operational decision detail)
- `docs/governance/SCHEMA_MIGRATION_INVENTORY.md` (evidence)
- Future work orders and completion reports that touch isolated candidate-intake databases

## Rollback or Supersession Conditions

A later user-approved ADR may supersede this decision. It must name the replacement baseline/chain, describe how existing isolated databases and ledgers migrate, record the new bootstrap entrypoint, and state the implementation and verification work required. A future work order may extend the chain with the documented missing capabilities; extending the chain does not require superseding this ADR.
