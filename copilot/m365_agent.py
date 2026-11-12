"""Backwards compatibility shim re-exporting from copilot.workspace_agent."""
from copilot.workspace_agent import (  # noqa: F401
    GoogleIdTokenHeader,
    M365_TOOLS,
    READ_TOOLS,
    WORKSPACE_TOOLS,
    WRITE_TOOLS,
    build_m365_agent,
    build_m365_toolset,
    build_m365_toolsets,
    build_workspace_agent,
    build_workspace_toolset,
    build_workspace_toolsets,
    m365_agent,
    workspace_agent,
)

__all__ = [
    "GoogleIdTokenHeader",
    "M365_TOOLS",
    "READ_TOOLS",
    "WORKSPACE_TOOLS",
    "WRITE_TOOLS",
    "build_m365_agent",
    "build_m365_toolset",
    "build_m365_toolsets",
    "build_workspace_agent",
    "build_workspace_toolset",
    "build_workspace_toolsets",
    "m365_agent",
    "workspace_agent",
]
