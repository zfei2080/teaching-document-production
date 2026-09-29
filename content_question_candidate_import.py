"""Persist deterministic source-derived question candidates with isolated solutions."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
from typing import Any

from content_library_extraction import _require_current_source_version
from content_question_validation_contract import CONTRACT_ID, CONTRACT_VERSION, QuestionValidationAssessment, assess_candidate
from source_content_question_segmentation import FieldEvidence, QuestionCandidate, QuestionSegmentationError, segment_questions
from source_library_intake import _git_revision, _insert_event, _stable_id, canonical, digest_text

SCHEMA = "content-library-question-candidate-import-v2"
IMPORTER_ID = "content-library-question-candidate-import"
IMPORTER_VERSION = "2.0.0"


class ContentQuestionCandidateImportError(RuntimeError):
    """Raised when source-derived question candidates cannot be stored safely."""


class AssetOptionAssociationError(ValueError):
    """Raised when an image-only option cannot be bound to one exact source asset."""


@dataclass(frozen=True)
class CandidateImportResult:
    source_version_id: str
    import_run_id: str | None
    status: str
    questions: int
    content_items: int
    internal_answer_evidence: int
    internal_analysis_evidence: int
    source_asset_evidence: int
    change_events_created: int

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "source_version_id": self.source_version_id,
            "import_run_id": self.import_run_id,
            "status": self.status,
            "questions": self.questions,
            "content_items": self.content_items,
            "internal_answer_evidence": self.internal_answer_evidence,
            "internal_analysis_evidence": self.internal_analysis_evidence,
            "source_asset_evidence": self.source_asset_evidence,
            "change_events_created": self.change_events_created,
        }


def _run_id(extraction_run_id: str) -> str:
    return _stable_id("content-import-run", {
        "mode": "extract", "extraction_run_id": extraction_run_id,
        "importer_id": IMPORTER_ID, "importer_version": IMPORTER_VERSION,
    })


def _candidate_payload(candidate: QuestionCandidate, *, source_question_key: str, options: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "source_question_no": candidate.source_question_no,
        "source_question_key": source_question_key,
        "question_type": candidate.question_type,
        "stem": candidate.stem,
        "options": list(candidate.options) if options is None else options,
        "source_block_ids": list(candidate.source_block_ids),
    }


def _source_question_keys(candidates: list[QuestionCandidate]) -> list[str]:
    """Create deterministic source locators when Word restarts numbering per section."""
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.source_question_no] = counts.get(candidate.source_question_no, 0) + 1
    return [
        candidate.source_question_no if counts[candidate.source_question_no] == 1
        else f"{candidate.question_type}:{candidate.source_question_no}"
        for candidate in candidates
    ]


def _internal_payload(evidence: FieldEvidence, *, marker: str) -> str:
    text = evidence.text
    if text.startswith(marker):
        return text[len(marker):].replace("\x01", "").replace("\x07", "").strip()
    return text.replace("\x01", "").replace("\x07", "").strip()


def _require_extracted_blocks(
    connection: sqlite3.Connection, *, source_version_id: str
) -> tuple[str, str, list[dict[str, object]], list[dict[str, object]]]:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """SELECT er.id AS extraction_run_id, v.source_document_id
             FROM content_extraction_runs er
             JOIN content_source_versions v ON v.id=er.source_version_id
             JOIN content_import_runs ir ON ir.id=er.import_run_id
            WHERE er.source_version_id=? AND ir.importer_id='content-library-word-extraction'
            ORDER BY er.created_at DESC, er.id DESC LIMIT 1""",
        (source_version_id,),
    ).fetchone()
    if row is None:
        raise ContentQuestionCandidateImportError("word_source_blocks_not_extracted")
    blocks = [
        {"id": item["id"], "ordinal": item["ordinal"], "raw_text": item["raw_text"],
         "locator": json.loads(item["locator_json"])}
        for item in connection.execute(
            "SELECT id,ordinal,raw_text,locator_json FROM source_content_blocks WHERE extraction_run_id=? ORDER BY ordinal",
            (row["extraction_run_id"],),
        ).fetchall()
    ]
    if not blocks:
        raise ContentQuestionCandidateImportError("word_source_blocks_missing")
    assets = [
        {"id": item["id"], "ordinal": item["ordinal"], "asset_kind": item["asset_kind"],
         "locator": json.loads(item["locator_json"])}
        for item in connection.execute(
            "SELECT id,ordinal,asset_kind,locator_json FROM source_content_assets WHERE extraction_run_id=? ORDER BY ordinal",
            (row["extraction_run_id"],),
        ).fetchall()
    ]
    return row["extraction_run_id"], row["source_document_id"], blocks, assets


@dataclass(frozen=True)
class AssetOptionBinding:
    option_label: str
    position: int
    source_asset_id: str
    source_block_id: str
    association_mode: str
    evidence_sha256: str


def _word_range(locator: object, *, reason: str) -> tuple[int, int]:
    if not isinstance(locator, dict):
        raise AssetOptionAssociationError(reason)
    start, end = locator.get("range_start"), locator.get("range_end")
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        raise AssetOptionAssociationError(reason)
    return start, end


def _asset_option_payload_and_bindings(
    candidate: QuestionCandidate, *, blocks: list[dict[str, object]], assets: list[dict[str, object]],
) -> tuple[list[dict[str, object]], tuple[AssetOptionBinding, ...]]:
    """Bind marker-only options to source assets, preserving strict range evidence first."""
    options = [dict(option) for option in candidate.options]
    asset_option_positions = [index for index, option in enumerate(options) if option.get("asset_only") is True]
    if not asset_option_positions:
        return options, tuple()
    if len(asset_option_positions) != len(options) or len(candidate.option_evidence) != len(options):
        raise AssetOptionAssociationError("asset_only_option_set_ambiguous")
    blocks_by_id = {str(block["id"]): block for block in blocks}
    asset_ranges: list[tuple[dict[str, object], int, int]] = []
    for asset in assets:
        try:
            asset_start, asset_end = _word_range(asset.get("locator"), reason="asset_only_option_asset_range_invalid")
        except AssetOptionAssociationError:
            continue
        asset_ranges.append((asset, asset_start, asset_end))
    by_block: dict[str, list[int]] = {}
    for option_index in asset_option_positions:
        by_block.setdefault(candidate.option_evidence[option_index].block_id, []).append(option_index)
    assignments: dict[int, tuple[dict[str, object], str]] = {}
    consumed_assets: set[str] = set()
    for block_id, option_indexes in by_block.items():
        block = blocks_by_id.get(block_id)
        if block is None:
            raise AssetOptionAssociationError("asset_only_option_block_missing")
        block_start, block_end = _word_range(block.get("locator"), reason="asset_only_option_block_range_invalid")
        strict: dict[int, list[dict[str, object]]] = {}
        for option_index in option_indexes:
            evidence = candidate.option_evidence[option_index]
            evidence_start, evidence_end = block_start + evidence.start, block_start + evidence.end
            strict[option_index] = [
                asset for asset, asset_start, asset_end in asset_ranges
                if str(asset["id"]) not in consumed_assets and asset_start >= evidence_start and asset_end <= evidence_end
            ]
        if all(len(matches) == 1 for matches in strict.values()) and len({str(matches[0]["id"]) for matches in strict.values()}) == len(option_indexes):
            for option_index, matches in strict.items():
                assignments[option_index] = (matches[0], "exact_marker_range")
                consumed_assets.add(str(matches[0]["id"]))
            continue
        # Word may report a rendered inline object as a wider range than its one-character marker.
        # This fallback remains fail-closed: one source block, exact count, and independent Word ordering.
        block_assets = [
            (asset, asset_start, asset_end) for asset, asset_start, asset_end in asset_ranges
            if str(asset["id"]) not in consumed_assets and asset_start >= block_start and asset_end <= block_end
        ]
        if len(block_assets) != len(option_indexes):
            raise AssetOptionAssociationError("asset_only_option_asset_count_mismatch")
        ordered_options = sorted(option_indexes, key=lambda index: candidate.option_evidence[index].start)
        ordered_assets = sorted(block_assets, key=lambda value: (value[1], value[2], int(value[0]["ordinal"]), str(value[0]["id"])))
        for option_index, (asset, _, _) in zip(ordered_options, ordered_assets, strict=True):
            assignments[option_index] = (asset, "block_range_order")
            consumed_assets.add(str(asset["id"]))
    bindings: list[AssetOptionBinding] = []
    for option_index, option in enumerate(options):
        asset, association_mode = assignments[option_index]
        evidence = candidate.option_evidence[option_index]
        asset_id = str(asset["id"])
        label = option.get("label")
        if not isinstance(label, str) or not label:
            raise AssetOptionAssociationError("asset_only_option_label_invalid")
        option["text"] = None
        option["asset_only"] = True
        option["source_asset_ids"] = [asset_id]
        evidence_sha256 = digest_text(canonical({
            "option_label": label, "source_block_id": evidence.block_id,
            "character_range": [evidence.start, evidence.end], "source_asset_id": asset_id,
            "association_mode": association_mode,
        }))
        bindings.append(AssetOptionBinding(
            option_label=label, position=0, source_asset_id=asset_id,
            source_block_id=evidence.block_id, association_mode=association_mode,
            evidence_sha256=evidence_sha256,
        ))
    return options, tuple(bindings)


def _insert_question_source_asset_evidence(
    connection: sqlite3.Connection, *, question_id: str, binding: AssetOptionBinding,
    run_id: str, transaction_id: str, revision: str | None, actor: str,
) -> int:
    record_id = _stable_id("question-source-asset-evidence", {
        "question_id": question_id, "source_asset_id": binding.source_asset_id,
        "source_block_id": binding.source_block_id, "option_label": binding.option_label,
        "position": binding.position, "association_mode": binding.association_mode,
        "evidence_sha256": binding.evidence_sha256,
    })
    event_id, created = _insert_event(
        connection, event_type="content_created", entity_type="question_source_asset_evidence", entity_id=record_id,
        operation="create", reason="bind image-only question option to exact Word source asset", actor=actor,
        import_run_id=run_id, transaction_id=transaction_id,
        after_state={"question_id": question_id, "source_content_asset_id": binding.source_asset_id,
                     "source_block_id": binding.source_block_id, "option_label": binding.option_label,
                     "position": binding.position, "association_mode": binding.association_mode}, after_hash=binding.evidence_sha256,
        git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
    )
    connection.execute(
        """INSERT INTO question_source_asset_evidence(
            id,question_id,source_content_asset_id,source_block_id,option_label,position,
            evidence_sha256,created_change_event_id
        ) VALUES(?,?,?,?,?,?,?,?)""",
        (record_id, question_id, binding.source_asset_id, binding.source_block_id,
         binding.option_label, binding.position, binding.evidence_sha256, event_id),
    )
    return int(created)


def _existing_result(
    connection: sqlite3.Connection, *, source_version_id: str, extraction_run_id: str
) -> CandidateImportResult | None:
    run_id = _run_id(extraction_run_id)
    if connection.execute("SELECT 1 FROM content_import_runs WHERE id=?", (run_id,)).fetchone() is None:
        return None
    question_count = connection.execute(
        """SELECT COUNT(*) FROM content_item_question_links link
             JOIN content_items item ON item.id=link.content_item_id
            WHERE item.source_version_id=?""", (source_version_id,)
    ).fetchone()[0]
    evidence_counts = connection.execute(
        """SELECT field_name,COUNT(*) FROM question_internal_evidence e
             JOIN questions q ON q.id=e.question_id
             JOIN content_item_question_links link ON link.question_id=q.id
             JOIN content_items item ON item.id=link.content_item_id
            WHERE item.source_version_id=? GROUP BY field_name""", (source_version_id,)
    ).fetchall()
    source_asset_evidence = connection.execute(
        """SELECT COUNT(*) FROM question_source_asset_evidence evidence
             JOIN content_item_question_links link ON link.question_id=evidence.question_id
             JOIN content_items item ON item.id=link.content_item_id
            WHERE item.source_version_id=?""", (source_version_id,)
    ).fetchone()[0]
    by_field = dict(evidence_counts)
    return CandidateImportResult(
        source_version_id=source_version_id, import_run_id=run_id, status="already_segmented",
        questions=question_count, content_items=question_count,
        internal_answer_evidence=by_field.get("answer", 0), internal_analysis_evidence=by_field.get("analysis", 0),
        source_asset_evidence=source_asset_evidence, change_events_created=0,
    )


def _create_run(
    connection: sqlite3.Connection, *, extraction_run_id: str, source_version_id: str, workspace: Path, actor: str
) -> tuple[str, str, int]:
    run_id = _run_id(extraction_run_id)
    selection = {"schema": SCHEMA, "source_version_id": source_version_id, "extraction_run_id": extraction_run_id}
    selection_hash = digest_text(canonical(selection))
    transaction_id = _stable_id("transaction", {"run_id": run_id, "operation": "question_candidate_import"})
    revision = _git_revision(workspace)
    event_id, created = _insert_event(
        connection, event_type="import_run_started", entity_type="content_import_runs", entity_id=run_id,
        operation="create", reason="create explicit source-derived question candidate import", actor=actor,
        import_run_id=None, transaction_id=transaction_id, after_state=selection, after_hash=selection_hash,
        git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
    )
    connection.execute(
        """INSERT INTO content_import_runs(
            id,mode,selection_json,selection_sha256,importer_id,importer_version,created_change_event_id
        ) VALUES(?,?,?,?,?,?,?)""",
        (run_id, "extract", canonical(selection), selection_hash, IMPORTER_ID, IMPORTER_VERSION, event_id),
    )
    start_id = f"{run_id}:started"
    start_event_id, start_created = _insert_event(
        connection, event_type="import_run_started", entity_type="content_import_run_events", entity_id=start_id,
        operation="status_change", reason="start source-derived question candidate import", actor=actor,
        import_run_id=run_id, transaction_id=transaction_id, after_state={"status": "started"},
        git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
    )
    connection.execute(
        "INSERT INTO content_import_run_events(id,import_run_id,status,totals_json,change_event_id) VALUES(?,?,?,?,?)",
        (start_id, run_id, "started", "{}", start_event_id),
    )
    return run_id, transaction_id, int(created) + int(start_created)


def _insert_field_evidence(
    connection: sqlite3.Connection, *, content_item_id: str, field_name: str, visibility: str,
    evidence: FieldEvidence, run_id: str, transaction_id: str, revision: str | None, actor: str,
) -> int:
    if evidence.end <= evidence.start:
        raise ContentQuestionCandidateImportError("source_field_evidence_range_invalid")
    evidence_hash = digest_text(evidence.text)
    evidence_id = _stable_id("content-item-evidence", {
        "item": content_item_id, "field": field_name, "visibility": visibility,
        "block": evidence.block_id, "range": [evidence.start, evidence.end], "hash": evidence_hash,
    })
    event_id, created = _insert_event(
        connection, event_type="content_created", entity_type="content_item_evidence", entity_id=evidence_id,
        operation="create", reason="bind source-derived content field to exact source block range", actor=actor,
        import_run_id=run_id, transaction_id=transaction_id,
        after_state={"field_name": field_name, "visibility": visibility, "source_block_id": evidence.block_id,
                     "character_range": [evidence.start, evidence.end]}, after_hash=evidence_hash,
        git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
    )
    connection.execute(
        """INSERT INTO content_item_evidence(
            id,content_item_id,field_name,visibility,source_block_id,character_range_json,evidence_text,
            evidence_sha256,created_change_event_id
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (evidence_id, content_item_id, field_name, visibility, evidence.block_id,
         canonical([evidence.start, evidence.end]), evidence.text, evidence_hash, event_id),
    )
    return int(created)


def _insert_internal_evidence(
    connection: sqlite3.Connection, *, question_id: str, field_name: str, evidence: FieldEvidence,
    run_id: str, transaction_id: str, revision: str | None, actor: str,
) -> int:
    marker = "\u3010\u7b54\u6848\u3011" if field_name == "answer" else "\u3010\u89e3\u6790\u3011"
    payload = _internal_payload(evidence, marker=marker)
    if not payload:
        return 0
    evidence_hash = digest_text(evidence.text)
    record_id = _stable_id("question-internal-evidence", {
        "question": question_id, "field": field_name, "block": evidence.block_id,
        "range": [evidence.start, evidence.end], "hash": evidence_hash,
    })
    event_id, created = _insert_event(
        connection, event_type="content_created", entity_type="question_internal_evidence", entity_id=record_id,
        operation="create", reason="persist internal-only source-derived answer or solution evidence", actor=actor,
        import_run_id=run_id, transaction_id=transaction_id,
        after_state={"field_name": field_name, "source_block_id": evidence.block_id,
                     "character_range": [evidence.start, evidence.end]}, after_hash=evidence_hash,
        git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
    )
    connection.execute(
        """INSERT INTO question_internal_evidence(
            id,question_id,field_name,source_block_id,character_range_json,internal_payload,evidence_sha256,
            created_change_event_id
        ) VALUES(?,?,?,?,?,?,?,?)""",
        (record_id, question_id, field_name, evidence.block_id, canonical([evidence.start, evidence.end]),
         payload, evidence_hash, event_id),
    )
    return int(created)


def _insert_math_validation_evidence(
    connection: sqlite3.Connection, *, content_item_id: str, question_id: str, content_sha256: str,
    assessment: QuestionValidationAssessment, run_id: str, transaction_id: str,
    revision: str | None, actor: str,
) -> int:
    validation = assessment.mathematical_validation
    evidence_json = canonical(validation.evidence)
    evidence_hash = digest_text(evidence_json)
    record_id = _stable_id("content-item-math-validation", {
        "content_item_id": content_item_id, "contract_id": CONTRACT_ID,
        "validator_id": validation.validator_id, "validator_version": CONTRACT_VERSION,
        "input_sha256": assessment.input_sha256,
    })
    event_id, created = _insert_event(
        connection, event_type="content_created", entity_type="content_item_math_validation_evidence",
        entity_id=record_id, operation="create",
        reason="persist independent mathematical validation result for source-derived question",
        actor=actor, import_run_id=run_id, transaction_id=transaction_id,
        after_state={"validation_status": validation.status, "validator_id": validation.validator_id,
                     "validator_version": CONTRACT_VERSION, "content_sha256": content_sha256,
                     "source_answer_present": validation.source_answer is not None},
        after_hash=evidence_hash, git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
    )
    connection.execute(
        """INSERT INTO content_item_math_validation_evidence(
            id,content_item_id,question_id,validation_contract_id,validator_id,validator_version,
            validation_status,computed_answer,source_answer,content_sha256,input_sha256,evidence_json,
            evidence_sha256,created_change_event_id
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (record_id, content_item_id, question_id, CONTRACT_ID, validation.validator_id, CONTRACT_VERSION,
         validation.status, validation.computed_answer, validation.source_answer, content_sha256,
         assessment.input_sha256, evidence_json, evidence_hash, event_id),
    )
    return int(created)


def _insert_knowledge_mapping_evidence(
    connection: sqlite3.Connection, *, content_item_id: str, assessment: QuestionValidationAssessment,
    run_id: str, transaction_id: str, revision: str | None, actor: str,
) -> int:
    created_count = 0
    validation = assessment.mathematical_validation
    for mapping in assessment.knowledge_mappings:
        evidence = {
            "contract_id": CONTRACT_ID, "contract_version": CONTRACT_VERSION,
            "validator_id": validation.validator_id, "validation_status": validation.status,
            "input_sha256": assessment.input_sha256, "relation_type": mapping.relation_type,
        }
        evidence_json = canonical(evidence)
        evidence_hash = digest_text(evidence_json)
        record_id = _stable_id("content-item-knowledge-mapping", {
            "content_item_id": content_item_id, "knowledge_point_id": mapping.knowledge_point_id,
            "relation_type": mapping.relation_type, "evidence_sha256": evidence_hash,
        })
        event_id, created = _insert_event(
            connection, event_type="content_created", entity_type="content_item_knowledge_mappings",
            entity_id=record_id, operation="create",
            reason="bind independently validated source-derived question to approved knowledge point",
            actor=actor, import_run_id=run_id, transaction_id=transaction_id,
            after_state={"knowledge_point_id": mapping.knowledge_point_id, "relation_type": mapping.relation_type,
                         "mapping_status": "validated", "mapping_method": CONTRACT_ID},
            after_hash=evidence_hash, git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
        )
        connection.execute(
            """INSERT INTO content_item_knowledge_mappings(
                id,content_item_id,knowledge_point_id,relation_type,mapping_status,mapping_method,
                evidence_json,evidence_sha256,created_change_event_id
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (record_id, content_item_id, mapping.knowledge_point_id, mapping.relation_type, "validated",
             CONTRACT_ID, evidence_json, evidence_hash, event_id),
        )
        created_count += int(created)
    return created_count


def _insert_difficulty_evidence(
    connection: sqlite3.Connection, *, content_item_id: str, assessment: QuestionValidationAssessment,
    run_id: str, transaction_id: str, revision: str | None, actor: str,
) -> int:
    if assessment.difficulty is None or assessment.difficulty_evidence is None:
        return 0
    evidence = {"contract_id": CONTRACT_ID, "contract_version": CONTRACT_VERSION,
                "input_sha256": assessment.input_sha256, **assessment.difficulty_evidence}
    evidence_json = canonical(evidence)
    evidence_hash = digest_text(evidence_json)
    record_id = _stable_id("content-item-difficulty-evidence", {
        "content_item_id": content_item_id, "difficulty": assessment.difficulty,
        "evidence_sha256": evidence_hash,
    })
    event_id, created = _insert_event(
        connection, event_type="content_created", entity_type="content_item_difficulty_evidence",
        entity_id=record_id, operation="create",
        reason="persist feature-based difficulty evidence for independently validated source-derived question",
        actor=actor, import_run_id=run_id, transaction_id=transaction_id,
        after_state={"difficulty": assessment.difficulty, "evidence_status": "validated",
                     "method": assessment.difficulty_evidence["method"]},
        after_hash=evidence_hash, git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
    )
    connection.execute(
        """INSERT INTO content_item_difficulty_evidence(
            id,content_item_id,difficulty,evidence_status,method,evidence_json,evidence_sha256,
            created_change_event_id
        ) VALUES(?,?,?,?,?,?,?,?)""",
        (record_id, content_item_id, assessment.difficulty, "validated",
         str(assessment.difficulty_evidence["method"]), evidence_json, evidence_hash, event_id),
    )
    return int(created)


def _record_deferred(
    connection: sqlite3.Connection, *, source_version_id: str, extraction_run_id: str,
    reason: str, workspace: Path, actor: str,
) -> CandidateImportResult:
    transaction_id = _stable_id("transaction", {
        "source_version_id": source_version_id, "extraction_run_id": extraction_run_id,
        "operation": "question_candidate_deferred", "reason": reason,
    })
    event_row_id = _stable_id("source-lifecycle", {
        "source_version_id": source_version_id, "status": "deferred", "reason": reason,
    })
    with connection:
        event_id, created = _insert_event(
            connection, event_type="source_status_changed", entity_type="content_source_lifecycle_events",
            entity_id=event_row_id, operation="status_change", reason=reason, actor=actor,
            import_run_id=None, transaction_id=transaction_id,
            after_state={"status": "deferred", "retry_eligible": True, "reason": reason},
            after_hash=digest_text(reason), git_revision=_git_revision(workspace),
            tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
        )
        connection.execute(
            """INSERT OR IGNORE INTO content_source_lifecycle_events(
                id,source_version_id,status,reason,retry_eligible,change_event_id
            ) VALUES(?,?,?,?,?,?)""",
            (event_row_id, source_version_id, "deferred", reason, 1, event_id),
        )
    return CandidateImportResult(
        source_version_id=source_version_id, import_run_id=None, status="deferred",
        questions=0, content_items=0, internal_answer_evidence=0,
        internal_analysis_evidence=0, source_asset_evidence=0, change_events_created=int(created),
    )


def import_question_candidates(
    connection: sqlite3.Connection,
    *,
    source_version_id: str,
    source_root: str | Path,
    workspace: str | Path,
    actor: str = "codex",
) -> CandidateImportResult:
    """Store exact question candidates from one explicitly selected extracted source version."""
    root = Path(source_root).resolve(strict=True)
    work = Path(workspace).resolve(strict=True)
    connection.execute("PRAGMA foreign_keys=ON")
    _require_current_source_version(connection, source_version_id=source_version_id, source_root=root)
    extraction_run_id, source_document_id, blocks, assets = _require_extracted_blocks(
        connection, source_version_id=source_version_id,
    )
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_item_math_validation_evidence'"
    ).fetchone() is None:
        raise ContentQuestionCandidateImportError("v2_39_difficulty_evidence_contract_required")
    existing = _existing_result(connection, source_version_id=source_version_id, extraction_run_id=extraction_run_id)
    if existing is not None:
        return existing
    approved_knowledge_point_ids = {
        row[0] for row in connection.execute(
            "SELECT id FROM knowledge_points WHERE review_status='approved'"
        ).fetchall()
    }
    try:
        candidates = segment_questions(blocks)
        source_question_keys = _source_question_keys(list(candidates))
        candidate_assets = [
            _asset_option_payload_and_bindings(candidate, blocks=blocks, assets=assets)
            for candidate in candidates
        ]
        assessments = [
            assess_candidate(candidate, approved_knowledge_point_ids=approved_knowledge_point_ids)
            for candidate in candidates
        ]
    except QuestionSegmentationError as exc:
        return _record_deferred(
            connection, source_version_id=source_version_id, extraction_run_id=extraction_run_id,
            reason=f"question_candidate_segmentation_deferred:{exc}", workspace=work, actor=actor,
        )
    except AssetOptionAssociationError as exc:
        return _record_deferred(
            connection, source_version_id=source_version_id, extraction_run_id=extraction_run_id,
            reason=f"question_candidate_asset_association_deferred:{exc}", workspace=work, actor=actor,
        )
    events_created = 0
    answers = analyses = asset_evidence_count = 0
    with connection:
        run_id, transaction_id, created = _create_run(
            connection, extraction_run_id=extraction_run_id, source_version_id=source_version_id,
            workspace=work, actor=actor,
        )
        events_created += created
        revision = _git_revision(work)
        for candidate, source_question_key, (candidate_options, asset_bindings), assessment in zip(
            candidates, source_question_keys, candidate_assets, assessments, strict=True
        ):
            payload = _candidate_payload(candidate, source_question_key=source_question_key, options=candidate_options)
            content_hash = digest_text(canonical(payload))
            question_id = _stable_id("source-question", {
                "source_version_id": source_version_id, "source_question_no": source_question_key,
                "content_sha256": content_hash,
            })
            content_item_id = _stable_id("content-item", {"kind": "question", "question_id": question_id})
            fragment_text = "\n".join(
                next(block["raw_text"] for block in blocks if block["id"] == block_id)
                for block_id in candidate.source_block_ids
            )
            fragment_hash = digest_text(fragment_text)
            fragment_id = _stable_id("source-fragment", {
                "source_document_id": source_document_id, "question_id": question_id, "hash": fragment_hash,
            })
            fragment_event_id, fragment_created = _insert_event(
                connection, event_type="content_created", entity_type="source_fragments", entity_id=fragment_id,
                operation="create", reason="persist source fragment for structured question candidate", actor=actor,
                import_run_id=run_id, transaction_id=transaction_id,
                after_state={"location_type": "mixed", "source_question_no": source_question_key, "raw_source_question_no": candidate.source_question_no},
                after_hash=fragment_hash, git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
            )
            events_created += int(fragment_created)
            connection.execute(
                """INSERT INTO source_fragments(
                    id,source_document_id,location_type,page_number,paragraph_index,question_number,raw_text,raw_hash
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (fragment_id, source_document_id, "mixed", None, None, source_question_key, fragment_text, fragment_hash),
            )
            question_event_id, question_created = _insert_event(
                connection, event_type="content_created", entity_type="questions", entity_id=question_id,
                operation="create", reason="persist student-visible source-derived question candidate", actor=actor,
                import_run_id=run_id, transaction_id=transaction_id,
                after_state={"source_question_no": source_question_key, "raw_source_question_no": candidate.source_question_no, "question_type": candidate.question_type,
                             "answer_storage": "internal_only"}, after_hash=content_hash,
                git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
            )
            events_created += int(question_created)
            connection.execute(
                """INSERT INTO questions(
                    id,stem,options_json,answer,analysis,question_type,difficulty,stage,grade_level,source_document_id,
                    source_fragment_id,source_question_no,source_page,content_hash,extraction_status,quality_status,review_status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (question_id, candidate.stem, canonical(candidate_options), None, None, candidate.question_type,
                 assessment.difficulty, None, None, source_document_id, fragment_id, source_question_key,
                 None, content_hash, "structured", "pending" if assessment.content_eligible else "blocked", "pending"),
            )
            item_event_id, item_created = _insert_event(
                connection, event_type="content_created", entity_type="content_items", entity_id=content_item_id,
                operation="create", reason="persist reusable source-derived question content item", actor=actor,
                import_run_id=run_id, transaction_id=transaction_id,
                after_state={"item_kind": "question", "source_question_no": source_question_key, "raw_source_question_no": candidate.source_question_no},
                after_hash=content_hash, git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
            )
            events_created += int(item_created)
            connection.execute(
                """INSERT INTO content_items(
                    id,source_version_id,item_kind,student_payload_json,display_order,content_sha256,created_change_event_id
                ) VALUES(?,?,?,?,?,?,?)""",
                (content_item_id, source_version_id, "question", canonical(payload), int(candidate.source_question_no),
                 content_hash, item_event_id),
            )
            link_event_id, link_created = _insert_event(
                connection, event_type="content_created", entity_type="content_item_question_links", entity_id=content_item_id,
                operation="create", reason="link reusable source-derived item to student-visible question record", actor=actor,
                import_run_id=run_id, transaction_id=transaction_id,
                after_state={"question_id": question_id}, after_hash=content_hash,
                git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
            )
            events_created += int(link_created)
            connection.execute(
                "INSERT INTO content_item_question_links(content_item_id,question_id,created_change_event_id) VALUES(?,?,?)",
                (content_item_id, question_id, link_event_id),
            )
            events_created += _insert_math_validation_evidence(
                connection, content_item_id=content_item_id, question_id=question_id, content_sha256=content_hash,
                assessment=assessment, run_id=run_id, transaction_id=transaction_id, revision=revision, actor=actor,
            )
            if assessment.content_eligible:
                events_created += _insert_knowledge_mapping_evidence(
                    connection, content_item_id=content_item_id, assessment=assessment, run_id=run_id,
                    transaction_id=transaction_id, revision=revision, actor=actor,
                )
                events_created += _insert_difficulty_evidence(
                    connection, content_item_id=content_item_id, assessment=assessment, run_id=run_id,
                    transaction_id=transaction_id, revision=revision, actor=actor,
                )
            for evidence in candidate.student_evidence:
                events_created += _insert_field_evidence(
                    connection, content_item_id=content_item_id, field_name="stem", visibility="student", evidence=evidence,
                    run_id=run_id, transaction_id=transaction_id, revision=revision, actor=actor,
                )
            for evidence in candidate.option_evidence:
                events_created += _insert_field_evidence(
                    connection, content_item_id=content_item_id, field_name="options", visibility="student", evidence=evidence,
                    run_id=run_id, transaction_id=transaction_id, revision=revision, actor=actor,
                )
            for binding in asset_bindings:
                asset_evidence_count += 1
                events_created += _insert_question_source_asset_evidence(
                    connection, question_id=question_id, binding=binding, run_id=run_id,
                    transaction_id=transaction_id, revision=revision, actor=actor,
                )
            for evidence in candidate.answer_evidence:
                answers += 1
                events_created += _insert_internal_evidence(
                    connection, question_id=question_id, field_name="answer", evidence=evidence,
                    run_id=run_id, transaction_id=transaction_id, revision=revision, actor=actor,
                )
            for evidence in candidate.analysis_evidence:
                analyses += 1
                events_created += _insert_internal_evidence(
                    connection, question_id=question_id, field_name="analysis", evidence=evidence,
                    run_id=run_id, transaction_id=transaction_id, revision=revision, actor=actor,
                )
        totals = {
            "questions": len(candidates), "content_items": len(candidates),
            "internal_answer_evidence": answers, "internal_analysis_evidence": analyses,
            "source_asset_evidence": asset_evidence_count,
        }
        complete_id = f"{run_id}:completed"
        complete_event_id, complete_created = _insert_event(
            connection, event_type="import_run_finished", entity_type="content_import_run_events", entity_id=complete_id,
            operation="status_change", reason="complete source-derived question candidate import", actor=actor,
            import_run_id=run_id, transaction_id=transaction_id,
            after_state={"status": "completed", "totals": totals}, after_hash=digest_text(canonical(totals)),
            git_revision=revision, tool_id=IMPORTER_ID, tool_version=IMPORTER_VERSION,
        )
        events_created += int(complete_created)
        connection.execute(
            "INSERT INTO content_import_run_events(id,import_run_id,status,totals_json,change_event_id) VALUES(?,?,?,?,?)",
            (complete_id, run_id, "completed", canonical(totals), complete_event_id),
        )
    return CandidateImportResult(
        source_version_id=source_version_id, import_run_id=run_id, status="imported",
        questions=len(candidates), content_items=len(candidates), internal_answer_evidence=answers,
        internal_analysis_evidence=analyses, source_asset_evidence=asset_evidence_count,
        change_events_created=events_created,
    )
