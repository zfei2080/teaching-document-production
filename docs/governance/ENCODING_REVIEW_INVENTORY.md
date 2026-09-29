# Encoding Review Inventory

## Scope and Method

This inventory records documents for which this governance review observed a decoding concern. It does not alter suspect historical text. The review used explicit UTF-8 reads for current control documents; default terminal decoding rendered Chinese text as mojibake when `AGENTS.md` was first read without an explicit encoding.

| Affected path | Observed concern | Required review | Status |
| --- | --- | --- | --- |
| `AGENTS.md` before GOVERNANCE-RESET-001 replacement | Default PowerShell decoding displayed mojibake; explicit UTF-8 read recovered readable text. | No forensic repair of the historical version is required here because the allowed replacement creates a new control file. Preserve Git history. | Current file rewritten as new UTF-8 control document. |
| `docs/交接文档.md` | Listed as a historical/compatibility control document; not read or changed in this work order. Encoding state is unverified. | Human/forensic byte and encoding review in a separately approved work order; do not guess repairs. | Pending. |
| `STATE.md` | Listed as a historical/compatibility record; not read or changed in this work order. Encoding state is unverified. | Human/forensic byte and encoding review in a separately approved work order; do not guess repairs. | Pending. |
| Historical completion reports and task history | Not reviewed wholesale; some historical documents may contain mixed-language or terminal-display artifacts. | Inventory exact files and inspect original bytes in a dedicated forensic work order before any correction. | Pending. |

## Required Handling

- Read current Markdown explicitly as UTF-8 during implementation and record any decoding failure.
- Treat terminal display corruption as a display observation until byte-level inspection confirms a stored-file defect.
- Do not rewrite historical records to make them look cleaner. Preserve original bytes and report uncertainty.
- Do not include sensitive source content while performing any future forensic review.

## Fact and Uncertainty Boundary

The default-decoding mojibake observation is a current-review fact. It does not prove that a file's stored bytes are corrupt. The unreviewed paths and historical documents require future human/forensic measurement; repair decisions remain reserved for the user and a dedicated approved work order.
