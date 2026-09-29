"""Read-only Microsoft Word COM adapter for controlled legacy `.doc` conversion.

The adapter is deliberately a narrow subprocess boundary used by
`controlled_doc_conversion.convert_legacy_doc`.  It never selects source files,
never writes to the source location, and only accepts the LibreOffice-compatible
argument shape supplied by that module.
"""
from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys


class WordComConverterError(RuntimeError):
    """Raised when the installed Word automation server cannot convert safely."""


def _powershell(script: str, *, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if environment:
        env.update(environment)
    return subprocess.run(
        [
            "powershell",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
        env=env,
    )


def _version() -> int:
    script = r'''
$ErrorActionPreference = "Stop"
$word = $null
try {
  $word = New-Object -ComObject Word.Application
  [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
  Write-Output ("Microsoft Word COM " + $word.Version)
} finally {
  if ($null -ne $word) { $word.Quit() }
}
'''
    completed = _powershell(script)
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr or "word_com_version_failed")
        return completed.returncode or 1
    sys.stdout.write(completed.stdout)
    return 0


def _convert(argv: list[str]) -> int:
    expected = ["--headless", "--convert-to", "docx", "--outdir"]
    if len(argv) != 6 or argv[:4] != expected:
        raise WordComConverterError("unsupported_converter_arguments")
    output_dir = Path(argv[4]).resolve()
    source = Path(argv[5]).resolve(strict=True)
    if source.suffix.lower() != ".doc":
        raise WordComConverterError("source_not_legacy_doc")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{source.stem}.docx"
    if target.exists():
        target.unlink()

    script = r'''
$SourcePath = $env:CONTROLLED_DOC_CONVERTER_SOURCE
$TargetPath = $env:CONTROLLED_DOC_CONVERTER_TARGET
$ErrorActionPreference = "Stop"
$word = $null
$document = $null
try {
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  try { $word.AutomationSecurity = 3 } catch { }
  $document = $word.Documents.Open($SourcePath, $false, $true, $false)
  # 16 is wdFormatDocumentDefault (.docx), supported by Word 2007 and later.
  $document.SaveAs($TargetPath, 16)
  if (-not (Test-Path -LiteralPath $TargetPath)) { throw "target_not_created" }
} finally {
  if ($null -ne $document) { try { $document.Close(0) } catch { } }
  if ($null -ne $word) { try { $word.Quit() } catch { } }
}
exit 0
'''
    completed = _powershell(
        script,
        environment={
            "CONTROLLED_DOC_CONVERTER_SOURCE": str(source),
            "CONTROLLED_DOC_CONVERTER_TARGET": str(target),
        },
    )
    if completed.returncode != 0:
        if target.exists():
            target.unlink()
        sys.stderr.write(completed.stderr or completed.stdout or "word_com_conversion_failed")
        return completed.returncode or 1
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv == ["--version"]:
            return _version()
        return _convert(argv)
    except (OSError, ValueError, WordComConverterError) as exc:
        sys.stderr.write(f"word_com_converter_blocked:{type(exc).__name__}:{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
