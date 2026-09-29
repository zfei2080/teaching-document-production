from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

REQUIRED_DOCX_PARTS = (
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
)

ISOLATION_REASON_MISSING_ANSWERS = "missing_answers"
ISOLATION_REASON_COMPLEX_LAYOUT = "complex_layout"
ISOLATION_REASON_UNCUTTABLE = "not_reliably_segmentable"
ISOLATION_REASON_PATH_OUTSIDE_ROOT = "path_outside_root"
ISOLATION_REASON_ARCHIVE_PATH_OUTSIDE_ROOT = "archive_path_outside_root"
ISOLATION_REASON_ARCHIVE_HASH_MISMATCH = "archive_hash_mismatch"
ISOLATION_REASON_ARCHIVE_MISSING = "archive_missing"
ISOLATION_REASON_MISSING_DOCX_PARTS = "missing_docx_parts"
ISOLATION_REASON_INVALID_DOCX = "invalid_docx"


@dataclass(frozen=True)
class SourceIntakeResult:
    source_path: str
    canonical_path: str
    sha256_hex: str
    archive_path: str
    archive_sha256_hex: str
    archive_matches_source: bool
    size_bytes: int
    is_within_root: bool
    required_docx_parts: tuple[str, ...]
    missing_docx_parts: tuple[str, ...]
    has_answers: bool
    has_complex_content: bool
    can_reliably_segment: bool
    trusted_source: bool
    approved: bool
    isolated: bool
    isolation_reasons: tuple[str, ...]
    manifest: dict[str, Any]

    def to_source_document_record(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "canonical_path": self.canonical_path,
            # source_documents.relative_path must name the immutable archive
            # copy.  The original path is retained separately for audit only.
            "archive_path": self.archive_path,
            "file_hash": self.archive_sha256_hex,
            "original_relative_path": self.source_path,
            "original_file_hash": self.sha256_hex,
            "archive_matches_source": self.archive_matches_source,
            "size_bytes": self.size_bytes,
            "trusted_source": self.trusted_source,
            "approved": self.approved,
            "isolated": self.isolated,
            "isolation_reasons": list(self.isolation_reasons),
            "required_docx_parts": list(self.required_docx_parts),
            "missing_docx_parts": list(self.missing_docx_parts),
            "content_flags": {
                "has_answers": self.has_answers,
                "has_complex_content": self.has_complex_content,
                "can_reliably_segment": self.can_reliably_segment,
            },
            "manifest": self.manifest,
            "intake_manifest_json": self.manifest,
        }


def intake_trusted_source(
    source_path: str | Path,
    allowed_root: str | Path,
    archive_path: str | Path,
    archive_root: str | Path,
    *,
    trusted_source: bool,
    has_answers: bool,
    has_complex_content: bool,
    can_reliably_segment: bool,
) -> SourceIntakeResult:
    root = Path(allowed_root).resolve(strict=True)
    expected_archive_root = Path(archive_root).resolve(strict=True)
    candidate = Path(source_path)
    canonical = candidate.resolve(strict=True)
    archive_candidate = Path(archive_path)
    is_within_root = _is_within_root(canonical, root)

    file_bytes = canonical.read_bytes()
    size_bytes = len(file_bytes)
    digest = sha256(file_bytes).hexdigest()

    archive_missing = not archive_candidate.exists()
    if archive_missing:
        archive_canonical = archive_candidate.absolute()
        archive_bytes = b""
    else:
        archive_canonical = archive_candidate.resolve(strict=True)
        archive_bytes = archive_canonical.read_bytes()
    archive_digest = sha256(archive_bytes).hexdigest() if not archive_missing else ""
    archive_within_root = not archive_missing and _is_within_root(archive_canonical, expected_archive_root)
    archive_matches_source = not archive_missing and digest == archive_digest

    missing_docx_parts, invalid_docx = _inspect_docx(canonical)

    isolation_reasons: list[str] = []
    if not is_within_root:
        isolation_reasons.append(ISOLATION_REASON_PATH_OUTSIDE_ROOT)
    if archive_missing:
        isolation_reasons.append(ISOLATION_REASON_ARCHIVE_MISSING)
    elif not archive_within_root:
        isolation_reasons.append(ISOLATION_REASON_ARCHIVE_PATH_OUTSIDE_ROOT)
    elif not archive_matches_source:
        isolation_reasons.append(ISOLATION_REASON_ARCHIVE_HASH_MISMATCH)
    if invalid_docx:
        isolation_reasons.append(ISOLATION_REASON_INVALID_DOCX)
    elif missing_docx_parts:
        isolation_reasons.append(ISOLATION_REASON_MISSING_DOCX_PARTS)
    if not has_answers:
        isolation_reasons.append(ISOLATION_REASON_MISSING_ANSWERS)
    if has_complex_content:
        isolation_reasons.append(ISOLATION_REASON_COMPLEX_LAYOUT)
    if not can_reliably_segment:
        isolation_reasons.append(ISOLATION_REASON_UNCUTTABLE)

    isolated = bool(isolation_reasons)
    manifest = {
        "source_path": str(source_path),
        "canonical_path": str(canonical),
        "sha256": digest,
        "archive_path": str(archive_canonical),
        "archive_sha256": archive_digest,
        "archive_matches_source": archive_matches_source,
        "size_bytes": size_bytes,
        "trusted_source": trusted_source,
        "approved": False,
        "isolated": isolated,
        "isolation_reasons": isolation_reasons,
        "docx_parts": {
            "required": list(REQUIRED_DOCX_PARTS),
            "missing": list(missing_docx_parts),
        },
        "embedded_media": _docx_media_hashes(canonical),
        "content_flags": {
            "has_answers": has_answers,
            "has_complex_content": has_complex_content,
            "can_reliably_segment": can_reliably_segment,
        },
    }

    return SourceIntakeResult(
        source_path=str(source_path),
        canonical_path=str(canonical),
        sha256_hex=digest,
        archive_path=str(archive_canonical),
        archive_sha256_hex=archive_digest,
        archive_matches_source=archive_matches_source,
        size_bytes=size_bytes,
        is_within_root=is_within_root,
        required_docx_parts=REQUIRED_DOCX_PARTS,
        missing_docx_parts=missing_docx_parts,
        has_answers=has_answers,
        has_complex_content=has_complex_content,
        can_reliably_segment=can_reliably_segment,
        trusted_source=trusted_source,
        approved=False,
        isolated=isolated,
        isolation_reasons=tuple(isolation_reasons),
        manifest=manifest,
    )


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _inspect_docx(path: Path) -> tuple[tuple[str, ...], bool]:
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
    except BadZipFile:
        return tuple(REQUIRED_DOCX_PARTS), True

    missing = tuple(part for part in REQUIRED_DOCX_PARTS if part not in names)
    return missing, False


def _docx_media_hashes(path: Path) -> list[dict[str, str]]:
    """Inventory package media so a later asset import has source evidence."""
    try:
        with ZipFile(path) as archive:
            return [
                {"package_path": name, "sha256": sha256(archive.read(name)).hexdigest()}
                for name in sorted(archive.namelist())
                if name.startswith("word/media/") and not name.endswith("/")
            ]
    except BadZipFile:
        return []
