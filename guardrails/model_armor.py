"""Google Cloud Model Armor integration & local sanitization fallback.

Defense-in-depth architecture:
- Layer 1 (Model Armor): Edge/Prompt-level sanitization against prompt injection,
  jailbreaks, toxic content, and PII leakage before the model is invoked.
- Layer 2 (ADK Native Callbacks): Deterministic business logic guardrails on tool calls
  (e.g., hygiene policy blocking refunds on opened items without adverse reaction).
- Layer 3 (ADK Governance Plugin & HITL): Hard write budgets, allowed email recipient
  domains, and Human-In-The-Loop review_claim confirmation.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from opentelemetry import trace

log = logging.getLogger("advisor.guardrails.model_armor")
tracer = trace.get_tracer("advisor.guardrails.model_armor")

# Standard prompt injection and PII patterns for local fallback / testing
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(DAN|unrestricted|jailbroken|godmode)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?safety\s+guidelines", re.IGNORECASE),
    re.compile(r"dump\s+(all\s+)?(system\s+prompts?|instructions?)", re.IGNORECASE),
    re.compile(r"reveal\s+your\s+(secret|internal|system)\s+instructions?", re.IGNORECASE),
]

_PII_PATTERNS = [
    re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b"),  # Visa, MC, Amex
]


def sanitize_prompt(text: str) -> dict[str, Any]:
    """Inspects and sanitizes user prompt before it enters the ADK runner.

    Returns dict with keys:
      - safe: bool
      - action: 'allow' | 'block'
      - filter_type: str | None
      - message: str
    """
    if os.getenv("MODEL_ARMOR_ENABLED", "1").lower() in ("0", "false", "off"):
        return {"safe": True, "action": "allow", "filter_type": None, "message": ""}

    with tracer.start_as_current_span("guardrails.model_armor.sanitize") as span:
        template = os.getenv("MODEL_ARMOR_TEMPLATE")
        if template:
            try:
                from google.cloud import modelarmor_v1  # type: ignore
                client = modelarmor_v1.ModelArmorClient()
                req = modelarmor_v1.SanitizeUserPromptRequest(
                    name=template,
                    user_prompt_data=modelarmor_v1.UserPromptData(text=text),
                )
                resp = client.sanitize_user_prompt(request=req)
                if resp.sanitization_result.filter_match_state == modelarmor_v1.FilterMatchState.MATCH_FOUND:
                    span.set_attributes({
                        "guardrail.model_armor.blocked": True,
                        "guardrail.model_armor.filter_type": "vertex_model_armor",
                    })
                    return {
                        "safe": False,
                        "action": "block",
                        "filter_type": "vertex_model_armor",
                        "message": "Requête bloquée par Google Cloud Model Armor (injection ou contenu non conforme).",
                    }
            except Exception as e:
                log.warning("Vertex Model Armor API call failed, falling back to local inspection: %s", e)

        # Local deterministic fallback (offline gate & demo safety)
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                span.set_attributes({
                    "guardrail.model_armor.blocked": True,
                    "guardrail.model_armor.filter_type": "prompt_injection",
                })
                return {
                    "safe": False,
                    "action": "block",
                    "filter_type": "prompt_injection",
                    "message": "Requête interceptée par Model Armor : tentative d'injection de prompt détectée.",
                }

        for pattern in _PII_PATTERNS:
            if pattern.search(text):
                span.set_attributes({
                    "guardrail.model_armor.blocked": True,
                    "guardrail.model_armor.filter_type": "pii_leakage",
                })
                return {
                    "safe": False,
                    "action": "block",
                    "filter_type": "pii_leakage",
                    "message": "Requête interceptée par Model Armor : présence de données de paiement non chiffrées (PII).",
                }

        span.set_attributes({
            "guardrail.model_armor.blocked": False,
            "guardrail.model_armor.status": "passed",
        })
        return {"safe": True, "action": "allow", "filter_type": None, "message": ""}
