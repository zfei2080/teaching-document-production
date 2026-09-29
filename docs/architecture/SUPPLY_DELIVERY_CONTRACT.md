# Supply and Delivery Contract

## Purpose

Formal delivery and supply acquisition are two controlled loops. They meet only through the canonical intake contract and supply-planning contract. A rough lecture proof, official-browser export, isolated import, and KPLC supply mapping are evidence-producing stages in one causal chain; none is a competing formal delivery route.

## Core Records

| Record | Contract purpose | Identity rule |
| --- | --- | --- |
| Delivery Card | Versioned request for one class, scope, output, and required supply buckets. | Delivery Card version is its controlled request identity. |
| SourceArtifact | Immutable representation of an acquired source artifact plus provenance and acquisition evidence. | Content and provenance contract, not a filename-only identity. |
| QSR | Question Source Revision, the canonical source-derived question revision. | Only canonical cross-system question identity after exact binding. |
| Parse-Import Evidence | Records how a SourceArtifact became one or more QSR candidates and what fidelity checks observed. | Evidence references source and QSR identities. |
| Decision-Usage Evidence | Records gate decisions, selection, rendering, delivery, and class usage. | Evidence references controlled decision and QSR identities. |
| Bridge Manifest | Reconciliation artifact connecting export, isolated import, and canonical QSR outcomes. | Must provide exact source-to-QSR binding or declare failure. |

File name, question number, run ID, position, and semantic hash are correlation clues only. None is a cross-system identity key. A semantic hash may support duplicate review but cannot assert QSR identity.

## Supply Buckets and Delivery Card

Each Delivery Card declares immutable versioned constraints: class, textbook/version, school stage, topic/scope, document type, requested question buckets, quantity, difficulty/format needs, reuse constraints, and delivery deadline or review conditions when approved. Supply planning returns one result per bucket:

- `sufficient`: eligible canonical QSR supply meets the requested constraints.
- `insufficient`: measured eligible supply does not meet quantity or required composition.
- `blocked`: evidence, policy, or gate failure prevents use.
- `pending_measurement`: a future approved work order must produce the measurement.

No bucket may be satisfied by crossing Delivery Card scope. A shortage produces a blocking report.

## Controlled Loops

### Supply Loop

1. Acquire or register a SourceArtifact under an approved source-acquisition policy.
2. Parse and import in isolation; record Parse-Import Evidence.
3. Produce a Bridge Manifest and exact QSR binding.
4. Run fidelity, scope, eligibility, and duplicate handling gates.
5. Publish only eligible QSR supply to the supply-planning view.

### Delivery Loop

1. Version and approve a Delivery Card.
2. Compute bucketed supply results against eligible QSRs.
3. Select only approved, in-scope QSR revisions.
4. Render and review the requested output.
5. Record formal delivery and then same-class usage only when the delivery gate passes.

## State Machine

`observed -> candidate_only -> intake_bound -> fidelity_checked -> scope_checked -> eligible -> planned -> selected -> rendered -> reviewed -> delivered -> usage_recorded`

Transitions are fail-closed. `candidate_only` may retain detached evidence but cannot transition to `planned`, `selected`, `delivered`, or `usage_recorded`. A new source revision creates a new QSR revision or blocks for reconciliation; it does not overwrite prior evidence.

## Gates

| Gate | Required result before advancing |
| --- | --- |
| G0 Source policy | Approved source/acquisition context and SourceArtifact registration. |
| G1 Isolation | Import work is isolated from formal inventory. |
| G2 Bridge | Bridge Manifest has exact SourceArtifact-to-QSR binding and reconciliation outcome. |
| G3 Fidelity | Required source fidelity evidence is present and passes the approved criteria. |
| G4 Scope | Textbook, stage, topic, and Delivery Card applicability are determined. |
| G5 Eligibility | QSR is eligible under approved policy, including required metadata and restrictions. |
| G6 Supply/selection | Delivery Card buckets are sufficient and selected QSRs meet reuse rules. |
| G7 Rendering/review | Output renders and passes the approved review evidence. |
| G8 Delivery/usage | Formal delivery is recorded; only then may same-class usage history be written. |

## Duplicate Handling

Duplicates are reviewed through evidence, not guessed identity. Exact canonical QSR binding determines source-derived identity. Semantic or structural similarity can raise a duplicate-review candidate; it cannot merge QSRs, bypass gates, or support class-reuse decisions. Same-class prior delivery excludes reuse unless a future approved policy explicitly authorizes an exception.

## Protected Boundaries

- Trusted source content is immutable at the QSR source-content layer. Any adaptation must be explicit, separately identified, and governed by a future approved contract.
- Browser artifacts, collection logs, candidate records, and KPLC evidence remain outside formal inventory until G2 closes with exact binding.
- Collection Step 9 remains unclosed until an isolated bridge/import/reconciliation work order provides evidence.
- Existing KPLC records remain `candidate_only` until exact QSR binding is verified.
- The rough lecture sample is a technical proof only, not G7/G8 evidence for formal delivery.

## Fact and Uncertainty Boundary

Observed in this review: the contract requirements above are governance decisions. Historical reports mention prior collection, import, and sample work but were not re-run. Future work orders must define data representations, execute gates, and measure outcomes. The user reserves source-acquisition budget policy and any temporary exception.
