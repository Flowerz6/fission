"""Tool-using agent adapter — a ``DocumentTarget`` backed by a local
ReAct loop with four simulated tools (send_email, share_document,
create_calendar_event, delete_file).

Threat-model framing follows InjecAgent (zhan2024injecagent), which
established tool-using LLM agents as the canonical IPI threat model.
The four tools mirror the InjecAgent direct-harm + data-exfil action
set narrowed to a Workspace context.

This adapter exists because the Workspace ``api_mode`` adapters (Prompt
11) only summarise — Gemini doesn't actually expose tool calls from a
document-summarisation context. Without a tool-using DocumentTarget,
the four ``unauthorized_action`` seed behaviours are untestable
end-to-end. See FOUNDATIONS.md "Frameworks" for the rationale.
"""

from advtest_ext.adapters.tool_use_agent.agent import (
    DEFAULT_SYSTEM_PROMPT,
    AgentResult,
    ToolUseAgent,
)
from advtest_ext.adapters.tool_use_agent.target import (
    DEFAULT_USER_DOMAIN,
    ToolUseAgentTarget,
    collect_hidden_techniques,
    detect_unauthorized_action,
    linearise_document,
)
from advtest_ext.adapters.tool_use_agent.tools import (
    CREATE_CALENDAR_EVENT,
    DEFAULT_TOOLS,
    DELETE_FILE,
    SEND_EMAIL,
    SHARE_DOCUMENT,
    CallLog,
    CallRecord,
    Tool,
    tool_schemas,
)

__citations__: list[str] = [
    "zhan2024injecagent",  # tool-using agents under IPI
    "greshake2023ipi",  # foundational IPI threat class
    "promptware_nassi2025",  # Workspace tool-action threats (calendar)
    "geminijack_2025",  # Workspace tool-action threats (doc share)
]

__all__ = [
    "CREATE_CALENDAR_EVENT",
    "DEFAULT_SYSTEM_PROMPT",
    "DEFAULT_TOOLS",
    "DEFAULT_USER_DOMAIN",
    "DELETE_FILE",
    "SEND_EMAIL",
    "SHARE_DOCUMENT",
    "AgentResult",
    "CallLog",
    "CallRecord",
    "Tool",
    "ToolUseAgent",
    "ToolUseAgentTarget",
    "collect_hidden_techniques",
    "detect_unauthorized_action",
    "linearise_document",
    "tool_schemas",
]
