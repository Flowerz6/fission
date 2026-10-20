# Shelved guides

Guides here are **excluded from the active corpus** (`load_guides` globs
`guides/*.yaml` non-recursively, so a subdirectory drops out of every run,
export, and dashboard listing). Move a file back up to `guides/` to re-activate.

## calendar_* — blocked on surface (no Gemini side panel in Calendar)

Shelved 2026-09-28. Google never shipped the Gemini **side panel** to Google
Calendar — it exists in Gmail, Docs, Sheets, Slides and Drive, but not Calendar
(confirmed on a live account that shows the panel everywhere else). There is no
toggle, licence, or selector that brings it back, so any guide whose
`type_prompt` drives a Calendar side panel cannot run.

Affected:
- `calendar_event_deaddrop_exfil` (2026 Miggo event-creation dead-drop)
- `calendar_event_injection`
- `calendar_conditional_trigger_exfil`
- `calendar_covert_attendee_exfil`

The UI-mode **plant** these use (`calendar_ui_create_event`, editor-driven, no
Calendar API) works and is kept — the block is purely the drive surface.

### If the technique is worth reviving
The only surface where Gemini can create/modify calendar events is the **Gemini
app** (`gemini.google.com`) with the `@Google Calendar` extension — a different
target (`gemini_app`), not the Calendar side panel. That's a deliberate new
adapter, not a selector heal. The docs analog `docs_deaddrop_selfwrite_exfil`
tests the same self-write dead-drop idea on a working surface in the meantime.
