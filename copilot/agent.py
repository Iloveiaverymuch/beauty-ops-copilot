"""Root router. Two composition modes, selectable for the D1.4 experiment (env ROUTER_MODE):

  transfer   root --transfer--> m365_agent | catalog_agent        (LLM-driven handoff; the callee OWNS the turn)
  agenttool  root --transfer--> m365_agent  (+ catalog via AgentTool: called like a function, control RETURNS)
             root --transfer--> catalog_agent                        (pure catalog questions)

Rule of thumb: transfer = "you take over the conversation"; AgentTool = "do this sub-task and report back".
"""
import os

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.tools import AgentTool

from copilot.catalog_agent import build_catalog_agent
from copilot.governance import GovernancePlugin
from copilot.m365_agent import build_m365_agent, build_m365_toolsets

_ROUTER_RULES = {
    "transfer": (
        "You are a router. You never answer yourself and never call tools.\n"
        "- Workspace requests (mail, meetings, calendar, files, drafting emails) -> transfer to m365_agent.\n"
        "- Product or campaign questions (SKUs, ingredients, prices, budgets) -> transfer to catalog_agent."
    ),
    "agenttool": (
        "You are a router. You never answer yourself and never call tools.\n"
        "- Anything involving the workspace (mail, meetings, files, drafting emails), INCLUDING requests that also "
        "need product or campaign facts -> transfer to m365_agent (it can look up the catalog itself).\n"
        "- Pure product or campaign questions with no workspace action -> transfer to catalog_agent."
    ),
}


def build_root(mode: str | None = None, toolset=None) -> Agent:
    mode = mode or os.getenv("ROUTER_MODE", "agenttool")
    # include_plugins=True (default): the governance plugin also runs inside the nested catalog run
    extra = [AgentTool(agent=build_catalog_agent("catalog_lookup"), include_plugins=True)] if mode == "agenttool" else []
    return Agent(
        name="copilot",
        model=os.getenv("MODEL", "gemini-3.8-flash"),
        description="Beauty Ops Copilot router.",
        instruction=_ROUTER_RULES[mode],
        sub_agents=[
            build_m365_agent(toolset=toolset or build_m365_toolsets(), extra_tools=extra),
            build_catalog_agent(),
        ],
    )


def build_app(mode: str | None = None, toolset=None, governance: bool | None = None) -> App:
    """`adk web` / `adk run` pick up `app` before `root_agent`. Plugins live on the App (platform level)."""
    if governance is None:
        governance = os.getenv("GOVERNANCE", "enforce")
    plugins = [] if governance in (False, "off") else [GovernancePlugin(enforce=governance not in ("shadow",))]
    return App(name="copilot", root_agent=build_root(mode, toolset), plugins=plugins)


root_agent = build_root()
app = App(name="copilot", root_agent=root_agent, plugins=build_app().plugins)
