"""Fail-closed conversion of trusted legacy .doc originals into temporary DOCX copies.

This module deliberately does not approve, import, map, or deliver converted content.
It only creates a traceable temporary conversion artifact.  A later, separate fidelity
chain must prove that text, tables, formulae, and graphics are preserved before any
content can be used by the teaching-document workflow.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Sequence
from zipfile import BadZipFile, ZipFile
import json
import subprocess
import tempfile


REQUIRED_DOCX_PARTS = (
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
)


class LegacyDocConversionBlockedError(RuntimeError):
    """Raised whenever a conversion cannot produce a safe temporary DOCX artifact."""


@dataclass(frozen=True)
class LegacyDocConversionResult:
    source_path: str
    source_sha256: str
    converted_path: str
    converted_sha256: str
    converter_command: tuple[str, ...]
    converter_version: str
    manifest: dict[str, Any]

    @property
    def eligible_for_delivery(self) -> bool:
        """Conversion alone never proves fidelity or teaching suitability."""
        return False


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest().upper()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_docx(path: Path) -> tuple[str, ...]:
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
    except (BadZipFile, OSError) as exc:
        raise LegacyDocConversionBlockedError(f"converted_docx_invalid:{type(exc).__name__}") from exc
    missing = tuple(part for part in REQUIRED_DOCX_PARTS if part not in names)
    if missing:
        raise LegacyDocConversionBlockedError(
            "converted_docx_missing_parts:" + ",".join(missing)
        )
    return tuple(sorted(name for name in names if name.startswith("word/media/")))


def _run(command: Sequence[str], *, cwd: Path) -> CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise LegacyDocConversionBlockedError("converter_not_found") from exc
    except subprocess.TimeoutExpired as exc:
        raise LegacyDocConversionBlockedError("converter_timeout") from exc


def _converter_version(converter_command: tuple[str, ...], *, cwd: Path) -> str:
    completed = _run((*converter_command, "--version"), cwd=cwd)
    if completed.returncode != 0:
        raise LegacyDocConversionBlockedError(
            f"converter_version_failed:{completed.returncode}"
        )
    version = (completed.stdout or completed.stderr).strip()
    if not version:
        raise LegacyDocConversionBlockedError("converter_version_empty")
    return version[:4000]


def convert_legacy_doc(
    source_path: str | Path,
    *,
    allowed_root: str | Path,
    conversion_root: str | Path,
    converter_command: Sequence[str],
) -> LegacyDocConversionResult:
    """Create one temporary DOCX conversion artifact without touching the original.

    ``converter_command`` is intentionally explicit so the caller can record the
    exact converter executable and wrapper used.  The supported command contract is
    LibreOffice-compatible:

    ``<converter> --headless --convert-to docx --outdir <tempdir> <source.doc>``
    """

    source_root = Path(allowed_root).resolve(strict=True)
    source = Path(source_path).resolve(strict=True)
    output_root = Path(conversion_root).resolve()
    command_prefix = tuple(str(item) for item in converter_command)

    if not command_prefix:
        raise LegacyDocConversionBlockedError("converter_command_missing")
    if not _is_within(source, source_root):
        raise LegacyDocConversionBlockedError("source_outside_allowed_root")
    if source.suffix.lower() != ".doc":
        raise LegacyDocConversionBlockedError("source_not_legacy_doc")
    if _is_within(output_root, source_root):
        raise LegacyDocConversionBlockedError("conversion_root_inside_source_root")

    output_root.mkdir(parents=True, exist_ok=True)
    source_hash_before = _sha256(source)
    converter_version = _converter_version(command_prefix, cwd=output_root)
    converter_fingerprint = sha256(
        _canonical_json(
            {
                "command": command_prefix,
                "version": converter_version,
            }
        ).encode("utf-8")
    ).hexdigest().upper()

    destination_dir = output_root / source_hash_before / converter_fingerprint
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{source.stem}.docx"
    if destination.exists():
        # Office conversions can embed a current timestamp in DOCX metadata.  A
        # same source/converter invocation must therefore reuse the previously
        # verified immutable temporary artifact rather than regenerate a byte-
        # different file and mistake non-deterministic metadata for a conflict.
        media_parts = _validate_docx(destination)
    else:
        with tempfile.TemporaryDirectory(dir=output_root, prefix="legacy-doc-convert-") as temp:
            temp_dir = Path(temp)
            completed = _run(
                (
                    *command_prefix,
                    "--headless",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    str(temp_dir),
                    str(source),
                ),
                cwd=output_root,
            )
            if completed.returncode != 0:
                raise LegacyDocConversionBlockedError(
                    f"converter_failed:{completed.returncode}"
                )
            generated = tuple(sorted(temp_dir.glob("*.docx")))
            if len(generated) != 1:
                raise LegacyDocConversionBlockedError(
                    f"converter_output_count_invalid:{len(generated)}"
                )
            media_parts = _validate_docx(generated[0])
            source_hash_after = _sha256(source)
            if source_hash_before != source_hash_after:
                raise LegacyDocConversionBlockedError("source_changed_during_conversion")
            generated[0].replace(destination)

    converted_hash = _sha256(destination)
    manifest = {
        "schema": "controlled-legacy-doc-conversion-v1",
        "source": {
            "path": str(source),
            "sha256": source_hash_before,
            "file_type": "doc",
        },
        "converted": {
            "path": str(destination.resolve()),
            "sha256": converted_hash,
            "file_type": "docx",
            "required_docx_parts": list(REQUIRED_DOCX_PARTS),
            "media_parts": list(media_parts),
        },
        "converter": {
            "command": list(command_prefix),
            "version": converter_version,
            "fingerprint": converter_fingerprint,
        },
        "conversion": {
            "source_unchanged": True,
            "fidelity_status": "conversion_structural_only",
            "eligible_for_delivery": False,
            "next_required_gate": "independent_text_table_formula_graphic_fidelity",
        },
    }
    return LegacyDocConversionResult(
        source_path=str(source),
        source_sha256=source_hash_before,
        converted_path=str(destination.resolve()),
        converted_sha256=converted_hash,
        converter_command=command_prefix,
        converter_version=converter_version,
        manifest=manifest,
    )
