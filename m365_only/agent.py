# Isolated app for D1.3/D1.5: talk to the M365 agent alone in `adk web` (pick "m365_only").
import os

from google.adk.apps import App

from copilot.governance import GovernancePlugin
from copilot.m365_agent import m365_agent as root_agent

_gov = os.getenv("GOVERNANCE", "enforce")
app = App(name="m365_only", root_agent=root_agent,
          plugins=[] if _gov == "off" else [GovernancePlugin(enforce=_gov != "shadow")])
