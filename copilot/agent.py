"""Root router. Two composition modes, selectable for the D1.4 experiment (env ROUTER_MODE):

  transfer   root --transfer--> m365_agent | catalog_agent        (LLM-driven handoff; the callee OWNS the turn)
  agenttool  root --transfer--> m365_agent  (+ catalog via AgentTool: called like a function, control RETURNS)
             root --transfer--> catalog_agent                        (pure catalog questions)

Rule of thumb: transfer = "you take over the conversation"; AgentTool = "do this sub-task and report back".
"""
import os

from google.adk.agents import Agent
from google.adk.tools import AgentTool

from copilot.catalog_agent import build_catalog_agent
from copilot.m365_agent import build_m365_agent, build_m365_toolset

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
    extra = [AgentTool(agent=build_catalog_agent("catalog_lookup"))] if mode == "agenttool" else []
    return Agent(
        name="copilot",
        model=os.getenv("MODEL", "gemini-3.8-flash"),
        description="Beauty Ops Copilot router.",
        instruction=_ROUTER_RULES[mode],
        sub_agents=[
            build_m365_agent(toolset=toolset or build_m365_toolset(), extra_tools=extra),
            build_catalog_agent(),
        ],
    )


root_agent = build_root()
