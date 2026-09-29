"""Path-generic isolated schema bootstrap runner (ISOLATED-BOOTSTRAP-001,
hardened by ISOLATED-BOOTSTRAP-SAFETY-001).

Purpose
-------
Create and verify a **fresh, empty** SQLite database from the conditionally
selected isolated schema authority (ADR-0002): baseline ``schema_v2.sql``
followed by ``schema_v2_1.sql`` ... ``schema_v2_39.sql`` in strict numeric
order, with the ``schema_migrations`` ledger as the version truth.

This runner proves the runtime prerequisite of ``SCHEMA-AUTHORITY-001``:
a path-generic bootstrap entrypoint that creates a fresh database from the
SQL chain alone. It never uses, opens, copies, or inspects any existing
database, snapshot, source artifact, ``<local-scratch>/`` tree, collection/KPLC
output, or browser state.

Approved-root policy (ISOLATED-BOOTSTRAP-SAFETY-001)
----------------------------------------------------
Bootstrap targets are admitted only under approved roots:

- **Temporary root:** a root lexically contained by
  ``tempfile.gettempdir()`` (path comparison only).
- **Controlled root:** a root lexically contained by one explicitly
  configured parent path *and* containing the marker file
  ``.isolated-bootstrap-controlled-root`` whose UTF-8 text (whitespace
  stripped) equals the fixed, non-secret contract value
  ``isolated-bootstrap-controlled-root-v1``. A marker that cannot be
  decoded as UTF-8 is treated as unreadable, so the root is rejected
  (ISOLATED-BOOTSTRAP-SAFETY-002). The controlled parent has no
  default: it must be passed via ``--controlled-root-parent`` or the
  ``ISOLATED_BOOTSTRAP_CONTROLLED_ROOT_PARENT`` environment variable, and
  it must never be the repository root or a project data/source/backup/
  output/collection/KPLC/browser directory. If no parent is configured,
  only temporary roots are usable.
- Any other root -- repository root, repository subdirectories, ``<local-scratch>/``,
  data, source, backup, output, collection, KPLC, browser, or arbitrary
  paths -- is rejected with the stable path-only error
  ``ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP`` before target creation.
- The policy uses only lexical path comparison and, for a candidate
  controlled root, the marker file text. It never opens a database,
  snapshot, source artifact, or protected file.

Safety contract
---------------
- An explicit ``--root`` and ``--target`` are always required; there are no
  default database paths. Root admission always runs before any target
  check.
- The resolved target must not be a known protected database path
  (``question_bank.db``, ``data/dev/teaching_docs_dev.db``, and anything
  under ``data/dev/backups/``); rejection is pure path comparison and never
  opens or stats the protected file.
- The resolved target must lie under the resolved root (lexical path
  comparison only).
- The target must not already exist. The runner creates only the new target
  file and only inside the explicit root.
- The runner fails closed on: missing schema files, unapproved/unsafe/
  outside-root/protected/existing target paths, unexpected ledger state,
  migration errors, non-empty wrapper preconditions, prerequisite gaps,
  and foreign key violations. Output is deterministic and contains paths,
  versions, and statuses only (never SQL or row content).

Wrapper-specific behavior preserved (not bypassed)
--------------------------------------------------
The chain's apply wrappers are read-only for this work order; the runner
reproduces the wrapper-declared behavior where it matters:

- **Check-ledger-then-apply**: every step verifies the version is absent
  before applying and present afterwards (wrappers 16-39 semantics).
- **Prerequisites**: the declared prerequisites of wrappers 28-39 are
  enforced before each step (v2.29 needs v2.28; v2.30 needs v2.28+v2.29;
  v2.31 needs v2.29; v2.32 needs v2.30+v2.31; v2.34 needs v2.30; v2.35
  needs v2.34; v2.36 needs v2.35; v2.37 needs v2.36; v2.38 needs v2.37;
  v2.39 needs v2.38).
- **v2.29 (``apply_schema_v2_29.py``)**: the SQL file performs the rebuild
  inside ``BEGIN IMMEDIATE`` with foreign keys toggled off/on and does NOT
  insert its ledger row; the wrapper inserts the row afterwards inside the
  same transaction with rollback on failure, after seeding legacy current
  revisions. On a fresh chain the seed query finds no rows; the runner
  reproduces the query and **fails closed** if it ever finds rows (a
  non-empty precondition), then inserts the ledger row and commits.
- **v2.37 (``apply_schema_v2_37.py``)**: the wrapper captures the
  ``questions`` indexes and every trigger and view, disables foreign keys,
  drops all triggers/views, applies the rebuild SQL, recreates indexes,
  views, and triggers from the captured SQL, re-enables foreign keys, and
  requires ``PRAGMA foreign_key_check`` to be clean. Reproduced verbatim.
- **v2.39 (``apply_schema_v2_39.py``)**: the wrapper refuses to run when
  ``content_item_difficulty_evidence`` contains any row
  (``v2_39_refuses_ambiguous_legacy_difficulty_values``), drops only the
  dependent triggers/views of the rebuilt table, applies the repair SQL,
  recreates them, and requires a clean ``PRAGMA foreign_key_check``.
  Reproduced verbatim.
- **v2.17 / v2.24 / v2.25**: the SQL files themselves perform the
  ``quality_reports`` rebuild (v2.17), the view/trigger replacement
  (v2.24), and the hardened replacement (v2.25) in file order; the runner
  applies them in numeric order and verifies the ledger after each step.

Ledger expectation
------------------
A fresh chain completes with exactly 40 ledger versions in insertion
order: ``v2-initial-2026-07-25`` plus ``v2.1`` ... ``v2.39`` (the v2.29 row
is inserted by this runner reproducing its wrapper, not by the SQL file).

Scope boundary
--------------
This runner proves only that a fresh empty database can be built from the
SQL chain. It is not an importer, does not load source content, does not
establish QSR/Bridge Manifest identity, does not qualify candidates, and
makes no production, delivery, or supply-bridge claim. See
``docs/governance/ISOLATED_BOOTSTRAP_CONTRACT.md``.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# --------------------------------------------------------------------------
# The static chain (ADR-0002 / SCHEMA_MIGRATION_INVENTORY)
# --------------------------------------------------------------------------

BASELINE_FILE = "schema_v2.sql"
BASELINE_VERSION = "v2-initial-2026-07-25"

V2_1 = "v2.1-field-provenance-2026-07-25"
V2_2 = "v2.2-question-review-audit-2026-07-25"
V2_3 = "v2.3-teacher-final-review-2026-07-25"
V2_4 = "v2.4-automatic-verification-2026-07-25"
V2_5 = "v2.5-controlled-catalog-releases-2026-07-25"
V2_6 = "v2.6-controlled-import-provenance-2026-07-26"
V2_7 = "v2.7-provenance-link-integrity-2026-07-26"
V2_8 = "v2.8-catalog-audit-attestation-2026-07-26"
V2_9 = "v2.9-question-knowledge-point-import-provenance-2026-07-26"
V2_10 = "v2.10-question-mapping-audit-attestation-2026-07-26"
V2_11 = "v2.11-controlled-knowledge-mapping-audit-2026-07-26"
V2_12 = "v2.12-controlled-asset-adjacency-audit-2026-07-26"
V2_13 = "v2.13-question-approval-state-machine-2026-07-26"
V2_14 = "v2.14-current-input-snapshot-evidence-2026-07-26"
V2_15 = "v2.15-controlled-delivery-state-machine-2026-07-26"
V2_16 = "v2.16-class-progress-reuse-authorization-2026-07-26"
V2_17 = "v2.17-quality-gate-unsupported-missing-2026-07-26"
V2_18 = "v2.18-textbook-body-snapshot-chain-2026-07-26"
V2_19 = "v2.19-delivery-quality-gate-binding-2026-07-26"
V2_20 = "v2.20"
V2_21 = "v2.21-controlled-source-intake"
V2_22 = "v2.22-draft-question-curriculum-mapping-evidence"
V2_23 = "v2.23-question-auto-mapping-audits"
V2_24 = "v2.24-question-auto-mapping-audit-logs"
V2_25 = "v2.25-question-auto-mapping-audit-bundle-hardening"
V2_26 = "v2.26-controlled-content-evidence"
V2_27 = "v2.27-controlled-content-segment-revisions"
V2_28 = "v2.28-p13c-approval-boundary-and-mapping-revisions"
V2_29 = "v2.29-p12f-append-only-controlled-content-source-revisions"
V2_30 = "v2.30-p14-current-p13c-question-admission-path"
V2_31 = "v2.31-p14b-controlled-question-source-derivations"
V2_32 = "v2.32-p14b-current-pedagogical-role-evidence"
V2_33 = "v2.33-p14b-controlled-auxiliary-content-evidence"
V2_34 = "v2.34-database-enrichment-change-ledger"
V2_35 = "v2.35-database-enrichment-complete-source-inventory"
V2_36 = "v2.36-question-source-asset-evidence"
V2_37 = "v2.37-question-stage-optional-for-content-library"
V2_38 = "v2.38-content-item-mathematical-validation-contract"
V2_39 = "v2.39-repair-content-item-difficulty-enum"

MIGRATION_VERSIONS: Tuple[str, ...] = (
    V2_1, V2_2, V2_3, V2_4, V2_5, V2_6, V2_7, V2_8, V2_9, V2_10,
    V2_11, V2_12, V2_13, V2_14, V2_15, V2_16, V2_17, V2_18, V2_19, V2_20,
    V2_21, V2_22, V2_23, V2_24, V2_25, V2_26, V2_27, V2_28, V2_29, V2_30,
    V2_31, V2_32, V2_33, V2_34, V2_35, V2_36, V2_37, V2_38, V2_39,
)

MIGRATION_FILES: Tuple[str, ...] = tuple(
    f"schema_v2_{i}.sql" for i in range(1, 40)
)

EXPECTED_VERSIONS: Tuple[str, ...] = (BASELINE_VERSION,) + MIGRATION_VERSIONS

# Declared prerequisites of apply_schema_v2_28.py ... apply_schema_v2_39.py.
PREREQUISITES = {
    V2_29: (V2_28,),
    V2_30: (V2_28, V2_29),
    V2_31: (V2_29,),
    V2_32: (V2_30, V2_31),
    V2_34: (V2_30,),
    V2_35: (V2_34,),
    V2_36: (V2_35,),
    V2_37: (V2_36,),
    V2_38: (V2_37,),
    V2_39: (V2_38,),
}

# Seed query reproduced from apply_schema_v2_29.py; on a fresh chain it must
# return zero rows (the wrapper's legacy-current-revision seeding path).
V29_SEED_SELECT = """
SELECT s.*, src.import_run_id, run.manifest_sha256
  FROM controlled_content_segments s
  JOIN controlled_content_sources src ON src.id=s.source_id
  JOIN controlled_content_import_runs run ON run.id=src.import_run_id
 WHERE s.invalidated_at IS NULL
   AND src.fidelity_status='passed'
   AND run.status='validated'
 ORDER BY s.content_type, s.textbook_id, s.curriculum_node_id, s.id
"""

SCHEMA_DIR = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------
# Approved-root policy (ISOLATED-BOOTSTRAP-SAFETY-001)
# --------------------------------------------------------------------------
#
# A bootstrap root is admitted only if it is a fresh temporary root (lexically
# contained by the system temporary directory) or a controlled root (lexically
# contained by one explicitly configured parent path AND carrying the marker
# file below with exactly the fixed, non-secret contract value). The
# controlled parent has no default; it must be configured via
# ``--controlled-root-parent`` or the ``ISOLATED_BOOTSTRAP_CONTROLLED_ROOT_PARENT``
# environment variable, and it must never be the repository root or a project
# data/source/backup/output/collection/KPLC/browser directory. The policy uses
# only lexical path comparison and, for a candidate controlled root, the
# marker file text; it never opens a database, snapshot, source artifact, or
# protected file.

CONTROLLED_ROOT_MARKER_FILE = ".isolated-bootstrap-controlled-root"
CONTROLLED_ROOT_MARKER_VALUE = "isolated-bootstrap-controlled-root-v1"
CONTROLLED_ROOT_PARENT_ENV = "ISOLATED_BOOTSTRAP_CONTROLLED_ROOT_PARENT"

ROOT_NOT_APPROVED_ERROR = "ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP"


class BootstrapError(RuntimeError):
    """Fail-closed bootstrap failure with a stable machine-readable message."""


# --------------------------------------------------------------------------
# Path safety (lexical comparison only; nothing is opened or stat'ed for the
# containment/protection decisions)
# --------------------------------------------------------------------------


def _lexical(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def is_under_root(root: Path, target: Path) -> bool:
    """True when *target* resolves lexically under *root*."""
    root_n, target_n = _lexical(root), _lexical(target)
    try:
        return os.path.commonpath([root_n, target_n]) == root_n
    except ValueError:  # different drives
        return False


def _forbidden_paths() -> List[Path]:
    """Directory families that must never be a bootstrap root or the
    configured controlled parent: the repository root and project data,
    source, backup, output, collection, KPLC, and browser directories.

    Comparison-only; the paths need not exist.
    """
    names = (
        "<local-scratch>",
        "data",
        "source",
        "output",
        "backup",
        "backups",
        "collection",
        "kplc",
        "KPLC",
        "browser",
    )
    return [SCHEMA_DIR] + [SCHEMA_DIR / name for name in names]


def is_forbidden_path(path: Path) -> bool:
    """True when *path* is or lies under a forbidden directory family."""
    path_n = _lexical(path)
    sep = os.sep
    for forbidden in _forbidden_paths():
        forbidden_n = _lexical(forbidden)
        if path_n == forbidden_n or path_n.startswith(forbidden_n + sep):
            return True
    return False


def is_repository_root_or_subdirectory(path: Path) -> bool:
    """True when *path* is the repository root or lexically under it."""
    return is_under_root(SCHEMA_DIR, path)


def is_temporary_root(root: Path) -> bool:
    """True when *root* is lexically contained by the system temporary
    directory (pure path comparison; the root need not exist yet)."""
    return is_under_root(Path(tempfile.gettempdir()), root)


def resolve_controlled_root_parent(cli_value: Optional[Path]) -> Optional[Path]:
    """Explicit configuration only, never a default.

    The ``--controlled-root-parent`` CLI value wins over the
    ``ISOLATED_BOOTSTRAP_CONTROLLED_ROOT_PARENT`` environment variable; an
    unset or whitespace-only environment value means "not configured".
    """
    if cli_value is not None:
        return Path(cli_value)
    raw = os.environ.get(CONTROLLED_ROOT_PARENT_ENV, "")
    if raw.strip():
        return Path(raw)
    return None


def _controlled_root_marker_value(root: Path) -> Optional[str]:
    """Marker text of a candidate controlled root, or None when unreadable.

    Only the dedicated marker file is ever read; no database, snapshot,
    source artifact, or protected file is opened. A UTF-8 byte-order mark
    (an encoding artifact, not content) and surrounding whitespace are
    ignored; the remaining text must equal the fixed contract value exactly.
    A marker that cannot be decoded as UTF-8 (invalid byte sequences) is
    treated as unreadable and yields None, so the caller rejects the root
    with the stable ``ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP`` error
    instead of leaking ``UnicodeDecodeError`` (ISOLATED-BOOTSTRAP-SAFETY-002).
    """
    try:
        text = (root / CONTROLLED_ROOT_MARKER_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text.lstrip("\ufeff").strip()


def approve_root(root: Path, controlled_root_parent: Optional[Path] = None) -> None:
    """Admit *root* under the approved-root policy; raise BootstrapError with
    the stable ``ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP`` otherwise.

    Order of decision (all path comparison except the controlled marker):
    1. A configured controlled parent that is itself forbidden is a policy
       violation and rejects any candidate root.
    2. The repository root, repository subdirectories, and forbidden
       directory families are always rejected.
    3. When a controlled parent is configured and *root* is lexically under
       it, *root* is a controlled-root candidate and must carry the marker
       file with exactly the fixed contract value -- even when *root* also
       happens to lie under the temporary directory.
    4. Otherwise a root lexically contained by the system temporary
       directory is approved as a temporary root.
    5. Everything else (arbitrary, unmarked, unconfigured) is rejected.
    """
    if controlled_root_parent is not None and is_forbidden_path(
        controlled_root_parent
    ):
        raise BootstrapError(ROOT_NOT_APPROVED_ERROR)
    if is_forbidden_path(root) or is_repository_root_or_subdirectory(root):
        raise BootstrapError(ROOT_NOT_APPROVED_ERROR)
    if controlled_root_parent is not None and is_under_root(
        controlled_root_parent, root
    ):
        if _controlled_root_marker_value(root) != CONTROLLED_ROOT_MARKER_VALUE:
            raise BootstrapError(ROOT_NOT_APPROVED_ERROR)
        return
    if is_temporary_root(root):
        return
    raise BootstrapError(ROOT_NOT_APPROVED_ERROR)


def protected_paths() -> List[Path]:
    """Known protected database paths, documented in governance records.

    - ``question_bank.db``: legacy question-bank family (schema.sql/database.py).
    - ``data/dev/teaching_docs_dev.db``: the development database.
    - ``data/dev/backups/``: binary snapshot test-fixture family.
    """
    return [
        SCHEMA_DIR / "question_bank.db",
        SCHEMA_DIR / "data" / "dev" / "teaching_docs_dev.db",
        SCHEMA_DIR / "data" / "dev" / "backups",
    ]


def is_protected_target(target: Path) -> bool:
    """Reject known protected database paths by path comparison only.

    Never touches the filesystem: no stat, no open, no hash of any file.
    """
    target_n = _lexical(target)
    sep = os.sep
    for protected in protected_paths():
        protected_n = _lexical(protected)
        if target_n == protected_n or target_n.startswith(protected_n + sep):
            return True
    return False


def validate_target(
    root: Path,
    target: Path,
    controlled_root_parent: Optional[Path] = None,
) -> Path:
    """Validate an explicit root/target pair; raise BootstrapError on refusal.

    Root admission (approved-root policy) always runs first and rejects the
    repository root, repository subdirectories, forbidden directory families,
    and arbitrary roots with ``ROOT_NOT_APPROVED_FOR_ISOLATED_BOOTSTRAP``
    before any target consideration. The fail-closed target checks
    (protected, outside-root, existing, missing parent) execute afterwards,
    all by path comparison only.
    """
    approve_root(root, controlled_root_parent)
    if not root.is_dir():
        raise BootstrapError("ROOT_MISSING_OR_NOT_DIRECTORY")
    if is_protected_target(target):
        raise BootstrapError("TARGET_IS_PROTECTED_DATABASE")
    if not is_under_root(root, target):
        raise BootstrapError("TARGET_OUTSIDE_ROOT")
    if target.exists():
        raise BootstrapError("TARGET_ALREADY_EXISTS")
    if not target.parent.is_dir():
        raise BootstrapError("TARGET_PARENT_MISSING")
    return target


# --------------------------------------------------------------------------
# Chain application
# --------------------------------------------------------------------------


def _schema_text(name: str) -> str:
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise BootstrapError(f"MISSING_SCHEMA_FILE:{name}")
    return path.read_text(encoding="utf-8")


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _require_applied(conn: sqlite3.Connection, version: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
    ).fetchone()
    if row is None:
        raise BootstrapError(f"LEDGER_MISSING_VERSION:{version}")


def _require_not_applied(
    conn: sqlite3.Connection, version: str, allow_missing_table: bool = False
) -> None:
    try:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
        ).fetchone()
    except sqlite3.OperationalError as exc:
        # Before the baseline, the ledger table does not exist yet; that is
        # the expected state of a fresh target, not a ledger anomaly.
        if allow_missing_table and "no such table" in str(exc):
            return
        raise
    if row is not None:
        raise BootstrapError(f"UNEXPECTED_LEDGER_STATE:{version}")


def _check_prerequisites(conn: sqlite3.Connection, version: str) -> None:
    for required in PREREQUISITES.get(version, ()):
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (required,)
        ).fetchone()
        if row is None:
            raise BootstrapError(f"PREREQUISITE_MISSING:{version} requires {required}")


def _verify_ledger_count(conn: sqlite3.Connection, expected: int) -> None:
    count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    if count != expected:
        raise BootstrapError(f"UNEXPECTED_LEDGER_COUNT:expected={expected} actual={count}")


def _apply_baseline(conn: sqlite3.Connection, emit: Callable[[str], None]) -> None:
    _require_not_applied(conn, BASELINE_VERSION, allow_missing_table=True)
    emit(f"STEP=baseline {BASELINE_FILE}")
    emit(f"VERSION={BASELINE_VERSION}")
    conn.executescript(_schema_text(BASELINE_FILE))
    _require_applied(conn, BASELINE_VERSION)
    conn.commit()
    _verify_ledger_count(conn, 1)
    emit(f"APPLIED={BASELINE_VERSION}")


def _apply_generic(
    conn: sqlite3.Connection, index: int, emit: Callable[[str], None]
) -> None:
    """Check-ledger-then-apply for migrations without wrapper-specific work."""
    version = MIGRATION_VERSIONS[index - 1]
    sql_name = MIGRATION_FILES[index - 1]
    _require_not_applied(conn, version)
    _check_prerequisites(conn, version)
    emit(f"STEP=migration {sql_name}")
    emit(f"VERSION={version}")
    conn.executescript(_schema_text(sql_name))
    _require_applied(conn, version)
    conn.commit()
    _verify_ledger_count(conn, index + 1)
    emit(f"APPLIED={version}")


def v29_seed_rows(conn: sqlite3.Connection) -> List[tuple]:
    """Rows the v2.29 wrapper would seed; must be empty on a fresh chain."""
    return conn.execute(V29_SEED_SELECT).fetchall()


def _check_v229_seed_precondition(conn: sqlite3.Connection) -> None:
    if v29_seed_rows(conn):
        raise BootstrapError(
            "V229_NON_EMPTY_PRECONDITION:controlled_content_* rows require "
            "the wrapper's legacy seeding path, which a fresh bootstrap must "
            "never need; refusing"
        )


def _apply_v29(conn: sqlite3.Connection, emit: Callable[[str], None]) -> None:
    """Reproduce apply_schema_v2_29.py: rebuild + wrapper-inserted ledger row.

    The SQL file runs ``PRAGMA foreign_keys=OFF``, ``BEGIN IMMEDIATE``, the
    rebuild, and ``PRAGMA foreign_keys=ON`` without committing; the wrapper
    then seeds legacy current revisions (a no-op on a fresh chain), inserts
    the ledger row, and commits inside the same transaction with rollback on
    failure. The runner reproduces exactly that, failing closed if the seed
    query ever finds rows.
    """
    sql_name = "schema_v2_29.sql"
    _require_not_applied(conn, V2_29)
    _check_prerequisites(conn, V2_29)
    emit(f"STEP=migration {sql_name}")
    emit(f"VERSION={V2_29}")
    try:
        conn.executescript(_schema_text(sql_name))
        _check_v229_seed_precondition(conn)
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) "
            "VALUES(?, datetime('now'))",
            (V2_29,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _require_applied(conn, V2_29)
    _verify_ledger_count(conn, 30)
    emit(f"APPLIED={V2_29}")


def _apply_v37(conn: sqlite3.Connection, emit: Callable[[str], None]) -> None:
    """Reproduce apply_schema_v2_37.py: drop/recreate dance + FK check.

    SQLite validates triggers on other tables that reference ``questions``
    and recompiles views during the rebuild, so every trigger and view is
    captured, dropped, and recreated from its original SQL around the
    rebuild; ``questions`` indexes are recreated too. ``PRAGMA
    foreign_key_check`` must be clean.
    """
    sql_name = "schema_v2_37.sql"
    _require_not_applied(conn, V2_37)
    _check_prerequisites(conn, V2_37)
    emit(f"STEP=migration {sql_name}")
    emit(f"VERSION={V2_37}")
    if conn.in_transaction:
        conn.commit()
    question_indexes = [
        row[0]
        for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name='questions' AND sql IS NOT NULL ORDER BY name"
        )
    ]
    trigger_rows = conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
    ).fetchall()
    view_rows = conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='view' ORDER BY name"
    ).fetchall()
    conn.execute("PRAGMA foreign_keys=OFF")
    for name, _statement in trigger_rows:
        conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    for name, _statement in view_rows:
        conn.execute('DROP VIEW "' + name.replace('"', '""') + '"')
    conn.executescript(_schema_text(sql_name))
    for statement in question_indexes:
        conn.execute(statement)
    for _name, statement in view_rows:
        conn.execute(statement)
    for _name, statement in trigger_rows:
        conn.execute(statement)
    conn.execute("PRAGMA foreign_keys=ON")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise BootstrapError(f"V237_FOREIGN_KEY_CHECK_FAILED:{violations!r}")
    _require_applied(conn, V2_37)
    _verify_ledger_count(conn, 38)
    emit(f"APPLIED={V2_37}")


def _check_v239_empty_precondition(conn: sqlite3.Connection) -> None:
    count = conn.execute(
        "SELECT COUNT(*) FROM content_item_difficulty_evidence"
    ).fetchone()[0]
    if count:
        raise BootstrapError("V239_REFUSES_AMBIGUOUS_LEGACY_DIFFICULTY_VALUES")


def _apply_v39(conn: sqlite3.Connection, emit: Callable[[str], None]) -> None:
    """Reproduce apply_schema_v2_39.py: empty-table refusal + dependent-object
    drop/recreate + FK check."""
    sql_name = "schema_v2_39.sql"
    _require_not_applied(conn, V2_39)
    _check_prerequisites(conn, V2_39)
    emit(f"STEP=migration {sql_name}")
    emit(f"VERSION={V2_39}")
    conn.execute("PRAGMA foreign_keys=ON")
    _check_v239_empty_precondition(conn)
    trigger_rows = conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name='content_item_difficulty_evidence' ORDER BY name"
    ).fetchall()
    view_rows = conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='view' "
        "AND sql LIKE '%content_item_difficulty_evidence%' ORDER BY name"
    ).fetchall()
    for name, _statement in view_rows:
        conn.execute("DROP VIEW " + _quoted(name))
    for name, _statement in trigger_rows:
        conn.execute("DROP TRIGGER " + _quoted(name))
    conn.executescript(_schema_text(sql_name))
    for _name, statement in trigger_rows:
        conn.execute(statement)
    for _name, statement in view_rows:
        conn.execute(statement)
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise BootstrapError(f"V239_FOREIGN_KEY_CHECK_FAILED:{violations!r}")
    _require_applied(conn, V2_39)
    _verify_ledger_count(conn, 40)
    emit(f"APPLIED={V2_39}")


def _verify_final_ledger(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT version FROM schema_migrations ORDER BY rowid"
    ).fetchall()
    actual = [row[0] for row in rows]
    if actual != list(EXPECTED_VERSIONS):
        raise BootstrapError(
            "UNEXPECTED_LEDGER_STATE:"
            f"expected={len(EXPECTED_VERSIONS)} actual={len(actual)}"
        )


@dataclass
class BootstrapResult:
    """Deterministic outcome of a successful fresh bootstrap."""

    target: Path
    root: Path
    versions_applied: List[str] = field(default_factory=list)
    ledger_count: int = 0
    foreign_key_check_clean: bool = True


def bootstrap(
    root: Path,
    target: Path,
    emit: Callable[[str], None] = print,
    controlled_root_parent: Optional[Path] = None,
) -> BootstrapResult:
    """Bootstrap a fresh database at *target* under explicit *root*.

    Raises BootstrapError on any unapproved root, unsafe path, missing file,
    unexpected ledger state, migration error, non-empty precondition, or
    integrity failure. Emits deterministic path/version/status lines via
    *emit*. ``controlled_root_parent`` enables the controlled-root admission
    path (see ``approve_root``); it has no default and is never derived from
    the environment here -- only ``main`` resolves CLI/environment
    configuration.
    """
    resolved = validate_target(root, target, controlled_root_parent)
    for name in (BASELINE_FILE,) + MIGRATION_FILES:
        if not (SCHEMA_DIR / name).is_file():
            raise BootstrapError(f"MISSING_SCHEMA_FILE:{name}")

    emit(f"TARGET={_lexical(resolved)}")
    emit(f"ROOT={_lexical(root)}")

    conn = sqlite3.connect(resolved)
    versions: List[str] = []
    try:
        _apply_baseline(conn, emit)
        versions.append(BASELINE_VERSION)
        for index in range(1, 40):
            if index == 29:
                _apply_v29(conn, emit)
            elif index == 37:
                _apply_v37(conn, emit)
            elif index == 39:
                _apply_v39(conn, emit)
            else:
                _apply_generic(conn, index, emit)
            versions.append(MIGRATION_VERSIONS[index - 1])
        _verify_final_ledger(conn)
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise BootstrapError(f"FINAL_FOREIGN_KEY_CHECK_FAILED:{violations!r}")
    finally:
        conn.close()

    emit(f"LEDGER_COUNT={len(EXPECTED_VERSIONS)}")
    emit("FOREIGN_KEY_CHECK=clean")
    emit("STATUS=BOOTSTRAP_OK")
    return BootstrapResult(
        target=resolved,
        root=root,
        versions_applied=versions,
        ledger_count=len(EXPECTED_VERSIONS),
        foreign_key_check_clean=True,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create and verify a fresh isolated SQLite database from the "
            "v2 schema chain (schema_v2.sql + schema_v2_1.sql..schema_v2_39.sql)."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="explicit root directory; the target must resolve under it",
    )
    parser.add_argument(
        "--target",
        type=Path,
        required=True,
        help="database file to create; must not exist and must not be a "
        "protected database path",
    )
    parser.add_argument(
        "--controlled-root-parent",
        type=Path,
        default=None,
        help="explicit parent directory for a marked controlled root; no "
        "default (environment: ISOLATED_BOOTSTRAP_CONTROLLED_ROOT_PARENT); "
        "must never be the repository root or a project data/source/backup/"
        "output/collection/KPLC/browser directory",
    )
    args = parser.parse_args(argv)
    controlled_root_parent = resolve_controlled_root_parent(
        args.controlled_root_parent
    )
    try:
        bootstrap(
            args.root, args.target, controlled_root_parent=controlled_root_parent
        )
    except BootstrapError as exc:
        print(f"STATUS=REJECTED {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"STATUS=FAILED {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
