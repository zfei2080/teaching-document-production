# Maturity and Worktree Register

## Register Basis

This is a factual governance register updated by `BRANCH-RECONCILE-001` on 2026-08-14 from read-only Git metadata only (`git worktree list --porcelain`, `git branch -vv --no-abbrev --all`, `git status --short --untracked-files=all`, bounded `git log`, `git rev-list`, `git diff --stat`, `git ls-remote --heads origin`) and the work order's allowed control documents. It does not inspect code, databases, source artifacts, browser state, KPLC corpus/output, or historical test results. Protected paths (`<local-scratch>/`, collection output) were not read; only path names visible in `git status` are recorded, and `<local-scratch>/` file names are not enumerated.

## Observed Worktrees (2026-08-14)

### Root worktree

- **Path:** `<local-path>`
- **Branch:** `main`; HEAD `770b9dc308ffccc85b570c0ed44527622b208c31`; upstream `origin/main` (0 ahead, 0 behind). 160 total commits.
- **Dirty state:** `PROJECT_CONTROL.md` and `task_plan.md` modified (authorized files carrying the approved-work-order record); untracked `<local-scratch>/` (protected, not read, not staged) and untracked `docs/work_orders/BRANCH-RECONCILE-001.md` (authorized, committed by this work order). No other changed or untracked paths.
- **Ancestry:** common ancestor with the collection line is `9fe6407` (root is 31 commits ahead of it; collection branches are 1-13 commits ahead of it). Common ancestor with the claude branches is the initial commit `dc84b38f` (root is 159 commits ahead of it).
- **Linked governance:** `GOVERNANCE-RESET-001`, `GOVERNANCE-REPORT-CORRECTION-001`, `GOVERNANCE-CLOSURE-PROTOCOL-001`, `ENGINEERING-BASELINE-001`, `ENGINEERING-TEST-MARKER-001`, and `BRANCH-RECONCILE-001` (this work order). The root history also contains KPLC direction-A implementation commits and the collection-004 batch spec and round reports (commit subjects observed; contents not inspected).
- **Maturity:** `legacy` - the root code is retained for audit/migration reference per governance; this review did not run it.
- **Promotion or merge prerequisite:** schema authority and contract-scoped promotion work under a dedicated approved work order; no merge under `BRANCH-RECONCILE-001`.

### Collection worktree for branch `main`

- **Path:** `<local-path>` (directory suffix is `-001` while the branch suffix is `-002`).
- **Branch:** `main`; HEAD `a48eadefb0ab934440246c696e9629a07cead653`; upstream `origin/main` (0 ahead, 0 behind). 134 total commits; 5 commits unique versus the root branch: live collector and inline answer extraction, SPA routing/gesture hardening, completion report for live validation, and the cleanup-incident completion-report record.
- **Dirty state:** `docs/completion_reports/extsrc-BROWSER-COLLECTION-002.md` modified and uncommitted (local-only; not absorbed by this work order). No untracked files.
- **Ancestry:** common ancestor with the root baseline is `9fe6407`; unique content versus that ancestor: 29 files, +3841 lines (stat only).
- **Linked governance:** no `extsrc-BROWSER-COLLECTION-002` work order file exists in the tracked inventory. The 002 completion report exists in two lines via different commits: `5d091c3`/`a48eade` on the 002 branch and `bd61541` on the root line; contents were not compared. Historical report claims were not re-run.
- **Maturity:** `experimental` - isolated proof with no production claim.
- **Promotion or merge prerequisite:** `SUPPLY-BRIDGE-001` (or a separate collection-bridge work order) and a dedicated reconciliation gate; no merge under this work order.

### Collection worktree for branch `main`

- **Path:** `<local-path>`
- **Branch:** `main`; HEAD `969673566bf2a5116c0815e6f47978d20f29c280`; upstream `origin/main` at `3af1a85` (3 ahead, 0 behind). 142 total commits; 13 commits unique versus the root branch.
- **Local-only commits (3):** cross-day resume runbook with login-state probe, cli import fix, and A1 offline hardening (12 items). All other branch commits match the remote.
- **Dirty state:** `collection/special_group.py` and `test_collection_special_group.py` modified and uncommitted (local-only; not absorbed); untracked `<local-scratch>/` (protected, names not enumerated).
- **Ancestry:** common ancestor with the root baseline is `9fe6407`. Unique content versus the 003 tip (`3bf9c9b`): 16 files, +2652/-53 lines (stat only), covering collection driver/ledger/eye-assistant/special-group modules, runbooks, probe scripts, and tests.
- **Linked governance:** the `extsrc-BROWSER-COLLECTION-004` work order and its round reports are tracked on the root branch only (commits `bd61541`, `63066df`, `d4f2e0e`, `5424c9d`, `a5c554d`, `dcc80c8`, `0a4e94f`); the 004 branch itself does not track them (observed `git ls-files` on the 004 worktree). Collection Step 9 is unclosed; browser export, resume, and budget expansion remain paused.
- **Maturity:** `experimental`, blocked.
- **Promotion or merge prerequisite:** remains blocked until the isolated bridge/import/reconciliation gate closes and its report agrees with actual state, per the source-acquisition ADR resolution and `SUPPLY-BRIDGE-001` (or a separate collection-bridge work order); no merge under this work order.

### Claude worktree

- **Path:** `<local-path>`
- **Branch:** `main`; HEAD `dc84b38f0360314c8b1ce05e9ecea68fb20f27c3` (the initial commit; its tree contains `README.md` only); no upstream; clean (no changed or untracked paths); 0 commits unique versus the root branch (root is 159 ahead).
- **Sibling branches at the same initial commit, no worktree, no upstream:** `main`, `main`, `main`, `main`.
- **Maturity:** `unknown` - purpose and content were not reviewed by this work order.
- **Promotion or merge prerequisite:** a future approved inventory/reconciliation work order before any promotion or merge.

### Branches without worktrees

- `main` at `05890e0` (equal to `origin`; subsystem core implementation) and `main` at `3bf9c9b` (equal to `origin`; skip-already-collected fix). Same experimental causal chain; maturity `experimental`.

### KPLC

- No KPLC branch or worktree exists in local refs or in observed `origin` heads. KPLC direction-A implementation commits exist in the root branch history (commit subjects observed only). KPLC contents and records were not inspected (protected). Untracked `<local-scratch>/` in the root worktree is present but was not read.
- **Maturity:** `candidate_only`, detached and unverified; exact QSR binding was not verified and commits on the root branch do not promote it.
- **Promotion or merge prerequisite:** `KPLC-RECONCILIATION-001` after the canonical QSR bridge is available.

## Summary Table

| Item | Observed location or branch | Current maturity | Observed fact | Promotion or merge prerequisite |
| --- | --- | --- | --- | --- |
| Root legacy code | Root worktree, branch `main` | `legacy` | 160 commits, in sync with `origin`; authorized control docs modified; untracked `<local-scratch>/` not read. Not run by this review. | Schema authority and contract-scoped promotion work under a dedicated approved work order. |
| Collection worktree | Worktree `extsrc-browser-collection-001`, branch `main` | `experimental` | In sync with `origin`; 5 unique commits vs root; 002 completion report has uncommitted local modifications; no 002 work order file tracked. | `SUPPLY-BRIDGE-001` (or separate collection-bridge work order) and a dedicated reconciliation gate. |
| Collection worktree | Worktree `extsrc-browser-collection-004`, branch `main` | `experimental`, blocked | Ahead 3 of `origin` (local-only commits), 2 modified uncommitted files, untracked `<local-scratch>/`; 004 governance docs tracked on root line only. | Isolated bridge/import/reconciliation gate must close and its report must agree with actual state; source-acquisition ADR; `SUPPLY-BRIDGE-001`. |
| KPLC records | No KPLC branch or worktree observed | `candidate_only` | KPLC direction-A commits exist in root history (subjects only); exact QSR binding not verified; contents not inspected. | `KPLC-RECONCILIATION-001` after the canonical QSR bridge is available. |
| Claude worktree | Worktree `intelligent-leavitt-eb4d00`, branch `main` | `unknown` | At the initial commit (`README.md` only), clean, no upstream; 4 sibling claude branches at the same commit without worktrees. | A future approved inventory/reconciliation work order before any promotion or merge. |

## Contradictions with Prior Records

- `PROJECT_CONTROL.md` previously recorded "one additional `claude` branch"; five claude branches are now observed, all at the initial commit, one checked out in a worktree.
- The 002 worktree directory is named `extsrc-browser-collection-001` while its branch is `main`.
- The 002 completion report exists in two lines through different commits (`5d091c3`/`a48eade` on the 002 branch; `bd61541` on the root line); contents were not compared.
- No `extsrc-BROWSER-COLLECTION-002` work order file exists in the tracked inventory.
- The 004 work order and round reports are tracked on the root branch only, not on the 004 branch.
- The 004 branch is ahead 3 of its remote and carries uncommitted modifications; prior records listed it only as a registered worktree.

## Integration Criteria

No item is production-ready merely because it has a branch, worktree, script, historical report, or test claim. Before a promotion or merge, a dedicated approved work order must identify ownership, reconcile the canonical QSR and bridge-manifest contract, run appropriate L0-L4 evidence, preserve unrelated worktree state, and obtain the required release decision. A clean branch is not automatically merge-ready, and a completion report is not proof that all work-order steps executed.

## Current Freeze

No merge, reset, cleanup, deletion, or repurposing of these worktrees is authorized. New browser export, resume, and budget expansion remain paused until the source-acquisition ADR resolves the download-budget policy conflict or the user approves a temporary policy in a separate work order. Uncommitted changes remain with their current worktree and are not absorbed by any governance document update.

## Fact and Uncertainty Boundary

The worktree and branch observations above are current-review facts from read-only Git metadata. Maturity labels are governance classifications, not test outcomes. Historical completion-report claims, KPLC contents, collection behavior, and root-code behavior were not re-run and must be measured by their listed future work orders. Unknown purpose or protected state stays unknown; it cannot become a production claim. The user reserves merge, promotion, and source-policy decisions.
