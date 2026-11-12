"""Google Agent2Agent (A2A) Protocol integration.

Exposes the Beauty Ops / Bathroom Advisor as an interoperable A2A service.
Allows remote agents built in any framework (ADK, LangGraph, TypeScript, Go)
to discover and communicate with this agent via standardized AgentCards.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class A2ACapability:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


@dataclass(frozen=True)
class AgentCard:
    """Standardized Agent Card for Agent-to-Agent (A2A) discovery and invocation."""
    agent_id: str
    name: str
    version: str
    description: str
    framework: str = "google-adk-2.13"
    auth_schemes: list[str] = field(default_factory=lambda: ["google_oauth2", "cloud_run_s2s"])
    input_modes: list[str] = field(default_factory=lambda: ["text/plain", "application/json", "multipart/form-data"])
    output_modes: list[str] = field(default_factory=lambda: ["text/event-stream", "application/json", "application/pdf"])
    capabilities: list[A2ACapability] = field(default_factory=list)
    endpoint_url: str = "http://127.0.0.1:8000/chat"


def get_advisor_agent_card() -> AgentCard:
    """Generates the A2A AgentCard for the customer-facing advisor."""
    return AgentCard(
        agent_id="retail-advisor-lea-v1",
        name="Léa - Conseillère Retail & Devis",
        version="1.0.0",
        description="Conseillère client experte: RAG agentique sourcé, calcul déterministe de devis, génération de PDF et réclamations avec garde-fous.",
        capabilities=[
            A2ACapability(
                name="grounded_knowledge_search",
                description="Recherche dans la base de connaissances documentaire avec citations exactes.",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                output_schema={"type": "object", "properties": {"answer": {"type": "string"}, "citations": {"type": "array"}}},
            ),
            A2ACapability(
                name="deterministic_quote_generation",
                description="Calcul de quantité et génération d'un devis officiel au format PDF.",
                input_schema={"type": "object", "properties": {"days": {"type": "integer"}, "products": {"type": "array"}}, "required": ["days"]},
                output_schema={"type": "object", "properties": {"pdf_url": {"type": "string"}, "total_eur": {"type": "number"}}},
            ),
            A2ACapability(
                name="customer_claim_arbitration",
                description="Ouverture d'un dossier de réclamation client avec politique d'hygiène déterministe.",
                input_schema={"type": "object", "properties": {"order_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["order_id"]},
                output_schema={"type": "object", "properties": {"claim_id": {"type": "string"}, "status": {"type": "string"}}},
            ),
        ],
    )


def to_a2a_dict() -> dict[str, Any]:
    """Serializes the AgentCard into A2A protocol JSON."""
    return asdict(get_advisor_agent_card())
