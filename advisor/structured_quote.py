"""Enterprise Structured Quote Generator for Google ADK 2.13.

Demonstrates the expert ADK SequentialAgent pattern:
1. Agent 1 (QuoteCollectorAgent): Calls deterministic calculation tools (e.g. estimate_quantity).
2. Agent 2 (QuoteFormatterAgent): Stateless formatter (include_contents="none") that strictly enforces
   a Pydantic output_schema (DevisConfiguratorSchema).

Critical architectural principle:
In Gemini/ADK, an agent with an explicit output_schema cannot simultaneously use function-calling tools
in standard mode. The SequentialAgent pattern decouples tool execution from structured JSON formatting.
"""
from __future__ import annotations

import os
from typing import Any, List, Optional
from pydantic import BaseModel, Field

from google.adk.agents import Agent, SequentialAgent
from google.adk.models import Gemini
from google.genai import types

from advisor.tools import estimate_quantity


# ---------------------------------------------------------------------------
# Strict Pydantic Output Schemas (e.g. Côté Salle de Bain / Retail Beauty)
# ---------------------------------------------------------------------------

class QuoteLineItem(BaseModel):
    sku: str = Field(description="Code article ou référence SKU")
    designation: str = Field(description="Nom ou désignation du produit ou matériel")
    quantity: int = Field(ge=1, description="Nombre d'unités chiffrées")
    unit_price_eur: float = Field(ge=0.0, description="Prix unitaire remisé ou standard en euros")
    total_eur: float = Field(ge=0.0, description="Total de la ligne en euros")


class QuoteOptions(BaseModel):
    installation_included: bool = Field(default=False, description="Option pose ou accompagnement inclus")
    warranty_months: int = Field(default=24, description="Durée de garantie en mois")
    estimated_delivery_days: int = Field(default=3, description="Délai indicatif de mise à disposition")


class DevisConfiguratorSchema(BaseModel):
    """Schéma de devis structuré et déterministe pour validation avant commande."""
    quote_id: str = Field(description="Identifiant unique du devis généré (format DEV-XXXXXX)")
    customer_need: str = Field(description="Synthèse du besoin client (ex: cure 90 jours ou rénovation vasque)")
    lines: List[QuoteLineItem] = Field(description="Lignes détaillées chiffrées")
    subtotal_eur: float = Field(ge=0.0, description="Sous-total hors remise en euros")
    discount_eur: float = Field(ge=0.0, default=0.0, description="Économie ou remise appliquée")
    total_eur: float = Field(ge=0.0, description="Total net à payer en euros")
    options: QuoteOptions = Field(default_factory=QuoteOptions, description="Options de pose et garanties")
    legal_disclaimer: str = Field(
        default="Devis estimatif sous réserve de validation par un conseiller en magasin.",
        description="Mentions légales obligatoires"
    )


# ---------------------------------------------------------------------------
# SequentialAgent Pipeline Factory
# ---------------------------------------------------------------------------

def create_quote_pipeline(model_name: Optional[str] = None) -> SequentialAgent:
    """Builds the 2-step SequentialAgent pipeline for deterministic, schema-enforced quotes."""
    target_model = model_name or os.getenv("MODEL", "gemini-3.8-flash")

    # Step 1: Collector Agent with tools (no output_schema)
    collector = Agent(
        name="quote_collector",
        model=Gemini(model=target_model),
        tools=[estimate_quantity],
        description="Collecte les quantités et prix via les outils de calcul déterministes.",
        instruction="""Tu es l'agent collecteur. Reçois le besoin client (ex: 90 jours avec LUM-SER-001).
Utilise TOUJOURS estimate_quantity pour calculer les quantités nécessaires et le coût exact.
Transmets dans ta réponse les détails bruts (SKU, nombre de flacons/unités, sous-total, économie, total).
Ne promets aucun prix sans résultat d'outil.
""",
    )

    # Step 2: Formatter Agent without tools, with strict output_schema
    formatter = Agent(
        name="quote_formatter",
        model=Gemini(model=target_model),
        tools=[],  # Strictly NO tools to avoid tool/schema conflict
        output_schema=DevisConfiguratorSchema,
        include_contents="none",  # Do not bloat context with intermediate thought chains
        description="Formate les données brutes dans le schéma Pydantic DevisConfiguratorSchema.",
        instruction="""Tu es le formateur de devis certifié.
À partir des informations brutes calculées par l'agent précédent, produis UNIQUEMENT le devis JSON
conforme au schéma DevisConfiguratorSchema.
Génère un quote_id préfixé par 'DEV-' suivi de 6 chiffres/lettres majuscules.
N'invente aucun prix, reprends fidèlement les montants calculés.
""",
    )

    return SequentialAgent(
        name="quote_sequential_pipeline",
        sub_agents=[collector, formatter],
        description="Pipeline séquentiel ADK garantissant calcul d'outils puis sortie JSON stricte Pydantic.",
    )


def extract_event_actions_state_deltas(events: list[Any]) -> dict[str, Any]:
    """Helper illustrating ADK Event & EventActions internal event-driven architecture.

    Demonstrates how state updates in tool_context are encapsulated in EventActions(state_delta=...)
    and committed atomically by SessionService.
    """
    accumulated_deltas: dict[str, Any] = {}
    for ev in events:
        actions = getattr(ev, "actions", None)
        if actions:
            delta = getattr(actions, "state_delta", None)
            if delta and isinstance(delta, dict):
                accumulated_deltas.update(delta)
    return accumulated_deltas

