"""Root router. Two composition modes, selectable via env ROUTER_MODE:

  transfer   root --transfer--> workspace_agent | catalog_agent        (LLM-driven handoff; the callee OWNS the turn)
  agenttool  root --transfer--> workspace_agent  (+ catalog via AgentTool: called like a function, control RETURNS)
             root --transfer--> catalog_agent                          (pure catalog questions)

Rule of thumb: transfer = "you take over the conversation"; AgentTool = "do this sub-task and report back".
"""
import os

from google.adk.agents import Agent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App
from google.adk.tools import AgentTool, FunctionTool

from copilot.catalog_agent import build_catalog_agent
from copilot.governance import GovernancePlugin
from copilot.workspace_agent import build_workspace_agent, build_workspace_toolsets
from copilot.tools.operations import list_pending_claims, review_claim
from copilot.model import build_generate_config, build_model
from copilot.observability import configure_audit_logging, setup_telemetry

_ROUTER_RULES = {
    "transfer": (
        "You are a router. You never answer yourself and never call tools.\n"
        "- Workspace requests (Gmail, meetings, Google Calendar, Google Drive files, drafting emails, claims arbitration) -> transfer to workspace_agent.\n"
        "- Product or campaign questions (SKUs, ingredients, prices, budgets) -> transfer to catalog_agent."
    ),
    "agenttool": (
        "You are a router. You never answer yourself and never call tools.\n"
        "- Anything involving the workspace (Gmail, meetings, Google Calendar, Google Drive files, drafting emails, claims arbitration), "
        "INCLUDING requests that also need product or campaign facts -> transfer to workspace_agent (it can look up the catalog and arbitrate claims itself).\n"
        "- Pure product or campaign questions with no workspace action -> transfer to catalog_agent."
    ),
}


setup_telemetry()  # before any agent runs; no-op unless TELEMETRY / OTEL_EXPORTER_OTLP_* are set
configure_audit_logging()  # Cloud Run: governance audit = pure JSON lines -> structured Cloud Logging entries


def _cache_config() -> ContextCacheConfig | None:
    """CONTEXT_CACHE=1: per-agent Gemini context cache (ADK warned: every transfer re-sends the prompt uncached).
    min_tokens avoids caching tiny prefixes (caching has a storage cost and a minimum size)."""
    if os.getenv("CONTEXT_CACHE", "0") != "1":
        return None
    return ContextCacheConfig(min_tokens=int(os.getenv("CONTEXT_CACHE_MIN_TOKENS", "2048")), ttl_seconds=1800,
                              cache_intervals=10)


def build_root(mode: str | None = None, toolset=None) -> Agent:
    mode = mode or os.getenv("ROUTER_MODE", "agenttool")
    # include_plugins=True (default): the governance plugin also runs inside the nested catalog run
    extra = [
        *( [AgentTool(agent=build_catalog_agent("catalog_lookup"), include_plugins=True)] if mode == "agenttool" else [] ),
        list_pending_claims,
        FunctionTool(func=review_claim, require_confirmation=True),
    ]
    return Agent(
        name="copilot",
        model=build_model(),
        generate_content_config=build_generate_config("router"),
        description="Beauty Ops Copilot router.",
        instruction=_ROUTER_RULES[mode],
        sub_agents=[
            build_workspace_agent(toolset=toolset or build_workspace_toolsets(), extra_tools=extra),
            build_catalog_agent(),
        ],
    )


def build_app(mode: str | None = None, toolset=None, governance: bool | None = None) -> App:
    """`adk web` / `adk run` pick up `app` before `root_agent`. Plugins live on the App (platform level)."""
    if governance is None:
        governance = os.getenv("GOVERNANCE", "enforce")
    plugins = [] if governance in (False, "off") else [GovernancePlugin(enforce=governance not in ("shadow",))]
    return App(name="copilot", root_agent=build_root(mode, toolset), plugins=plugins,
               context_cache_config=_cache_config())


root_agent = build_root()
app = App(name="copilot", root_agent=root_agent, plugins=build_app().plugins, context_cache_config=_cache_config())
