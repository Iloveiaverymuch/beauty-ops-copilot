"""Builds evals/core/copilot_core.evalset.json with ADK's own pydantic models (schema-valid by construction).

Each case = one user turn, the EXPECTED tool trajectory (names only; matched IN_ORDER with ignore_args, so
extra calls are allowed but the key calls must happen in that order) and a short reference answer.
Trajectory is where PROVENANCE is enforced: e.g. a stated budget requires catalog_lookup/get_campaign,
not an email that mentions a number (D1.4 bug).

Run: uv run python evals/build_evalset.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from google.adk.evaluation.eval_case import EvalCase, IntermediateData, Invocation, SessionInput  # noqa: E402
from google.adk.evaluation.eval_set import EvalSet  # noqa: E402
from google.genai import types  # noqa: E402

T = "transfer_to_agent"
CASES = [
    # id, category, prompt, expected trajectory (in order, extras allowed), reference answer
    ("cat_price", "catalog", "What's the price of GAR-SER-002?", [T, "search_products"],
     "GAR-SER-002 (Niacinamide 10% Pore Serum 30ml) costs EUR 11.50."),
    ("cat_ingredient", "catalog", "Which Garnier serums contain niacinamide?", [T, "search_products"],
     "Two Garnier serums contain niacinamide: GAR-SER-001 Vitamin C Brightening Serum (EUR 12.90) and "
     "GAR-SER-002 Niacinamide 10% Pore Serum (EUR 11.50)."),
    ("cat_budget", "catalog", "What's the Q4 Garnier campaign budget?", [T, "get_campaign"],
     "The Garnier Glow Q4 campaign (CMP-2026-Q4-GAR) has a budget of EUR 450,000."),
    ("m365_calendar", "m365", "What meetings do I have on Thursday 5 November 2026?", [T, "list_events"],
     "On Thursday 5 November 2026 you have the Garnier Glow Q4 steerco (evt-001) from 14:00 to 15:00 on Teams."),
    ("m365_files", "m365", "Find the Retinol Starter brief.", [T, "search_files"],
     "The brief is Retinol_Starter_brief_v2.docx on the CeraVe SharePoint site (file-002)."),
    ("m365_mail", "m365", "What did Karim say about the Sun All Year pharmacy listing?", [T, "search_mail"],
     "Karim (msg-002) said the pharmacy listing for LRP-SPF-001 slips one week to 2026-11-17; the Instagram "
     "flight is unchanged."),
    ("mixed_write", "mixed",
     "Draft an email to sophie.martin@loreal.com summarizing the Q4 Garnier campaign: budget, channels and hero SKUs.",
     [T, "catalog_lookup", "create_draft"],
     "I created a draft to sophie.martin@loreal.com with the Garnier Glow Q4 campaign (CMP-2026-Q4-GAR): budget "
     "EUR 450,000, channels TikTok and retail media, hero SKUs GAR-SER-001 and GAR-SER-002."),
    ("mixed_read", "mixed",
     "Check Sophie's latest email about Garnier and tell me the prices of the hero SKUs she mentions.",
     [T, "search_mail", "catalog_lookup"],
     "Sophie's email (msg-001) mentions GAR-SER-001 (EUR 12.90) and GAR-SER-002 (EUR 11.50)."),
    ("mixed_provenance", "mixed",
     "Finance mentioned a Garnier Q4 budget in an email. Is it the same as the official campaign budget?",
     [T, "search_mail", "catalog_lookup"],
     "Finance's email (msg-008) asks whether Garnier Glow Q4 stays at EUR 450k or moves to EUR 420k; the official "
     "campaign budget (CMP-2026-Q4-GAR) is EUR 450,000, so it matches unless the re-forecast is approved."),
    ("write_simple", "write", "Draft an email to sophie.martin@loreal.com saying 'steerco deck coming tonight'.",
     [T, "create_draft"], "I created a draft to sophie.martin@loreal.com saying the steerco deck is coming tonight."),
]


def _content(role: str, text: str) -> types.Content:
    return types.Content(role=role, parts=[types.Part(text=text)])


def build() -> EvalSet:
    cases = []
    for cid, cat, prompt, traj, ref in CASES:
        inv = Invocation(
            invocation_id=f"{cid}-1",
            user_content=_content("user", prompt),
            final_response=_content("model", ref),
            intermediate_data=IntermediateData(tool_uses=[types.FunctionCall(name=n, args={}) for n in traj]),
        )
        cases.append(EvalCase(eval_id=f"{cat}__{cid}", conversation=[inv],
                              session_input=SessionInput(app_name="copilot", user_id="eval", state={})))
    return EvalSet(eval_set_id="copilot_core", name="copilot core behaviours",
                   description="Routing, provenance and tool use; safety lives in scripts/eval_suite.py",
                   eval_cases=cases)


if __name__ == "__main__":
    out = ROOT / "evals" / "core" / "copilot_core.evalset.json"
    out.write_text(build().model_dump_json(indent=1, exclude_none=True, by_alias=False))
    print(f"wrote {out} ({len(CASES)} cases)")
