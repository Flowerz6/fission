"""Extract the assembled response text from an intercepted Workspace
Gemini ``streamGenerate`` capture.

The implementation now lives with the capture code, in
``advtest_ext.adapters._ui_mode_common.response_assembly`` — so the
ui_mode invokers can use the SAME assembly to persist a complete
``raw_response`` (not Playwright's truncated mid-stream read). This module
re-exports it under the historical name ``extract_full_response`` so the
dashboard and existing callers keep working unchanged.
"""

from __future__ import annotations

from advtest_ext.adapters._ui_mode_common.response_assembly import (
    assemble_full_response as extract_full_response,
)

__all__ = ["extract_full_response"]
