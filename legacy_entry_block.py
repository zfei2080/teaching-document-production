"""Fail-closed guard for archived pre-rebuild production entry points."""

from __future__ import annotations


CONTROL_DOCUMENT = "docs/交接文档.md"
CURRENT_SAFE_MODULES = (
    "automatic_verification_runner.py, automatic_gate.py, "
    "selection_engine.py and production_workflow.py"
)


class LegacyEntryBlockedError(RuntimeError):
    """Raised whenever archived production code is invoked."""


def blocked_message(entry_name: str) -> str:
    return (
        f"BLOCKED: {entry_name} is an archived pre-rebuild entry and must not be used. "
        "It can bypass current evidence, approval, selection, document-quality, and delivery gates. "
        f"Follow {CONTROL_DOCUMENT}. Current safe modules: {CURRENT_SAFE_MODULES}. "
        "The system is not yet authorized to generate or deliver formal teaching documents."
    )


def block_legacy_entry(entry_name: str) -> None:
    raise LegacyEntryBlockedError(blocked_message(entry_name))
