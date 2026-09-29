"""Fail-closed textbook-scope validation from controlled catalog mappings.

This validator deliberately does not infer a textbook or course node from the
question stem. A passing result means that an approved question mapping,
curriculum node, knowledge-point relation, and catalog release agree exactly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from input_snapshot import compute_approval_input_hash_v2


VALIDATOR_ID = "controlled_textbook_scope"
VALIDATOR_VERSION = "v1"


@dataclass(frozen=True)
class TextbookScopeResult:
    status: str  # pass | fail | unsupported
    evidence: dict[str, object]


def _has_p1_3c_revision_tables(conn: sqlite3.Connection) -> bool:
    required = {
        "current_question_mapping_source_revisions",
        "question_approval_input_snapshots_v2",
        "current_question_mapping_approval_evidence_v2",
        "current_question_mapping_auto_audits_v2",
        "question_mapping_approval_audits_v2",
    }
    found = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }
    return required.issubset(found)


def _p1_3c_scope_evidence(
    conn: sqlite3.Connection,
    *,
    question_id: str,
    textbook_id: str,
    curriculum_node_id: str,
) -> dict[str, object] | None:
    """Return a current P1-3c revision approval or an explicit blocker.

    ``None`` means no P1-3c revision exists for the mapping.  A dictionary
    means a revision exists, so legacy import provenance must not be used as a
    fallback if that revision has become stale.
    """
    if not _has_p1_3c_revision_tables(conn):
        return None
    revision = conn.execute(
        """SELECT id, revision_hash, source_hash, source_file_path, mapping_hash, head_revision
             FROM current_question_mapping_source_revisions
            WHERE question_id=? AND textbook_id=? AND curriculum_node_id=?""",
        (question_id, textbook_id, curriculum_node_id),
    ).fetchone()
    if revision is None:
        return None
    snapshot = conn.execute(
        """SELECT input_hash, revision, invalidated_at
             FROM question_approval_input_snapshots_v2 WHERE question_id=?""",
        (question_id,),
    ).fetchone()
    if snapshot is None or snapshot[2] is not None:
        return {"status": "unsupported", "reason": "p1_3c_approval_snapshot_missing_or_invalidated"}
    try:
        current_hash = compute_approval_input_hash_v2(conn, question_id)
    except (OSError, ValueError, sqlite3.Error) as exc:
        return {"status": "unsupported", "reason": f"p1_3c_approval_input_unavailable:{exc}"}
    if snapshot[0] != current_hash:
        return {"status": "unsupported", "reason": "p1_3c_approval_snapshot_stale"}
    approved = conn.execute(
        """SELECT a.id, a.audit_manifest_hash, a.audit_method,
                  e.evidence_hash, aa.audit_hash
             FROM question_mapping_approval_audits_v2 a
             JOIN current_question_mapping_approval_evidence_v2 e ON e.id=a.evidence_id
             JOIN current_question_mapping_auto_audits_v2 aa ON aa.id=a.auto_audit_id
            WHERE a.source_revision_id=? AND a.status='approved'
              AND e.question_id=? AND e.textbook_id=? AND e.curriculum_node_id=?
              AND e.approval_input_hash=? AND e.approval_snapshot_revision=?""",
        (revision[0], question_id, textbook_id, curriculum_node_id, snapshot[0], snapshot[1]),
    ).fetchone()
    if approved is None:
        return {"status": "unsupported", "reason": "p1_3c_current_revision_audit_missing"}
    points = conn.execute(
        """SELECT rkp.knowledge_point_id
             FROM question_mapping_source_revision_knowledge_points rkp
             JOIN knowledge_points kp ON kp.id=rkp.knowledge_point_id
             JOIN curriculum_knowledge_points ckp
               ON ckp.knowledge_point_id=kp.id AND ckp.curriculum_node_id=?
            WHERE rkp.source_revision_id=? AND kp.review_status='approved'
            ORDER BY rkp.knowledge_point_id""",
        (curriculum_node_id, revision[0]),
    ).fetchall()
    if not points:
        return {"status": "unsupported", "reason": "p1_3c_current_revision_knowledge_missing"}
    return {
        "status": "pass",
        "source_revision_id": revision[0],
        "source_revision_hash": revision[1],
        "source_hash": revision[2],
        "source_file_path": revision[3],
        "mapping_hash": revision[4],
        "head_revision": revision[5],
        "approval_audit_id": approved[0],
        "approval_audit_manifest_hash": approved[1],
        "approval_audit_method": approved[2],
        "evidence_hash": approved[3],
        "automatic_audit_hash": approved[4],
        "knowledge_points": [row[0] for row in points],
    }


def validate(conn: sqlite3.Connection, question_id: str) -> TextbookScopeResult:
    question = conn.execute(
        "SELECT id, stage FROM questions WHERE id=?", (question_id,)
    ).fetchone()
    if question is None:
        return TextbookScopeResult("fail", {"reason": "题目不存在", "question_id": question_id})

    qt_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(question_textbooks)")
    }
    classifier_run_column = (
        "qt.classifier_run_id" if "classifier_run_id" in qt_columns else "NULL"
    )
    classification_method_column = (
        "qt.classification_method" if "classification_method" in qt_columns else "NULL"
    )
    audit_table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_audits'"
    ).fetchone() is not None
    catalog_audit_select = "catalog_audit.status AS catalog_audit_status" if audit_table_exists else "NULL AS catalog_audit_status"
    catalog_audit_join = """LEFT JOIN catalog_audits catalog_audit
          ON catalog_audit.catalog_release_id = release.id
         AND catalog_audit.import_run_id = catalog_import.id
         AND catalog_audit.status = 'approved'""" if audit_table_exists else ""
    mappings = conn.execute(
        f"""
        SELECT qt.textbook_id, qt.curriculum_node_id, qt.fit_status,
               t.catalog_version AS textbook_catalog_version,
               n.textbook_id AS node_textbook_id, n.stage AS node_stage,
               n.catalog_version AS node_catalog_version, n.status AS node_status,
               release.id AS release_id, release.status AS release_status,
               release.source_reference, release.source_hash,
               catalog_import.status AS catalog_import_status,
               {catalog_audit_select},
               mapping_import.status AS mapping_import_status,
               mapping_import.id AS mapping_import_id,
               qti.mapping_hash,
               {classifier_run_column} AS classifier_run_id,
               {classification_method_column} AS classification_method
        FROM question_textbooks qt
        JOIN textbooks t ON t.id = qt.textbook_id
        LEFT JOIN curriculum_nodes n ON n.id = qt.curriculum_node_id
        LEFT JOIN catalog_releases release
          ON release.textbook_id = qt.textbook_id
         AND release.catalog_version = t.catalog_version
        LEFT JOIN catalog_release_imports cri ON cri.catalog_release_id = release.id
        LEFT JOIN controlled_import_runs catalog_import ON catalog_import.id = cri.import_run_id
        {catalog_audit_join}
        LEFT JOIN question_textbook_imports qti
          ON qti.question_id = qt.question_id
         AND qti.textbook_id = qt.textbook_id
         AND qti.curriculum_node_id = qt.curriculum_node_id
        LEFT JOIN controlled_import_runs mapping_import ON mapping_import.id = qti.import_run_id
        WHERE qt.question_id=?
        ORDER BY qt.textbook_id, qt.curriculum_node_id
        """,
        (question_id,),
    ).fetchall()
    if not mappings:
        return TextbookScopeResult(
            "unsupported", {"reason": "缺少受控教材课程映射", "question_id": question_id}
        )

    passes: list[dict[str, object]] = []
    unsupported: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for mapping in mappings:
        item = {
            "textbook_id": mapping[0],
            "curriculum_node_id": mapping[1],
            "fit_status": mapping[2],
        }
        if mapping[2] == "rejected":
            failures.append({**item, "reason": "教材映射已被拒绝"})
            continue
        if mapping[17] is not None or mapping[18] == "candidate":
            unsupported.append({**item, "reason": "分类器映射仅为候选建议，不能作为教材范围批准依据"})
            continue
        if mapping[2] != "approved":
            unsupported.append({**item, "reason": "教材映射尚未获自动批准"})
            continue
        if mapping[1] is None:
            unsupported.append({**item, "reason": "映射缺少课程节点"})
            continue
        if mapping[4] != mapping[0] or mapping[3] != mapping[6]:
            failures.append({**item, "reason": "教材与课程节点版本不一致"})
            continue
        if mapping[5] != question[1]:
            failures.append({**item, "reason": "题目学段与课程节点学段冲突"})
            continue
        if mapping[7] != "active":
            unsupported.append({**item, "reason": "课程节点未启用"})
            continue
        if mapping[8] is None or mapping[9] != "approved" or not mapping[10] or not mapping[11]:
            unsupported.append({**item, "reason": "缺少已批准且可追溯的教材目录发布记录"})
            continue
        catalog_import_approved = (
            mapping[12] == "validated" and mapping[13] == "approved"
            if audit_table_exists
            else mapping[12] == "approved"
        )
        if not catalog_import_approved:
            unsupported.append({**item, "reason": "\u7f3a\u5c11\u5df2\u6279\u51c6\u4e14\u53ef\u8ffd\u6eaf\u7684\u53d7\u63a7\u5bfc\u5165\u6559\u6750\u76ee\u5f55\u8bb0\u5f55"})
            continue
        revised = _p1_3c_scope_evidence(
            conn,
            question_id=question_id,
            textbook_id=mapping[0],
            curriculum_node_id=mapping[1],
        )
        if revised is not None:
            if revised["status"] != "pass":
                unsupported.append({**item, "reason": str(revised["reason"]), "p1_3c": revised})
                continue
            passes.append(
                {
                    **item,
                    "catalog_release_id": mapping[8],
                    "catalog_version": mapping[3],
                    "source_reference": mapping[10],
                    "mapping_hash": revised["mapping_hash"],
                    "knowledge_points": revised["knowledge_points"],
                    "p1_3c": revised,
                }
            )
            continue
        if mapping[14] != "approved" or not mapping[15] or not mapping[16]:
            unsupported.append({**item, "reason": "\u7f3a\u5c11\u5df2\u6279\u51c6\u4e14\u53ef\u8ffd\u6eaf\u7684\u53d7\u63a7\u5bfc\u5165\u6559\u6750\u76ee\u5f55\u8bb0\u5f55"})
            continue
        knowledge_points = conn.execute(
            """
            SELECT qkp.knowledge_point_id, qkp.relation_type
            FROM question_knowledge_points qkp
            JOIN question_knowledge_point_imports qkpi
              ON qkpi.question_id = qkp.question_id
             AND qkpi.knowledge_point_id = qkp.knowledge_point_id
             AND qkpi.import_run_id = ?
             AND qkpi.mapping_hash = ?
            JOIN curriculum_knowledge_points ckp
              ON ckp.knowledge_point_id = qkp.knowledge_point_id
             AND ckp.curriculum_node_id = ?
            JOIN knowledge_points kp ON kp.id = qkp.knowledge_point_id
            WHERE qkp.question_id=? AND kp.review_status='approved'
            ORDER BY qkp.knowledge_point_id
            """,
            (mapping[15], mapping[16], mapping[1], question_id),
        ).fetchall()
        if not knowledge_points:
            unsupported.append({**item, "reason": "缺少与课程节点一致的已批准知识点映射"})
            continue
        passes.append(
            {
                **item,
                "catalog_release_id": mapping[8],
                "catalog_version": mapping[3],
                "source_reference": mapping[10],
                "mapping_hash": mapping[16],
                "knowledge_points": [row[0] for row in knowledge_points],
            }
        )

    evidence = {"question_id": question_id, "passes": passes, "unsupported": unsupported, "failures": failures}
    if failures:
        return TextbookScopeResult("fail", evidence)
    if passes:
        return TextbookScopeResult("pass", evidence)
    return TextbookScopeResult("unsupported", evidence)
