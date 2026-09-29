# Agent / contributor instructions

This is the open-source edition. It ships structure, logic, specifications and verification,
plus a **text-stripped** real question bank (`data/sample/teaching_docs_sample_bank.db.gz`) —
all question text, options wording, answers, explanations and source documents were removed,
so **there is no third-party content here**. No collection automation is shipped either.

Read in this order:

1. `README.md` — product goal and the current blocker.
2. `CONTRIBUTING.md` — the process, hard product rules, verification requirements, boundaries.
3. `docs/spec/PROJECT_SPECIFICATION.md` — durable product scope.
4. `docs/architecture/` and `docs/governance/` — supply/delivery contract, schema authority,
   engineering standard, maturity register, isolated-bootstrap contract.
5. `docs/adr/` — accepted decisions.

Notes specific to this edition:

- There is no `PROJECT_CONTROL.md` and no work-order/completion-report history here. Those lived in the
  private development line; the process they enforced is described in `CONTRIBUTING.md` and is the part
  worth keeping.
- Root-level modules are legacy or transitional until promoted under
  `docs/governance/MATURITY_AND_WORKTREE_REGISTER.md`. Do not treat them as a formal delivery path.
- Vendor identifiers were replaced with neutral placeholders (`extsrc`), local absolute paths with
  `<local-path>`. Do not reintroduce real vendor names or local paths.
- Verify with `python run_offline_regression.py`. Never state that a check passed unless it was run.
- Do not commit question text, answers, credentials, cookies, tokens, or local source paths.
