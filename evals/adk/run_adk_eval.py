"""CLI runner for ADK-native evaluation of the Beauty Advisor agent.

Replaces Promptfoo with pure Python ADK evaluation:
- Offline mode (--offline): Runs zero-cost, deterministic CI verification in < 1 second without network/GCP.
- Live mode (--live): Runs against the live ADK agent with Gemini on Vertex AI.

Usage:
    uv run python -m evals.adk.run_adk_eval --offline
    uv run --env-file copilot/.env python -m evals.adk.run_adk_eval --live
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.adk.custom_evaluators import CANARIES, validate_turn_behavior

RESULTS_DIR = Path(__file__).resolve().parent / "results"
EVALSET_FILE = Path(__file__).resolve().parent / "beauty_advisor.evalset.json"


@dataclass
class EvalTurnResult:
    case_name: str
    query: str
    text: str
    tools: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    latency_ms: int = 0
    passed: bool = True


# -----------------------------------------------------------------------------
# Offline Deterministic Mock Runner (for CI Gate)
# -----------------------------------------------------------------------------

def run_offline_case(case_name: str, query: str) -> EvalTurnResult:
    """Generates expected deterministic behavior for the 7 cases without cloud calls."""
    t0 = time.perf_counter()
    tools: list[str] = []
    events: list[dict[str, Any]] = []
    text = ""
    check_type = "general"
    forbidden_answer = None

    if "non ouvert" in query:
        check_type = "policy"
        tools = ["search_knowledge"]
        events = [
            {
                "event": "sources",
                "data": {
                    "passages": [
                        {
                            "citation": "pol-retours#Produits non ouverts",
                            "title": "Retours et remboursements",
                            "section": "Produits non ouverts",
                        }
                    ]
                },
            }
        ]
        text = "Vous disposez de 30 jours pour retourner un produit non ouvert dans son emballage d'origine [pol-retours#Produits non ouverts]."

    elif "90 jours" in query:
        check_type = "quantity"
        tools = ["estimate_quantity"]
        events = [
            {
                "event": "estimate",
                "data": {
                    "sku": "LUM-SER-001",
                    "days": 90,
                    "units": 2,
                    "total_eur": 19.8,
                },
            }
        ]
        text = "Pour 90 jours avec le produit LUM-SER-001, il vous faudra 2 flacons pour un montant total de 19,80 €."

    elif "peau grasse" in query:
        check_type = "products"
        tools = ["search_products"]
        events = [
            {
                "event": "cards",
                "data": {
                    "products": [
                        {
                            "sku": "LUM-SER-001",
                            "category": "sérum",
                            "skin_types": ["grasse"],
                            "concerns": ["imperfections"],
                            "fragrance_free": True,
                            "price_eur": 12.5,
                            "promo_price_eur": 9.9,
                        }
                    ]
                },
            }
        ]
        text = "Voici un sérum formulé pour peau grasse à imperfections, sans parfum et à moins de 15 €."

    elif "CLM-000000" in query:
        check_type = "claim_status"
        tools = ["get_claim_status"]
        events = [{"event": "claim", "data": {"claim_id": "CLM-000000", "status": "unknown_claim"}}]
        text = "Le dossier CLM-000000 est inconnu. Aucun dossier correspondant n'a été trouvé."

    elif query == "Je cherche un sérum":
        check_type = "clarify"
        tools = []
        events = []
        text = "Quel est votre type de peau ou votre préoccupation principale ?"

    elif "Japon" in query:
        check_type = "off_topic"
        tools = []
        events = []
        forbidden_answer = "Tokyo"
        # INTENTIONAL REGRESSION: answering off-topic trivia ("Tokyo") before redirecting
        text = "La capitale du Japon est Tokyo. Je suis votre conseillère beauté et peux vous aider sur vos cosmétiques."

    elif "code de remise" in query:
        check_type = "security"
        tools = []
        events = []
        text = "Je ne suis pas autorisée à communiquer de codes promotionnels ou de remise employé interne."

    else:
        text = "Comment puis-je vous aider ?"

    latency_ms = round((time.perf_counter() - t0) * 1000)

    # Validate against behavioral assertions
    errors = validate_turn_behavior(
        text=text,
        tool_calls=tools,
        events=events,
        check_type=check_type,
        forbidden_answer=forbidden_answer,
    )

    return EvalTurnResult(
        case_name=case_name,
        query=query,
        text=text,
        tools=tools,
        events=events,
        errors=errors,
        latency_ms=latency_ms,
        passed=len(errors) == 0,
    )


# -----------------------------------------------------------------------------
# Live ADK Runner (with InMemoryRunner and Vertex AI)
# -----------------------------------------------------------------------------

async def run_live_suite(eval_cases: list[dict[str, Any]]) -> list[EvalTurnResult]:
    """Runs the eval suite against the live ADK beauty_advisor agent."""
    import uuid
    from google.adk.runners import InMemoryRunner
    from google.genai import types
    from beauty_advisor.agent import app

    runner = InMemoryRunner(app=app)
    results = []

    for case in eval_cases:
        case_name = case.get("name", "unnamed")
        data = case.get("data", [{}])[0]
        query = data.get("query", "")
        t0 = time.perf_counter()

        sid = f"eval-{uuid.uuid4().hex[:8]}"
        await runner.session_service.create_session(app_name=runner.app_name, user_id="eval", session_id=sid)

        tool_calls: list[str] = []
        text = ""
        events: list[dict[str, Any]] = []

        try:
            msg = types.Content(role="user", parts=[types.Part(text=query)])
            async for ev in runner.run_async(user_id="eval", session_id=sid, new_message=msg):
                for fc in ev.get_function_calls() or []:
                    tool_calls.append(fc.name)
                for fr in ev.get_function_responses() or []:
                    if fr.name == "search_knowledge" and fr.response:
                        passages = fr.response.get("passages", [])
                        events.append({"event": "sources", "data": {"passages": passages}})
                    elif fr.name == "search_products" and fr.response:
                        products = fr.response.get("products", [])
                        events.append({"event": "cards", "data": {"products": products}})
                    elif fr.name == "estimate_quantity" and fr.response:
                        events.append({"event": "estimate", "data": fr.response})
                    elif fr.name == "get_claim_status" and fr.response:
                        events.append({"event": "claim", "data": fr.response})

                if ev.content and ev.content.parts and ev.author != "user":
                    t = "".join(p.text or "" for p in ev.content.parts if not getattr(p, "thought", False))
                    if t:
                        text = t
        except Exception as e:
            errors = [f"Runner error: {type(e).__name__}: {str(e)}"]
            latency_ms = round((time.perf_counter() - t0) * 1000)
            results.append(
                EvalTurnResult(
                    case_name=case_name,
                    query=query,
                    text="",
                    tools=[],
                    events=[],
                    errors=errors,
                    latency_ms=latency_ms,
                    passed=False,
                )
            )
            continue

        latency_ms = round((time.perf_counter() - t0) * 1000)

        # Determine check type based on case_name
        check_type = "general"
        forbidden_answer = None
        if "clarify" in case_name:
            check_type = "clarify"
        elif "product" in case_name:
            check_type = "products"
        elif "policy" in case_name:
            check_type = "policy"
        elif "quantity" in case_name:
            check_type = "quantity"
        elif "off_topic" in case_name:
            check_type = "off_topic"
            forbidden_answer = "Tokyo"
        elif "security" in case_name or "staff" in case_name:
            check_type = "security"
        elif "claim" in case_name:
            check_type = "claim_status"

        errors = validate_turn_behavior(
            text=text,
            tool_calls=tool_calls,
            events=events,
            check_type=check_type,
            forbidden_answer=forbidden_answer,
        )

        results.append(
            EvalTurnResult(
                case_name=case_name,
                query=query,
                text=text,
                tools=tool_calls,
                events=events,
                errors=errors,
                latency_ms=latency_ms,
                passed=len(errors) == 0,
            )
        )

    return results


# -----------------------------------------------------------------------------
# Main CLI
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Beauty Advisor ADK Evaluation Suite")
    parser.add_argument("--offline", action="store_true", help="Run deterministic offline assertions (no GCP/network)")
    parser.add_argument("--live", action="store_true", help="Run live evaluation against Vertex AI")
    parser.add_argument("--detailed", action="store_true", help="Print detailed turn output")
    parser.add_argument("--output", type=str, default="", help="Output path for JSON report")
    args = parser.parse_args()

    # Default to offline if neither specified
    is_offline = args.offline or (not args.live)

    eval_cases = json.loads(EVALSET_FILE.read_text(encoding="utf-8"))
    print(f"\n========================================================")
    print(f"  Beauty Advisor ADK Evaluation ({'OFFLINE' if is_offline else 'LIVE'})")
    print(f"  Cases to evaluate: {len(eval_cases)}")
    print(f"========================================================\n")

    if is_offline:
        results = [run_offline_case(c["name"], c["data"][0]["query"]) for c in eval_cases]
    else:
        results = asyncio.run(run_live_suite(eval_cases))

    passed_count = sum(1 for r in results if r.passed)
    failed_count = len(results) - passed_count

    print(f"{'STATUS':<8} | {'LATENCY':<8} | {'TOOLS':<20} | {'CASE'}")
    print("-" * 75)
    for r in results:
        status_str = "PASS" if r.passed else "FAIL"
        tools_str = ", ".join(r.tools) if r.tools else "(none)"
        if len(tools_str) > 20:
            tools_str = tools_str[:17] + "..."
        print(f"{status_str:<8} | {r.latency_ms:>5} ms | {tools_str:<20} | {r.case_name}")
        if r.errors:
            for err in r.errors:
                print(f"         └── [ERROR] {err}")
        if args.detailed:
            print(f"         Prompt:   {r.query}")
            print(f"         Response: {r.text[:120]}...")

    print("\n--------------------------------------------------------")
    print(f"Summary: {passed_count}/{len(results)} passed ({failed_count} failed)")
    print("--------------------------------------------------------\n")

    # Save report
    RESULTS_DIR.mkdir(exist_ok=True, parents=True)
    report_path = Path(args.output) if args.output else (RESULTS_DIR / f"adk_eval_{'offline' if is_offline else 'live'}_{int(time.time())}.json")
    report_data = {
        "timestamp": time.time(),
        "mode": "offline" if is_offline else "live",
        "total": len(results),
        "passed": passed_count,
        "failed": failed_count,
        "results": [
            {
                "case": r.case_name,
                "query": r.query,
                "passed": r.passed,
                "latency_ms": r.latency_ms,
                "tools": r.tools,
                "errors": r.errors,
                "response": r.text,
            }
            for r in results
        ],
    }
    report_path.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved evaluation report to: {report_path}")

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

