"""Read-only structured Word COM extraction for source-content database import."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

SCHEMA = "word-com-content-extraction-v1"


class WordComContentExtractionError(RuntimeError):
    """Raised when a Word document cannot be extracted without modification."""


def _powershell(script: str, *, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    environment_values = os.environ.copy()
    if environment:
        environment_values.update(environment)
    return subprocess.run(
        ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        timeout=180, env=environment_values,
    )


def _validate(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise WordComContentExtractionError("word_com_content_extraction_schema_invalid")
    if payload.get("file_type") not in {"doc", "docx"}:
        raise WordComContentExtractionError("word_com_content_extraction_file_type_invalid")
    for key in ("source_sha256", "normalized_content_sha256"):
        value = payload.get(key)
        if not isinstance(value, str) or len(value) != 64:
            raise WordComContentExtractionError(f"word_com_content_extraction_{key}_invalid")
    blocks = payload.get("blocks")
    assets = payload.get("assets")
    if not isinstance(blocks, list) or not isinstance(assets, list):
        raise WordComContentExtractionError("word_com_content_extraction_collections_invalid")
    previous_ordinal = -1
    for block in blocks:
        if not isinstance(block, dict):
            raise WordComContentExtractionError("word_com_content_extraction_block_invalid")
        ordinal = block.get("ordinal")
        if not isinstance(ordinal, int) or ordinal <= previous_ordinal:
            raise WordComContentExtractionError("word_com_content_extraction_block_order_invalid")
        previous_ordinal = ordinal
        if block.get("kind") not in {"paragraph", "table_cell"}:
            raise WordComContentExtractionError("word_com_content_extraction_block_kind_invalid")
        if not isinstance(block.get("locator"), dict):
            raise WordComContentExtractionError("word_com_content_extraction_block_locator_invalid")
        for key in ("raw_text", "normalized_text", "raw_sha256", "normalized_sha256"):
            if not isinstance(block.get(key), str):
                raise WordComContentExtractionError(f"word_com_content_extraction_block_{key}_invalid")
        if len(block["raw_sha256"]) != 64 or len(block["normalized_sha256"]) != 64:
            raise WordComContentExtractionError("word_com_content_extraction_block_hash_invalid")
    previous_ordinal = -1
    for asset in assets:
        if not isinstance(asset, dict):
            raise WordComContentExtractionError("word_com_content_extraction_asset_invalid")
        ordinal = asset.get("ordinal")
        if not isinstance(ordinal, int) or ordinal <= previous_ordinal:
            raise WordComContentExtractionError("word_com_content_extraction_asset_order_invalid")
        previous_ordinal = ordinal
        if asset.get("kind") not in {"inline_shape", "shape", "ole_object", "formula", "embedded_media", "unknown"}:
            raise WordComContentExtractionError("word_com_content_extraction_asset_kind_invalid")
        if not isinstance(asset.get("locator"), dict):
            raise WordComContentExtractionError("word_com_content_extraction_asset_locator_invalid")
    return payload


def extract_document(path: str | Path) -> dict[str, Any]:
    """Extract ordered Word content blocks without modifying the original document."""
    source = Path(path).resolve(strict=True)
    if source.suffix.lower() not in {".doc", ".docx"}:
        raise WordComContentExtractionError("unsupported_document_type")
    script = r'''
$DocumentPath = $env:CONTENT_LIBRARY_WORD_SOURCE
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
function Get-Sha256([string]$Value) {
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
  $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  return -join ($hash | ForEach-Object { $_.ToString("x2") })
}
function Normalize-WordText([string]$Value) {
  if ($null -eq $Value) { return "" }
  return (($Value -replace "[\\s\\u00A0\\u0007]", ""))
}
function Trim-WordEndMarkers([string]$Value) {
  if ($null -eq $Value) { return "" }
  return $Value.TrimEnd([char]13, [char]7)
}
function Get-StyleName($Range) {
  try { return [string]$Range.Style.NameLocal } catch { return $null }
}
$word = $null
$document = $null
try {
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  try { $word.AutomationSecurity = 3 } catch { }
  $document = $word.Documents.Open($DocumentPath, $false, $true, $false)
  $blocks = @()
  $blockOrdinal = 0
  for ($paragraphIndex = 1; $paragraphIndex -le $document.Paragraphs.Count; $paragraphIndex++) {
    $paragraph = $document.Paragraphs.Item($paragraphIndex)
    $range = $paragraph.Range
    $rawText = Trim-WordEndMarkers ([string]$range.Text)
    if ($rawText.Length -eq 0) { continue }
    $inTable = $false
    try { $inTable = [bool]$range.get_Information(12) } catch { }
    $blocks += [PSCustomObject]@{
      ordinal = $blockOrdinal
      kind = "paragraph"
      locator = [PSCustomObject]@{
        story_type = [int]$range.StoryType
        paragraph_index = $paragraphIndex
        range_start = [int]$range.Start
        range_end = [int]$range.End
        style = Get-StyleName $range
        in_table = $inTable
      }
      raw_text = $rawText
      normalized_text = Normalize-WordText $rawText
      raw_sha256 = Get-Sha256 $rawText
      normalized_sha256 = Get-Sha256 (Normalize-WordText $rawText)
    }
    $blockOrdinal++
  }
  for ($tableIndex = 1; $tableIndex -le $document.Tables.Count; $tableIndex++) {
    $table = $document.Tables.Item($tableIndex)
    # Table.Rows is inaccessible for legitimate vertically merged Word cells.
    # Enumerate the table range's cells instead; each emitted block remains range-bound.
    $cellOrdinal = 1
    foreach ($cell in $table.Range.Cells) {
      $range = $cell.Range
      $rawText = Trim-WordEndMarkers ([string]$range.Text)
      if ($rawText.Length -gt 0) {
        $rowIndex = $null
        $columnIndex = $null
        try { $rowIndex = [int]$cell.RowIndex } catch { }
        try { $columnIndex = [int]$cell.ColumnIndex } catch { }
        $blocks += [PSCustomObject]@{
          ordinal = $blockOrdinal
          kind = "table_cell"
          locator = [PSCustomObject]@{
            story_type = [int]$range.StoryType
            table_index = $tableIndex
            row_index = $rowIndex
            cell_index = $cellOrdinal
            column_index = $columnIndex
            range_start = [int]$range.Start
            range_end = [int]$range.End
          }
          raw_text = $rawText
          normalized_text = Normalize-WordText $rawText
          raw_sha256 = Get-Sha256 $rawText
          normalized_sha256 = Get-Sha256 (Normalize-WordText $rawText)
        }
        $blockOrdinal++
      }
      $cellOrdinal++
    }
  }
  $assets = @()
  $assetOrdinal = 0
  for ($index = 1; $index -le $document.InlineShapes.Count; $index++) {
    $shape = $document.InlineShapes.Item($index)
    $oleClass = $null
    try { $oleClass = [string]$shape.OLEFormat.ClassType } catch { }
    $assets += [PSCustomObject]@{
      ordinal = $assetOrdinal
      kind = if ($null -ne $oleClass) { "ole_object" } else { "inline_shape" }
      locator = [PSCustomObject]@{
        inline_shape_index = $index
        type = [int]$shape.Type
        range_start = [int]$shape.Range.Start
        range_end = [int]$shape.Range.End
        width = [math]::Round([double]$shape.Width, 3)
        height = [math]::Round([double]$shape.Height, 3)
        ole_class_type = $oleClass
      }
    }
    $assetOrdinal++
  }
  for ($index = 1; $index -le $document.Shapes.Count; $index++) {
    $shape = $document.Shapes.Item($index)
    $anchorStart = $null
    $anchorEnd = $null
    try { $anchorStart = [int]$shape.Anchor.Start; $anchorEnd = [int]$shape.Anchor.End } catch { }
    $assets += [PSCustomObject]@{
      ordinal = $assetOrdinal
      kind = "shape"
      locator = [PSCustomObject]@{
        shape_index = $index
        type = [int]$shape.Type
        anchor_start = $anchorStart
        anchor_end = $anchorEnd
        width = [math]::Round([double]$shape.Width, 3)
        height = [math]::Round([double]$shape.Height, 3)
      }
    }
    $assetOrdinal++
  }
  for ($index = 1; $index -le $document.OMaths.Count; $index++) {
    $math = $document.OMaths.Item($index)
    $range = $math.Range
    $assets += [PSCustomObject]@{
      ordinal = $assetOrdinal
      kind = "formula"
      locator = [PSCustomObject]@{
        equation_index = $index
        range_start = [int]$range.Start
        range_end = [int]$range.End
      }
    }
    $assetOrdinal++
  }
  $fileBytes = [System.IO.File]::ReadAllBytes($DocumentPath)
  $fileHash = -join ([System.Security.Cryptography.SHA256]::Create().ComputeHash($fileBytes) | ForEach-Object { $_.ToString("x2") })
  $normalized = Normalize-WordText ([string]$document.Content.Text)
  [PSCustomObject]@{
    schema = "word-com-content-extraction-v1"
    file_type = [System.IO.Path]::GetExtension($DocumentPath).TrimStart('.').ToLowerInvariant()
    source_sha256 = $fileHash
    normalized_content_sha256 = Get-Sha256 $normalized
    engine_id = "Microsoft Word COM"
    engine_version = [string]$word.Version
    blocks = @($blocks)
    assets = @($assets)
  } | ConvertTo-Json -Depth 8 -Compress
} finally {
  if ($null -ne $document) { try { $document.Close(0) } catch { } }
  if ($null -ne $word) { try { $word.Quit() } catch { } }
}
'''
    completed = _powershell(script, environment={"CONTENT_LIBRARY_WORD_SOURCE": str(source)})
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        detail = (completed.stderr or completed.stdout or str(completed.returncode)).strip()
        raise WordComContentExtractionError("word_com_content_extraction_failed:" + detail) from exc
    if completed.returncode not in {0, 1}:
        raise WordComContentExtractionError("word_com_content_extraction_process_failed")
    return _validate(payload)
