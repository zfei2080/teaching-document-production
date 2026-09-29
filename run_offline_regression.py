"""Single bounded offline regression entry for the current fail-closed main path.

Included modules use only Python's standard library and local project code.
External API tests and the archived pytest/visual pipeline are intentionally excluded.

The suite covers the current trusted-source intake, automatic curriculum mapping,
and textbook-scope decisions. It never runs imports against a project database.

Schema migration tests (test_schema_v2_*.py) are auto-discovered and appended in
sorted order so that new migrations are covered automatically without editing this file.
"""

from __future__ import annotations

import hashlib
import platform
import sqlite3
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent

# Auto-discover schema migration tests sorted by name; no manual update needed
# when a new migration (e.g. schema_v2_20) is added.
_SCHEMA_MIGRATION_MODULES: tuple[str, ...] = tuple(
    sorted(
        p.stem for p in ROOT.glob("test_schema_v2_*.py")
    )
)

INCLUDED_MODULES = (
    "test_legacy_entry_blocking",
    "test_automatic_gate",
    "test_verification_registry",
    "test_verification_coverage_report",
    "test_controlled_catalog_import",
    "test_catalog_audit",
    "test_controlled_knowledge_mapping",
    "test_controlled_question_mapping_import",
    "test_controlled_doc_conversion",
    "test_controlled_content_evidence",
    "test_controlled_content_temp_import",
    "test_source_library_intake",
    "test_content_library_extraction",
    "test_source_content_question_segmentation",
    "test_content_question_candidate_import",
    "test_content_knowledge_item_import",
    "test_question_mapping_audit",
    "test_question_classifier",
    "test_question_scope_features",
    "test_curriculum_progress_scope",
    "test_question_curriculum_mapping_evidence",
    "test_question_auto_mapping_audit",
    "test_run_p1_2_auto_mapping_audit",
    "test_p1_1a_draft_evidence",
    "test_p1_3_approval_precheck",
    "test_p1_3b_mapping_approval",
    "test_p1_3c_mapping_approval",
    "test_p1_2e_content_conversion_prerequisite_audit",
    "test_controlled_content_revision_import",
    "test_run_p1_2f_content_revision_import",
    "test_p1_4_pilot_readiness_audit",
    "test_schema_v2_30",
    "test_p1_4a_q013_admission",
    "test_p1_4b_isolated_source_derivation_audit",
    "test_p1_4b_isolated_pedagogical_role_contract_audit",
    "test_p1_4b_isolated_auxiliary_content_contract_audit",
    "test_p1_4b_controlled_source_discovery",
    "test_p1_4b_explicit_teaching_role_marker_discovery",
    "test_p1_4b_allowed_node_marker_gap_audit",
    "test_p1_4b_class_progress_scope_audit",
    "test_golden_sample_scope_evaluation",
    "test_textbook_scope_validator",
    "test_question_approval_state_machine",
    "test_current_input_snapshot",
    "test_automatic_verification_runner",
    "test_selection_engine",
    "test_production_workflow",
    "test_delivery_state_machine",
    "test_class_progress_state_machine",
    "test_normalize_catalog_import_status",
) + _SCHEMA_MIGRATION_MODULES
EXCLUDED_MODULES = {
    "test_api_pool.py": "requires an external API service and is not an offline gate",
    "test_pipeline.py": "archived pytest/API/visual pipeline; not a current production gate",
}
# This edition ships no question bank. The modules below take a snapshot of the
# development database as their fixture, so they can only run against a bank you
# created yourself. They are reported as skipped instead of silently passing.
BANK_DEPENDENT_MODULES = frozenset({
    "test_content_knowledge_item_import",
    "test_content_library_extraction",
    "test_content_question_candidate_import",
    "test_controlled_content_revision_import",
    "test_golden_sample_scope_evaluation",
    "test_legacy_entry_blocking",
    "test_p1_1a_draft_evidence",
    "test_p1_3b_mapping_approval",
    "test_p1_3c_mapping_approval",
    "test_p1_4_pilot_readiness_audit",
    "test_p1_4a_q013_admission",
    "test_p1_4b_class_progress_scope_audit",
    "test_p1_4b_controlled_source_discovery",
    "test_p1_4b_isolated_auxiliary_content_contract_audit",
    "test_p1_4b_isolated_pedagogical_role_contract_audit",
    "test_p1_4b_isolated_source_derivation_audit",
    "test_run_p1_2f_content_revision_import",
    "test_schema_v2_28",
    "test_schema_v2_29",
    "test_schema_v2_30",
    "test_schema_v2_31",
    "test_schema_v2_32",
    "test_schema_v2_33",
    "test_schema_v2_34",
    "test_schema_v2_35",
    "test_schema_v2_36",
    "test_schema_v2_37",
    "test_schema_v2_38",
    "test_schema_v2_39",
    "test_source_library_intake",
})
RUNNABLE_MODULES = tuple(m for m in INCLUDED_MODULES if m not in BANK_DEPENDENT_MODULES)
PROTECTED_DATABASES = (
    ROOT / "question_bank.db",
    ROOT / "data" / "dev" / "teaching_docs_dev.db",
)
# The open-source edition ships no question bank, so drift protection covers only
# databases that are actually present in this checkout.
PRESENT_DATABASES = tuple(path for path in PROTECTED_DATABASES if path.exists())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def snapshot_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    conn = sqlite3.connect(path)
    try:
        return {
            "questions": conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
            "approved_questions": conn.execute(
                "SELECT COUNT(*) FROM questions WHERE quality_status='approved'"
            ).fetchone()[0],
            "teaching_documents": conn.execute("SELECT COUNT(*) FROM teaching_documents").fetchone()[0],
            "quality_reports": conn.execute("SELECT COUNT(*) FROM quality_reports").fetchone()[0],
            "question_usage": conn.execute("SELECT COUNT(*) FROM question_usage").fetchone()[0],
        }
    finally:
        conn.close()


def main() -> int:
    print(f"python={platform.python_version()} executable={sys.executable}")
    print(f"working_directory={ROOT}")
    print("dependency_profile=stdlib-only for included regression modules")
    print("included_behaviour=" + ",".join(RUNNABLE_MODULES))
    print("included_schema_migrations=" + ",".join(_SCHEMA_MIGRATION_MODULES))
    print("bank_dependent_skipped=" + ",".join(sorted(BANK_DEPENDENT_MODULES)))
    for name, reason in EXCLUDED_MODULES.items():
        print(f"excluded={name}: {reason}")

    before_hashes = {path: sha256(path) for path in PRESENT_DATABASES}
    before_counts = snapshot_counts(ROOT / "data" / "dev" / "teaching_docs_dev.db")
    print("development_database_before="
          + (repr(before_counts) if before_counts else "absent (not shipped)"))

    suite = unittest.defaultTestLoader.loadTestsFromNames(RUNNABLE_MODULES)
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    after_hashes = {path: sha256(path) for path in PRESENT_DATABASES}
    after_counts = snapshot_counts(ROOT / "data" / "dev" / "teaching_docs_dev.db")
    print("development_database_after="
          + (repr(after_counts) if after_counts else "absent (not shipped)"))
    print("protected_databases_unchanged=" + str(before_hashes == after_hashes))

    if before_hashes != after_hashes:
        print("FAIL: a protected database changed during offline regression", file=sys.stderr)
        return 2
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
