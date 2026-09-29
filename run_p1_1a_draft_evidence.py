"""P1-1a: controlled, draft-only triangle-theme scope evidence exercise.

This runner deliberately writes only v2.21 source bindings and v2.22 draft
mapping evidence. It never updates questions, mappings, verification records,
approval state, selection, documents, delivery, or usage history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from curriculum_progress_scope import resolve_trusted_progress_scope
from question_curriculum_mapping_evidence import MappingEvidence, record_mapping_evidence
from question_scope_features import extract_question_scope_features

RUNNER_ID = "p1-1a-triangle-theme-draft"
RUNNER_VERSION = "1.1.0"
TEXTBOOK_ID = "bsd-math-grade8-lower-2026-spring-extsrc"
PROGRESS_LABEL = "第二章"
SOURCE_DOCUMENT_ID = "golden-source-001"
ORIGINAL_PATH = "<local-scratch>/2026-07-15初中数学合格性考试-自定义类型 (1).docx"
ARCHIVE_PATH = "data/history/source-documents/2026-07-15初中数学合格性考试-自定义类型 (1).docx"

SQLITE_OK = 0
SQLITE_DENY = 1
SQLITE_IGNORE = 2
SQLITE_INSERT = 18
SQLITE_UPDATE = 23
SQLITE_TRANSACTION = 22
SQLITE_READ = 20
SQLITE_SELECT = 21
SQLITE_FUNCTION = 31
SQLITE_PRAGMA = 19
SQLITE_SAVEPOINT = 32

ALLOWED_SOURCE_DOCUMENT_COLUMNS = frozenset(
    ("original_relative_path", "original_file_hash", "trusted_source", "intake_manifest_json")
)
PROTECTED_TABLES = (
    "questions",
    "question_textbooks",
    "question_knowledge_points",
    "question_verifications",
    "teaching_documents",
    "quality_reports",
    "question_usage",
)

# Each rule is a transparent, deterministic fail-closed signature. A match says
# the question has an explicit dependency which this P1-1a exercise must not
# silently downgrade to an in-progress triangle-only problem.
EXPLICIT_FUTURE_OR_NON_THEME = (
    ("正方形", "explicit_square_dependency"),
    ("平行四边形", "explicit_parallelogram_dependency"),
    ("圆心", "explicit_circle_center_dependency"),
    ("半径", "explicit_circle_radius_dependency"),
    ("圆", "explicit_circle_dependency"),
    ("⊙", "explicit_circle_symbol_dependency"),
    ("相似", "explicit_similarity_dependency"),
    ("三角函数", "explicit_trigonometry_dependency"),
    ("正弦", "explicit_trigonometry_dependency"),
    ("余弦", "explicit_trigonometry_dependency"),
    ("正切", "explicit_trigonometry_dependency"),
    ("sin", "explicit_trigonometry_dependency"),
    ("cos", "explicit_trigonometry_dependency"),
    ("tan", "explicit_trigonometry_dependency"),
    ("正方体", "explicit_cube_dependency"),
    ("勾股", "explicit_pythagorean_dependency"),
)

# These are the only present-book nodes whose names can be claimed from an
# exact question-text anchor. All other triangle content remains unsupported:
# no keyword, image, or broad semantic guess is upgraded to candidate.
THEME_NODE_REQUIREMENTS = (
    {
        "anchor": "三角形内角和定理",
        "node_id": "bsd-math-8x-2026-node-01-section-01",
        "core_theme": "三角形内角和",
        "required_knowledge": (
            "识别三角形的三个内角",
            "应用三角形内角和为180°",
        ),
        "required_patterns": {
            "识别三角形的三个内角": ("三角形", "内角"),
            "应用三角形内角和为180°": ("内角和", "180"),
        },
    },
    {
        "anchor": "等腰三角形",
        "node_id": "bsd-math-8x-2026-node-01-section-02",
        "core_theme": "等腰三角形性质",
        "required_knowledge": (
            "识别等腰三角形的已知条件",
            "使用等腰三角形两底角相等或两腰相等",
        ),
        "required_patterns": {
            "识别等腰三角形的已知条件": ("等腰三角形",),
            "使用等腰三角形两底角相等或两腰相等": ("底角", "相等"),
        },
    },
    {
        "anchor": "直角三角形",
        "node_id": "bsd-math-8x-2026-node-01-section-03",
        "core_theme": "直角三角形基础性质",
        "required_knowledge": (
            "识别直角三角形或90°条件",
            "仅使用当前进度内的直角三角形基础性质",
        ),
        "required_patterns": {
            "识别直角三角形或90°条件": ("直角三角形", "90°", "90度"),
            "仅使用当前进度内的直角三角形基础性质": ("直角三角形",),
        },
    },
    {
        "anchor": "线段的垂直平分线",
        "node_id": "bsd-math-8x-2026-node-01-section-04",
        "core_theme": "线段垂直平分线性质",
        "required_knowledge": (
            "识别垂直平分线定义",
            "使用到线段两端点距离相等性质",
        ),
        "required_patterns": {
            "识别垂直平分线定义": ("垂直平分线",),
            "使用到线段两端点距离相等性质": ("距离相等", "两端点"),
        },
    },
    {
        "anchor": "角平分线",
        "node_id": "bsd-math-8x-2026-node-01-section-05",
        "core_theme": "角平分线性质",
        "required_knowledge": (
            "识别角平分线定义",
            "使用角平分线上的点到角两边距离相等性质",
        ),
        "required_patterns": {
            "识别角平分线定义": ("角平分线",),
            "使用角平分线上的点到角两边距离相等性质": ("距离相等", "两边"),
        },
    },
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def capture_db_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    approved_count = conn.execute(
        "SELECT COUNT(*) FROM questions WHERE quality_status='approved' OR review_status='approved'"
    ).fetchone()[0]
    snapshot: dict[str, Any] = {
        "questions": tuple(conn.execute(
            "SELECT id, quality_status, review_status, content_hash, source_document_id FROM questions ORDER BY id"
        ).fetchall()),
        "question_textbooks": tuple(conn.execute(
            "SELECT question_id, textbook_id, curriculum_node_id, fit_status FROM question_textbooks ORDER BY question_id, textbook_id, curriculum_node_id"
        ).fetchall()),
        "question_knowledge_points": tuple(conn.execute(
            "SELECT question_id, knowledge_point_id, relation_type FROM question_knowledge_points ORDER BY question_id, knowledge_point_id"
        ).fetchall()),
        "question_verifications": tuple(conn.execute(
            "SELECT question_id, verification_type, status, input_hash FROM question_verifications ORDER BY question_id, verification_type, verified_at, rowid"
        ).fetchall()),
        "approved_count": approved_count,
        "teaching_documents": tuple(conn.execute(
            "SELECT id, request_id, selection_plan_id, status, output_path, content_hash FROM teaching_documents ORDER BY id"
        ).fetchall()),
        "quality_reports": tuple(conn.execute(
            "SELECT id, document_id, data_gate, rule_gate, fidelity_gate, artifact_gate, status FROM quality_reports ORDER BY id"
        ).fetchall()),
        "question_usage": tuple(conn.execute(
            "SELECT id, question_id, class_id, document_id, delivered, reuse_allowed FROM question_usage ORDER BY id"
        ).fetchall()),
    }
    return snapshot


def assert_protected_snapshot_unchanged(before: dict[str, Any], after: dict[str, Any]) -> None:
    for key in PROTECTED_TABLES:
        if before[key] != after[key]:
            raise RuntimeError(f"protected_table_changed:{key}")
    for key in ("question_textbooks", "question_knowledge_points", "question_verifications", "questions", "teaching_documents", "quality_reports", "question_usage"):
        if before[key] != after[key]:
            raise RuntimeError(f"protected_snapshot_changed:{key}")
    if before["approved_count"] != after["approved_count"]:
        raise RuntimeError("approved_count_changed")


def _authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, trigger_name: str | None) -> int:
    if action == SQLITE_INSERT:
        if arg1 == "question_curriculum_mapping_evidence":
            return SQLITE_OK
        return SQLITE_DENY
    if action == SQLITE_UPDATE:
        if arg1 == "source_documents" and arg2 in ALLOWED_SOURCE_DOCUMENT_COLUMNS:
            return SQLITE_OK
        return SQLITE_DENY
    if action in (SQLITE_READ, SQLITE_SELECT, SQLITE_TRANSACTION, SQLITE_FUNCTION, SQLITE_PRAGMA, SQLITE_SAVEPOINT):
        return SQLITE_OK
    return SQLITE_DENY


@contextmanager
def guarded_connection(db_path: Path) -> Iterable[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.set_authorizer(_authorizer)
    try:
        yield conn
    finally:
        conn.close()


def bind_trusted_source(conn: sqlite3.Connection, root: Path) -> dict[str, Any]:
    """Bind original, history archive, and extractor copy with explicit manifest."""
    original = root / ORIGINAL_PATH
    archive = root / ARCHIVE_PATH
    if not original.is_file() or not archive.is_file():
        raise RuntimeError("trusted_original_or_archive_missing")
    original_hash = sha256_file(original)
    archive_hash = sha256_file(archive)
    if original_hash != archive_hash:
        raise RuntimeError("trusted_original_archive_hash_mismatch")
    row = conn.execute(
        "SELECT relative_path, file_hash FROM source_documents WHERE id=?", (SOURCE_DOCUMENT_ID,)
    ).fetchone()
    if row is None:
        raise RuntimeError("source_document_not_found")
    extractor_relative_path = str(row[0])
    extractor_hash = str(row[1])
    if extractor_hash != archive_hash:
        raise RuntimeError("database_archive_hash_does_not_match_trusted_archive")
    manifest = {
        "schema": "p1-1a-source-binding-v2",
        "original": {"relative_path": ORIGINAL_PATH, "sha256": original_hash},
        "history_archive": {"relative_path": ARCHIVE_PATH, "sha256": archive_hash},
        "extractor_copy": {"relative_path": extractor_relative_path, "sha256": extractor_hash},
        "hashes_match": original_hash == archive_hash == extractor_hash,
        "trusted_source": True,
        "approved": False,
        "purpose": "draft_mapping_evidence_only",
    }
    if not manifest["hashes_match"]:
        raise RuntimeError("trusted_source_hash_triplet_mismatch")
    conn.execute(
        """UPDATE source_documents SET original_relative_path=?, original_file_hash=?,
           trusted_source=1, intake_manifest_json=? WHERE id=?""",
        (ORIGINAL_PATH, original_hash, _json(manifest), SOURCE_DOCUMENT_ID),
    )
    return manifest


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def decide(question: sqlite3.Row, allowed_node_ids: set[str]) -> tuple[str, str, float, str, dict[str, Any]]:
    text = "\n".join(str(question[key] or "") for key in ("stem", "options_json", "answer", "analysis"))
    features = extract_question_scope_features(
        stem=question["stem"],
        options=question["options_json"],
        answer=question["answer"],
        analysis=question["analysis"],
        question_type=question["question_type"],
    )
    base_features: dict[str, Any] = {
        "exercise": "P1-1a",
        "task_theme": "三角形",
        "progress_upper_bound": "北师大版八年级下册至第二章",
        "feature_status": features.status,
        "feature_reason": features.reason,
        "feature_input_hash": features.input_hash,
        "candidate_features": list(features.candidate_features),
    }
    future_hits = [code for token, code in EXPLICIT_FUTURE_OR_NON_THEME if token in text]
    if future_hits:
        base_features["explicit_dependency_hits"] = sorted(set(future_hits))
        base_features["core_theme"] = None
        base_features["required_knowledge"] = []
        base_features["required_knowledge_evidence"] = {}
        return (
            "rejected",
            "bsd-math-8x-2026-node-01",
            1.0,
            "明确出现后续或非主题依赖：" + ",".join(sorted(set(future_hits))),
            base_features,
        )
    for rule in THEME_NODE_REQUIREMENTS:
        if rule["anchor"] not in text:
            continue
        node_id = str(rule["node_id"])
        required_knowledge = list(rule["required_knowledge"])
        evidence = {
            item: {
                "supported": _contains_any(text, tuple(rule["required_patterns"][item])),
                "patterns": list(rule["required_patterns"][item]),
            }
            for item in required_knowledge
        }
        base_features["core_theme"] = rule["core_theme"]
        base_features["required_knowledge"] = required_knowledge
        base_features["required_knowledge_evidence"] = evidence
        base_features["exact_theme_anchor"] = rule["anchor"]
        if node_id not in allowed_node_ids:
            return (
                "unsupported",
                node_id,
                0.0,
                "锚点节点超出当前教材进度，不能形成候选草案",
                base_features,
            )
        if features.status == "unsupported":
            return (
                "unsupported",
                node_id,
                0.0,
                "题目基础特征不足，无法自动证明核心主题与必需知识",
                base_features,
            )
        if not all(item["supported"] for item in evidence.values()):
            return (
                "unsupported",
                node_id,
                0.0,
                "无法自动证明全部必需知识已学并且在题目中实际被使用",
                base_features,
            )
        return (
            "candidate",
            node_id,
            0.8,
            "唯一当前教材节点精确锚点且全部必需知识证据齐备；仍仅为草案",
            base_features,
        )
    base_features["core_theme"] = None
    base_features["required_knowledge"] = []
    base_features["required_knowledge_evidence"] = {}
    return (
        "unsupported",
        "bsd-math-8x-2026-node-01",
        0.0,
        "无法自动证明核心主题及全部必需知识均属于截至当前进度的已学范围",
        base_features,
    )


def build_report(binding: dict[str, Any], progress: Any, records: list[dict[str, Any]], before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "exercise": "P1-1a",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "write_scope": [
            "UPDATE source_documents.original_relative_path",
            "UPDATE source_documents.original_file_hash",
            "UPDATE source_documents.trusted_source",
            "UPDATE source_documents.intake_manifest_json",
            "INSERT question_curriculum_mapping_evidence",
        ],
        "prohibited_writes": [
            "questions",
            "question_textbooks",
            "question_knowledge_points",
            "question_verifications",
            "approval",
            "selection",
            "teaching_documents",
            "quality_reports",
            "delivery",
            "question_usage",
        ],
        "input": {
            "textbook_id": TEXTBOOK_ID,
            "progress_label": PROGRESS_LABEL,
            "task_theme": "三角形",
            "source_document_id": SOURCE_DOCUMENT_ID,
            "source_binding": binding,
            "progress_scope": {
                "catalog_version": progress.catalog_version,
                "current_node_id": progress.current_node_id,
                "allowed_node_ids": list(progress.allowed_node_ids),
                "outside_node_ids": list(progress.outside_node_ids),
            },
        },
        "summary": {
            "question_count": len(records),
            "statuses": dict(Counter(item["status"] for item in records)),
            "approved_count": after["approved_count"],
            "teaching_documents_count": len(after["teaching_documents"]),
            "quality_reports_count": len(after["quality_reports"]),
            "question_usage_count": len(after["question_usage"]),
            "note": "candidate is draft-only and cannot create a question mapping or approval",
        },
        "protected_snapshot_unchanged": before == after,
        "questions": records,
    }


def _write_report_atomic(output_path: Path, report: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_path.parent, delete=False) as handle:
        temp_path = Path(handle.name)
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp_path.replace(output_path)


def run(db_path: Path, output_path: Path) -> dict[str, Any]:
    root = Path(__file__).resolve().parent
    with guarded_connection(db_path) as conn:
        before_snapshot = capture_db_snapshot(conn)
        try:
            conn.execute("BEGIN IMMEDIATE")
            binding = bind_trusted_source(conn, root)
            progress = resolve_trusted_progress_scope(
                conn, textbook_reference=TEXTBOOK_ID, progress_label=PROGRESS_LABEL
            )
            if progress.status != "resolved":
                raise RuntimeError("progress_scope_not_resolved:" + ",".join(progress.reasons))
            allowed = set(progress.allowed_node_ids)
            questions = conn.execute(
                """SELECT id, source_question_no, stem, options_json, answer, analysis, question_type
                   FROM questions WHERE source_document_id=?
                   ORDER BY CAST(source_question_no AS INTEGER), source_question_no, id""",
                (SOURCE_DOCUMENT_ID,),
            ).fetchall()
            if len(questions) != 20:
                raise RuntimeError(f"expected_20_questions_got_{len(questions)}")
            records: list[dict[str, Any]] = []
            for question in questions:
                status, node_id, confidence, reason, features = decide(question, allowed)
                evidence_id = record_mapping_evidence(
                    conn,
                    MappingEvidence(
                        question_id=question["id"],
                        textbook_id=TEXTBOOK_ID,
                        curriculum_node_id=node_id,
                        features=features,
                        decider_id=RUNNER_ID,
                        decider_version=RUNNER_VERSION,
                        status=status,
                        confidence=confidence,
                        reason=reason,
                    ),
                )
                records.append(
                    {
                        "question_id": question["id"],
                        "source_question_no": question["source_question_no"],
                        "status": status,
                        "curriculum_node_id": node_id,
                        "confidence": confidence,
                        "reason": reason,
                        "evidence_id": evidence_id,
                        "features": features,
                    }
                )
            after_snapshot = capture_db_snapshot(conn)
            assert_protected_snapshot_unchanged(before_snapshot, after_snapshot)
            if after_snapshot["approved_count"] != 0:
                raise RuntimeError("approved_count_must_remain_zero")
            if after_snapshot["teaching_documents"] or after_snapshot["quality_reports"] or after_snapshot["question_usage"]:
                raise RuntimeError("documents_reports_usage_must_remain_zero")
            report = build_report(binding, progress, records, before_snapshot, after_snapshot)
            _write_report_atomic(output_path, report)
            conn.commit()
            return report
        except Exception:
            conn.rollback()
            raise


def main() -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=root / "data" / "dev" / "teaching_docs_dev.db")
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "output" / "audits" / "p1-1a_triangle_bsd8x_chapter2_draft_evidence.json",
    )
    args = parser.parse_args()
    report = run(args.db, args.output)
    print(json.dumps({"output": str(args.output), "summary": report["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
