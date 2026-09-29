"""Read-only Word COM content profiler used by the P1-2b fidelity gate."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


class WordComProfileError(RuntimeError):
    """Raised when a source or converted document cannot be profiled."""


def _powershell(script: str, *, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if environment:
        env.update(environment)
    return subprocess.run(
        ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
        env=env,
    )


def profile_document(path: str | Path) -> dict[str, Any]:
    """Return a deterministic read-only semantic profile for a Word document."""
    document_path = Path(path).resolve(strict=True)
    if document_path.suffix.lower() not in {".doc", ".docx"}:
        raise WordComProfileError("unsupported_document_type")
    script = r'''
$DocumentPath = $env:CONTROLLED_DOC_PROFILE_SOURCE
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
function Normalize-WordText([string]$Value) {
  if ($null -eq $Value) { return "" }
  return (($Value -replace "[\s\u00A0\u0007]", ""))
}
function Get-TextHash([string]$Value) {
  $bytes = [System.Text.Encoding]::UTF8.GetBytes((Normalize-WordText $Value))
  $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  return -join ($hash | ForEach-Object { $_.ToString("x2") })
}
$word = $null
$document = $null
try {
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  try { $word.AutomationSecurity = 3 } catch { }
  $document = $word.Documents.Open($DocumentPath, $false, $true, $false)
  $tables = @()
  for ($tableIndex = 1; $tableIndex -le $document.Tables.Count; $tableIndex++) {
    $table = $document.Tables.Item($tableIndex)
    $cellTexts = @()
    for ($rowIndex = 1; $rowIndex -le $table.Rows.Count; $rowIndex++) {
      for ($cellIndex = 1; $cellIndex -le $table.Rows.Item($rowIndex).Cells.Count; $cellIndex++) {
        $cellTexts += (Normalize-WordText $table.Rows.Item($rowIndex).Cells.Item($cellIndex).Range.Text)
      }
    }
    $tables += [PSCustomObject]@{
      rows = [int]$table.Rows.Count
      columns = [int]$table.Columns.Count
      text_sha256 = Get-TextHash ($cellTexts -join "`n")
    }
  }
  $inlineShapes = @()
  for ($index = 1; $index -le $document.InlineShapes.Count; $index++) {
    $shape = $document.InlineShapes.Item($index)
    $oleClassType = $null
    try { $oleClassType = [string]$shape.OLEFormat.ClassType } catch { }
    $inlineShapes += [PSCustomObject]@{
      type = [int]$shape.Type
      width = [math]::Round([double]$shape.Width, 3)
      height = [math]::Round([double]$shape.Height, 3)
      ole_class_type = $oleClassType
    }
  }
  $shapes = @()
  for ($index = 1; $index -le $document.Shapes.Count; $index++) {
    $shape = $document.Shapes.Item($index)
    $shapes += [PSCustomObject]@{
      type = [int]$shape.Type
      width = [math]::Round([double]$shape.Width, 3)
      height = [math]::Round([double]$shape.Height, 3)
    }
  }
  $omathCount = $null
  try { $omathCount = [int]$document.OMaths.Count } catch { }
  $normalizedContentText = Normalize-WordText $document.Content.Text
  [PSCustomObject]@{
    schema = "word-com-readonly-profile-v1"
    file_type = [System.IO.Path]::GetExtension($DocumentPath).TrimStart('.').ToLowerInvariant()
    normalized_content_text = $normalizedContentText
    normalized_content_sha256 = Get-TextHash $document.Content.Text
    normalized_content_length = $normalizedContentText.Length
    paragraph_count = [int]$document.Paragraphs.Count
    table_signatures = @($tables)
    inline_shape_signatures = @($inlineShapes)
    shape_signatures = @($shapes)
    omath_count = $omathCount
    profile_engine = ("Microsoft Word COM " + $word.Version)
  } | ConvertTo-Json -Depth 6 -Compress
} finally {
  if ($null -ne $document) { try { $document.Close(0) } catch { } }
  if ($null -ne $word) { try { $word.Quit() } catch { } }
}
'''
    completed = _powershell(
        script, environment={"CONTROLLED_DOC_PROFILE_SOURCE": str(document_path)}
    )
    try:
        profile = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WordComProfileError(
            "word_com_profile_failed:" + (completed.stderr or completed.stdout or str(completed.returncode)).strip()
        ) from exc
    # Word 2007's COM server may return a non-zero process status after emitting a
    # complete profile because its out-of-process shutdown races the shell.  The
    # JSON profile is the authoritative successful operation record; malformed or
    # absent JSON remains a hard failure above.
    if not isinstance(profile, dict) or profile.get("schema") != "word-com-readonly-profile-v1":
        raise WordComProfileError("word_com_profile_invalid_shape")
    return profile


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        sys.stderr.write("usage: word_com_document_profile.py <document.doc|document.docx>\n")
        return 2
    try:
        print(json.dumps(profile_document(argv[0]), ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, WordComProfileError) as exc:
        sys.stderr.write(f"word_com_profile_blocked:{type(exc).__name__}:{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
