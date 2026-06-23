"""Adapters: concrete DocumentTargets and PyRIT bridges.

Two flavours live here:

* PyRIT prompt-target / converter / scorer adapters that bridge advtest
  abstractions to PyRIT's orchestration (stubs).
* Self-contained DocumentTarget adapters that wrap a Provider with
  extra machinery — currently :mod:`advtest_ext.adapters.tool_use_agent`,
  which exposes a four-tool ReAct loop so the
  ``unauthorized_action`` seed behaviours are testable end-to-end.
"""

__citations__: list[str] = [
    "pyrit_microsoft",
    "zhan2024injecagent",  # tool-using agent threat model (tool_use_agent)
]
