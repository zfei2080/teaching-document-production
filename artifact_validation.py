"""Fail-closed artifact validation primitives for generated delivery files.

This module is intentionally pure and side-effect free:
- no database writes
- no quality report writes
- no production status changes
- no external dependencies beyond the standard library

Current scope only supports deterministic validation of DOCX artifacts via
zip+xml inspection. PDF extraction is explicitly unsupported.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import posixpath
import re
from typing import Any, Iterable
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
NS = {
    "w": W_NS,
    "r": R_NS,
    "rel": REL_NS,
    "a": A_NS,
    "pic": PIC_NS,
    "wp": WP_NS,
}

QUESTION_RE = re.compile(r"^\s*(\d+)[\.．、]\s*(.*)$")
SCORE_RE = re.compile(r"（\s*(\d+)\s*分\s*）|\(\s*(\d+)\s*分\s*\)")
ANSWER_LINE_RE = re.compile(r"答案\s*[:：]\s*(.+)")
LEAK_TOKENS = (
    "答案",
    "解：",
    "解析",
    "故选",
    "正确答案",
)


class ArtifactValidationError(ValueError):
    """Raised when manifest input is malformed."""


@dataclass(frozen=True)
class ArtifactManifest:
    artifact_type: str
    audience: str
    content_bytes: bytes
    source_name: str
    expected_question_numbers: tuple[str, ...]
    expected_total_score: int | None
    expected_asset_names: tuple[str, ...] = ()
    expected_asset_hashes: tuple[str, ...] = ()

    def to_hash_payload(self) -> dict[str, object]:
        return {
            "artifact_type": self.artifact_type,
            "audience": self.audience,
            "content_sha256": sha256(self.content_bytes).hexdigest(),
            "source_name": self.source_name,
            "expected_question_numbers": list(self.expected_question_numbers),
            "expected_total_score": self.expected_total_score,
            "expected_asset_names": list(self.expected_asset_names),
            "expected_asset_hashes": list(self.expected_asset_hashes),
        }


@dataclass(frozen=True)
class ArtifactAsset:
    part_name: str
    sha256_hex: str
    content_type: str | None
    relationship_ids: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactQuestion:
    number: str
    line_text: str
    score: int | None


@dataclass(frozen=True)
class ArtifactView:
    artifact_type: str
    paragraphs: tuple[str, ...]
    question_lines: tuple[ArtifactQuestion, ...]
    assets: tuple[ArtifactAsset, ...]
    embedded_asset_names: tuple[str, ...]
    content_sha256: str


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    severity: str
    message: str
    evidence: dict[str, object]


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    status: str
    findings: tuple[ValidationFinding, ...]


@dataclass(frozen=True)
class EvidenceBundle:
    manifest_hash: str
    artifact_hash: str
    overall_status: str
    checks: tuple[ValidationCheck, ...]


@dataclass(frozen=True)
class ArtifactEvidenceRecord:
    artifact_role: str
    gate: str
    manifest_hash: str | None
    artifact_hash: str | None
    checks_json: str
    findings_json: str


DOCX_STUDENT_REQUIRED_CHECKS = ("structure", "questions_and_score", "assets", "answer_leak")
DOCX_TEACHER_REQUIRED_CHECKS = ("structure", "questions_and_score", "assets")
PDF_REQUIRED_ROLE = "pdf"


def build_manifest(*, artifact_type: str, audience: str, content_bytes: bytes, source_name: str,
                   expected_question_numbers: Iterable[str], expected_total_score: int | None,
                   expected_asset_names: Iterable[str] = (), expected_asset_hashes: Iterable[str] = ()) -> ArtifactManifest:
    eqn = tuple(str(item).strip() for item in expected_question_numbers)
    ean = tuple(str(item).strip() for item in expected_asset_names)
    eah = tuple(str(item).strip().lower() for item in expected_asset_hashes)
    if artifact_type not in {"docx", "pdf"}:
        raise ArtifactValidationError("unsupported artifact_type")
    if audience not in {"student", "teacher"}:
        raise ArtifactValidationError("unsupported audience")
    if not isinstance(content_bytes, (bytes, bytearray)) or not bytes(content_bytes):
        raise ArtifactValidationError("content_bytes is required")
    if not source_name.strip():
        raise ArtifactValidationError("source_name is required")
    if any(not item for item in eqn):
        raise ArtifactValidationError("expected_question_numbers contains blank value")
    return ArtifactManifest(
        artifact_type=artifact_type,
        audience=audience,
        content_bytes=bytes(content_bytes),
        source_name=source_name.strip(),
        expected_question_numbers=eqn,
        expected_total_score=expected_total_score,
        expected_asset_names=ean,
        expected_asset_hashes=eah,
    )


def manifest_hash(manifest: ArtifactManifest) -> str:
    payload = json.dumps(manifest.to_hash_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def extract_artifact_view(manifest: ArtifactManifest) -> ArtifactView | ValidationCheck:
    if manifest.artifact_type == "pdf":
        return ValidationCheck(
            name="extract_artifact_view",
            status="unsupported",
            findings=(ValidationFinding(
                code="pdf_unsupported",
                severity="error",
                message="PDF 提取未实现；当前成品验证基础模块不支持 PDF。",
                evidence={"artifact_type": manifest.artifact_type, "source_name": manifest.source_name},
            ),),
        )
    try:
        return _extract_docx_view(manifest)
    except (BadZipFile, KeyError, ET.ParseError, ValueError) as exc:
        return ValidationCheck(
            name="extract_artifact_view",
            status="fail",
            findings=(ValidationFinding(
                code="docx_extract_failed",
                severity="error",
                message="DOCX 只读提取失败。",
                evidence={"error": str(exc), "source_name": manifest.source_name},
            ),),
        )


def validate_artifact(manifest: ArtifactManifest) -> EvidenceBundle:
    extracted = extract_artifact_view(manifest)
    mh = manifest_hash(manifest)
    ah = sha256(manifest.content_bytes).hexdigest()
    if isinstance(extracted, ValidationCheck):
        return EvidenceBundle(manifest_hash=mh, artifact_hash=ah, overall_status="unsupported" if extracted.status == "unsupported" else "fail", checks=(extracted,))

    checks = (
        _check_answer_leak(manifest, extracted),
        _check_questions_and_score(manifest, extracted),
        _check_structure(manifest, extracted),
        _check_assets(manifest, extracted),
    )
    statuses = {check.status for check in checks}
    if "fail" in statuses:
        overall = "fail"
    elif "unsupported" in statuses or "missing" in statuses:
        overall = "unsupported"
    else:
        overall = "pass"
    return EvidenceBundle(manifest_hash=mh, artifact_hash=ah, overall_status=overall, checks=checks)


def build_artifact_evidence(*, artifact_type: str, audience: str, validation: EvidenceBundle) -> ArtifactEvidenceRecord:
    artifact_role = infer_artifact_role(artifact_type=artifact_type, audience=audience)
    payload_checks = []
    findings = []
    for check in validation.checks:
        payload_checks.append(
            {
                "name": check.name,
                "status": check.status,
                "findings": [
                    {
                        "code": finding.code,
                        "severity": finding.severity,
                        "message": finding.message,
                        "evidence": finding.evidence,
                    }
                    for finding in check.findings
                ],
            }
        )
        for finding in check.findings:
            findings.append(
                {
                    "check": check.name,
                    "code": finding.code,
                    "severity": finding.severity,
                    "message": finding.message,
                    "evidence": finding.evidence,
                }
            )
    return ArtifactEvidenceRecord(
        artifact_role=artifact_role,
        gate=validation_gate_for_delivery(artifact_type=artifact_type, audience=audience, validation=validation),
        manifest_hash=validation.manifest_hash,
        artifact_hash=validation.artifact_hash,
        checks_json=json.dumps(payload_checks, ensure_ascii=False, sort_keys=True),
        findings_json=json.dumps(findings, ensure_ascii=False, sort_keys=True),
    )


def infer_artifact_role(*, artifact_type: str, audience: str) -> str:
    if artifact_type == "pdf":
        return PDF_REQUIRED_ROLE
    if artifact_type != "docx":
        raise ArtifactValidationError("unsupported artifact_type")
    if audience not in {"student", "teacher"}:
        raise ArtifactValidationError("docx audience must be student or teacher")
    return f"docx_{audience}"


def required_artifact_checks(*, artifact_type: str, audience: str) -> tuple[str, ...]:
    role = infer_artifact_role(artifact_type=artifact_type, audience=audience)
    if role == "docx_student":
        return DOCX_STUDENT_REQUIRED_CHECKS
    if role == "docx_teacher":
        return DOCX_TEACHER_REQUIRED_CHECKS
    return ()


def validation_gate_for_delivery(*, artifact_type: str, audience: str, validation: EvidenceBundle) -> str:
    role = infer_artifact_role(artifact_type=artifact_type, audience=audience)
    if role == PDF_REQUIRED_ROLE:
        return "unsupported"
    checks = {check.name: check.status for check in validation.checks}
    required = required_artifact_checks(artifact_type=artifact_type, audience=audience)
    if any(name not in checks for name in required):
        return "missing"
    if any(checks[name] == "unsupported" for name in required):
        return "unsupported"
    if any(checks[name] in {"fail", "missing"} for name in required):
        return "fail"
    if validation.overall_status == "unsupported":
        return "unsupported"
    if validation.overall_status == "fail":
        return "fail"
    return "pass"


def _extract_docx_view(manifest: ArtifactManifest) -> ArtifactView:
    with ZipFile(BytesIO(manifest.content_bytes), "r") as zf:
        names = set(zf.namelist())
        document_xml = _read_xml(zf, "word/document.xml")
        rels_xml = _read_xml(zf, "word/_rels/document.xml.rels") if "word/_rels/document.xml.rels" in names else None
        content_types = _read_content_types(zf)
        rel_target_map = _read_relationship_targets(rels_xml)
        paragraphs = tuple(_iter_paragraph_texts(document_xml))
        question_lines = tuple(_extract_questions(paragraphs))
        assets = tuple(_extract_assets(zf, rel_target_map, content_types))
        embedded_names = tuple(sorted(asset.part_name for asset in assets))
        return ArtifactView(
            artifact_type="docx",
            paragraphs=paragraphs,
            question_lines=question_lines,
            assets=assets,
            embedded_asset_names=embedded_names,
            content_sha256=sha256(manifest.content_bytes).hexdigest(),
        )


def _read_xml(zf: ZipFile, name: str) -> ET.Element:
    return ET.fromstring(zf.read(name))


def _read_content_types(zf: ZipFile) -> dict[str, str]:
    root = _read_xml(zf, "[Content_Types].xml")
    mapping: dict[str, str] = {}
    for override in root.findall("{*}Override"):
        part_name = override.attrib.get("PartName", "").lstrip("/")
        content_type = override.attrib.get("ContentType", "")
        if part_name:
            mapping[part_name] = content_type
    return mapping


def _read_relationship_targets(rels_xml: ET.Element | None) -> dict[str, str]:
    if rels_xml is None:
        return {}
    mapping: dict[str, str] = {}
    for rel in rels_xml.findall("rel:Relationship", {"rel": REL_NS}):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rel_id and target:
            mapping[rel_id] = posixpath.normpath(posixpath.join("word", target))
    return mapping


def _iter_paragraph_texts(document_xml: ET.Element) -> Iterable[str]:
    for paragraph in document_xml.findall(".//w:p", NS):
        parts: list[str] = []
        for text_node in paragraph.findall(".//w:t", NS):
            parts.append(text_node.text or "")
        text = "".join(parts).strip()
        if text:
            yield text


def _extract_questions(paragraphs: Iterable[str]) -> Iterable[ArtifactQuestion]:
    for line in paragraphs:
        match = QUESTION_RE.match(line)
        if not match:
            continue
        score = None
        score_match = SCORE_RE.search(line)
        if score_match:
            score_text = score_match.group(1) or score_match.group(2)
            score = int(score_text)
        yield ArtifactQuestion(number=match.group(1), line_text=line, score=score)


def _extract_assets(zf: ZipFile, rel_target_map: dict[str, str], content_types: dict[str, str]) -> Iterable[ArtifactAsset]:
    root = _read_xml(zf, "word/document.xml")
    target_to_rel_ids: dict[str, list[str]] = {}
    for blip in root.findall(".//a:blip", NS):
        rel_id = blip.attrib.get(f"{{{R_NS}}}embed")
        if rel_id and rel_id in rel_target_map:
            target_to_rel_ids.setdefault(rel_target_map[rel_id], []).append(rel_id)
    for part_name, rel_ids in sorted(target_to_rel_ids.items()):
        payload = zf.read(part_name)
        yield ArtifactAsset(
            part_name=part_name,
            sha256_hex=sha256(payload).hexdigest(),
            content_type=content_types.get(part_name),
            relationship_ids=tuple(sorted(rel_ids)),
        )


def _check_answer_leak(manifest: ArtifactManifest, view: ArtifactView) -> ValidationCheck:
    findings: list[ValidationFinding] = []
    if manifest.audience != "student":
        return ValidationCheck(name="answer_leak", status="pass", findings=())
    for index, line in enumerate(view.paragraphs, start=1):
        if ANSWER_LINE_RE.search(line) or any(token in line for token in LEAK_TOKENS):
            findings.append(ValidationFinding(
                code="student_answer_leak",
                severity="error",
                message="学生卷出现疑似答案或解析内容。",
                evidence={"paragraph_index": index, "text": line},
            ))
    return ValidationCheck(name="answer_leak", status="fail" if findings else "pass", findings=tuple(findings))


def _check_questions_and_score(manifest: ArtifactManifest, view: ArtifactView) -> ValidationCheck:
    findings: list[ValidationFinding] = []
    actual_numbers = tuple(question.number for question in view.question_lines)
    if actual_numbers != manifest.expected_question_numbers:
        findings.append(ValidationFinding(
            code="question_numbers_mismatch",
            severity="error",
            message="题号或题目顺序不匹配。",
            evidence={"expected": manifest.expected_question_numbers, "actual": actual_numbers},
        ))
    if manifest.expected_total_score is not None:
        scores = [question.score for question in view.question_lines]
        if any(score is None for score in scores):
            findings.append(ValidationFinding(
                code="score_missing",
                severity="error",
                message="存在题目未标注分值，无法确认总分。",
                evidence={"scores": scores},
            ))
        else:
            total = sum(int(score) for score in scores)
            if total != manifest.expected_total_score:
                findings.append(ValidationFinding(
                    code="total_score_mismatch",
                    severity="error",
                    message="题目总分不匹配。",
                    evidence={"expected": manifest.expected_total_score, "actual": total},
                ))
    return ValidationCheck(name="questions_and_score", status="fail" if findings else "pass", findings=tuple(findings))


def _check_structure(manifest: ArtifactManifest, view: ArtifactView) -> ValidationCheck:
    findings: list[ValidationFinding] = []
    if not view.paragraphs:
        findings.append(ValidationFinding(
            code="empty_document",
            severity="error",
            message="文档正文为空。",
            evidence={},
        ))
    if not view.question_lines:
        findings.append(ValidationFinding(
            code="missing_questions",
            severity="error",
            message="文档中未识别出题目行。",
            evidence={"paragraph_count": len(view.paragraphs)},
        ))
    return ValidationCheck(name="structure", status="fail" if findings else "pass", findings=tuple(findings))


def _check_assets(manifest: ArtifactManifest, view: ArtifactView) -> ValidationCheck:
    findings: list[ValidationFinding] = []
    actual_names = view.embedded_asset_names
    if manifest.expected_asset_names and actual_names != manifest.expected_asset_names:
        findings.append(ValidationFinding(
            code="asset_name_mismatch",
            severity="error",
            message="嵌入资产名称绑定不匹配。",
            evidence={"expected": manifest.expected_asset_names, "actual": actual_names},
        ))
    if manifest.expected_asset_hashes:
        actual_hashes = tuple(asset.sha256_hex for asset in view.assets)
        if actual_hashes != manifest.expected_asset_hashes:
            findings.append(ValidationFinding(
                code="asset_hash_mismatch",
                severity="error",
                message="嵌入资产哈希绑定不匹配。",
                evidence={"expected": manifest.expected_asset_hashes, "actual": actual_hashes},
            ))
    if manifest.expected_asset_names and not view.assets:
        findings.append(ValidationFinding(
            code="asset_missing",
            severity="error",
            message="清单要求存在嵌入资产，但文档未提取到任何绑定资产。",
            evidence={"expected": manifest.expected_asset_names},
        ))
    return ValidationCheck(name="assets", status="fail" if findings else "pass", findings=tuple(findings))
