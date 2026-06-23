# Google Docs adapter — hiding-technique coverage

This is the support matrix for the 25-technique catalogue (see
`advtest_ext/attacks/indirect_injection/techniques.py`) on the
`GoogleDocsTarget`. It's a deliverable in its own right — the pitch
deck cites it as evidence that we know which techniques actually
land on the real Workspace surface and which don't.

**Auth context.** This adapter runs against personal Gmail in the
current setup, using OAuth2 user mode (`ADVTEST_AUTH_MODE=oauth_user`)
with the `drive.file` scope. Production deployments would swap to a
service account with domain-wide delegation; the auth shim
(`_auth.py`) supports both modes, but the test environment exercises
only `oauth_user`. None of the technique support below depends on
the auth mode — it's all about whether the public Docs / Drive API
exposes the operation.

## Legend

- ✅ **Supported** — the adapter renders this technique faithfully via
  one or more public API calls.
- ⚠️ **Partial** — the technique can be approximated but loses some
  fidelity vs. the published behaviour. Reason noted.
- ❌ **Unsupported** — no public API path; documented future work.

## Matrix

| ID | Technique | Status | Notes / API path |
|----|-----------|:--:|--|
| `white_text` | White text on white bg | ✅ | `updateTextStyle` with `foregroundColor=#FFFFFF` (greshake2023ipi, geminijack_2025). |
| `tiny_font` | Sub-readable font size | ✅ | `updateTextStyle.fontSize` at 1pt. |
| `font_color_near_background` | Near-white text | ✅ | Same as `white_text` with `#FFFEFE`-ish. |
| `text_box_offscreen` | Text box positioned off-page | ❌ | Docs API does not expose text-box entities; they only appear via the editor UI. Workaround would be browser automation in `ui_mode` — out of scope for this prompt. |
| `zero_width_chars` | Zero-width payload chars | ✅ | `insertText` with U+200B etc.; passes through Drive export. |
| `unicode_tag_chars` | Unicode tag (E0000+) hidden chars | ✅ | Same path as `zero_width_chars`. |
| `unicode_homoglyph` | Cyrillic / Greek look-alikes | ✅ | `insertText`; survives export. |
| `comment_thread` | Top-level comment | ✅ | `drive.comments().create`. |
| `comment_reply_thread` | Reply chain on a comment | ✅ | `drive.replies().create` per reply. |
| `footnote` | In-doc footnote | ⚠️ | The Docs API has no `insertFootnote` request as of v1; we render the footnote body as inline text + a marker. Loses the visual "footer marker" but preserves the payload. (promptware_nassi2025) |
| `header_footer` | Page header / footer carrier | ⚠️ | We currently inline header/footer blocks into the body as a simplification — a full implementation needs `createHeader` / `createFooter`. Tracked. |
| `suggested_edit` | Tracked-change carrier | ❌ | Docs API has no public surface for suggesting / accepting / rejecting edits — that lives entirely in the editor UI and the internal Suggestion service. Adapter-side workaround would require `ui_mode`. |
| `table_cell_hidden` | Hidden payload in a table cell | ✅ | `insertTable` + cell content; per-cell formatting via `updateTextStyle`. |
| `image_alt_text` | Alt text on inline image | ✅ | `insertInlineImage` writes `description` (alt) when supplied. |
| `document_metadata` | File-level metadata (title etc.) | ✅ | `drive.files().update(appProperties=...)`. The Drive `appProperties` field is the supported "metadata you control" surface. |
| `citation_block` | Citation-shaped carrier | ✅ | `insertText` with the citation prelude; matches how Docs renders pasted citation blocks. |
| `table_of_contents` | Auto-generated TOC | ⚠️ | The Docs API supports `insertTableOfContents` only in narrow contexts. We emit heading paragraphs that the editor *could* auto-TOC; full TOC injection lives in `ui_mode`. |
| `linked_content` | External / cross-doc link | ✅ | `updateTextStyle` with `link.url`. |
| `encoded_payload` | Base64 / ROT13 inline | ✅ | Content-level — passes through as plain text. |
| `multilingual` | Non-English / mixed-script payload | ✅ | `insertText` is unicode-clean. |
| `role_confusion` | "System: ..." role mimicry | ✅ | Plain text. |
| `tool_format_mimicry` | JSON / tool-call shape mimic | ✅ | Plain text. |
| `markdown_injection` | Raw markdown payload | ✅ | Docs renders markdown literally — preserves the technique's intent. |
| `cross_document_chain` | Linked second doc with payload | ⚠️ | We render the primary doc only; the linked secondary needs a coordinated double-render that the orchestrator owns. Tracked. |
| `image_rendering_exfil` | Image-tag exfiltration via render | ✅ | `insertInlineImage` with the attacker URL; whether Docs auto-fetches at render time is the Workspace-side variable we measure. (promptware_nassi2025, geminijack_2025) |

## Invoke modes

- **`api_mode` (default)**: `drive.files().export(mimeType='text/plain')`
  → Gemini Provider. Reproducible and cheap. The Drive plain-text
  export is the closest publicly-documented analogue to what the
  Workspace AI side panel feeds the model.
- **`ui_mode`**: stub. A full ui_mode requires browser automation
  against `docs.google.com` (Chrome DevTools Protocol or a
  Selenium-style harness), capturing the side-panel response. Worth
  building as a Prompt-13 follow-up — it's the only way to assert
  which techniques the *real* Workspace AI sees, vs. the API-export
  approximation.

## Known gaps (Prompt 13 candidates)

1. `suggested_edit` and `text_box_offscreen` need `ui_mode`.
2. `footnote` and `header_footer` need first-class API rendering
   instead of body-inlining.
3. `cross_document_chain` needs a multi-doc coordinator on the
   adapter side.
4. The `appProperties`-based `document_metadata` path stores values
   as strings; complex types (lists, dicts) are JSON-stringified by
   the adapter — TECHNIQUE_COVERAGE will need to flag this if a
   future hiding technique relies on structured metadata.
