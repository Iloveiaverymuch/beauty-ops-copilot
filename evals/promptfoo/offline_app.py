"""Deterministic fake agent behind the REAL /chat/sync route, solely to smoke-test Promptfoo wiring.

SESSIONS=memory BACKEND_DATA_DIR=/tmp/beauty-eval uv run uvicorn evals.promptfoo.offline_app:app --port 8011
Passing this stub proves the HTTP/config/assertion wiring, NOT live Gemini behavior or RAG quality.
"""
import json
from pathlib import Path

from advisor.tools import card, estimate
from backend.app import ENGINES, app

PRODUCT = json.loads((Path(__file__).resolve().parents[2] / "advisor/data/products.json").read_text())[0]


class FixtureEngine:
    name = "offline-fixture"

    async def stream(self, thread_id, message):
        name, result = "", {}
        if "non ouvert" in message:
            name = "search_knowledge"
            result = {"status": "ok", "passages": [{"citation": "pol-retours#Produits non ouverts",
                       "title": "Retours et remboursements", "section": "Produits non ouverts"}]}
            text = "Vous disposez de 30 jours [pol-retours#Produits non ouverts]."
        elif "90 jours" in message:
            name, result = "estimate_quantity", {"status": "ok", **estimate(PRODUCT, 90)}
            text = "Deux flacons pour 19,80 euros."
        elif "peau grasse" in message:
            name, result = "search_products", {"status": "ok", "products": [card(PRODUCT)]}
            text = "Voici un sérum adapté."
        elif "CLM-000000" in message:
            name, result = "get_claim_status", {"claim_id": "CLM-000000", "status": "unknown_claim"}
            text = "Je ne trouve pas ce dossier."
        elif message == "Je cherche un sérum":
            text = "Quel est votre type de peau ?"
        else:
            text = "Je ne peux pas vous aider sur ce sujet."
        if name:
            yield {"type": "tool_call", "name": name, "args": {}}
            yield {"type": "tool_result", "name": name, "response": result}
        yield {"type": "text", "delta": text}
        yield {"type": "usage", "tokens_in": 0, "tokens_out": 0}


ENGINES["adk"] = FixtureEngine()
