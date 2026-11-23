"""In-store advisor appointment booking tools for Léa (W2 / SPEC-011).

Design invariants:
- Identity & context (`user:id`, `user:store`, `user:contact`) come strictly from ADK session state,
  never from LLM tool parameters (anti-IDOR).
- Two-phase booking (`hold_slot` -> `confirm_slot`) with a stable `Idempotency-Key` derived from
  `(session_id, slot_id)` so retries or duplicate calls never double-book.
- Wrapped with ADK `FunctionTool(..., require_confirmation=True)` in `beauty_advisor/agent.py` so
  nothing is written without explicit user confirmation.
- Strict retry policy: retries ONLY transient failures (timeouts, connect errors, HTTP 429/502/503/504)
  with bounded exponential backoff; NEVER retries 400/401/403/404/409/422.
- Emits domain event `appointment.confirmed` on first non-duplicate confirmation.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any

import httpx
from google.adk.tools import ToolContext

from claims.bus import bus

RETAIL_API = os.getenv("RETAIL_API_URL", "http://127.0.0.1:8090")
API_TIMEOUT_S = float(os.getenv("RETAIL_API_TIMEOUT_S", "1.5"))
API_RETRIES = int(os.getenv("RETAIL_API_RETRIES", "2"))
APPOINTMENTS_TOPIC = os.getenv("APPOINTMENTS_EVENTS_TOPIC", "appointments-events")

TRANSIENT_STATUS_CODES = frozenset({429, 502, 503, 504})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")

_http = httpx.Client(timeout=API_TIMEOUT_S)


def _request_with_retry(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    idempotent: bool = True,
) -> tuple[dict[str, Any] | None, str, int | None]:
    """Execute an HTTP request with bounded retries on TRANSIENT errors only.

    Rules:
    - Retries only on TimeoutException, ConnectError, and HTTP 429/502/503/504.
    - Non-idempotent writes are NEVER retried (attempts = 1).
    - 4xx errors other than 429 (e.g. 400, 401, 403, 404, 409) are NEVER retried.
    Returns: (json_payload_or_None, error_reason, status_code_or_None).
    """
    max_attempts = (API_RETRIES + 1) if idempotent else 1
    last_reason = "unavailable"
    last_status: int | None = None

    for attempt in range(max_attempts):
        try:
            resp = _http.request(
                method,
                f"{RETAIL_API}{path}",
                params=params,
                json=json_body,
                headers=headers,
            )
            last_status = resp.status_code
            if resp.status_code == 200:
                return resp.json(), "", 200
            if resp.status_code in TRANSIENT_STATUS_CODES:
                last_reason = f"http_{resp.status_code}"
            else:
                # Non-transient 4xx/5xx (401, 403, 404, 409, etc.): fail fast, zero retry
                return None, f"http_{resp.status_code}", resp.status_code
        except httpx.TimeoutException:
            last_reason = "timeout"
            last_status = None
        except httpx.ConnectError:
            last_reason = "connect_error"
            last_status = None
        except httpx.HTTPError:
            last_reason = "http_error"
            last_status = None

        if attempt < max_attempts - 1:
            time.sleep(0.2 * (2 ** attempt))

    return None, last_reason, last_status


def _session_id(tool_context: ToolContext | None) -> str:
    if tool_context is None:
        return "no-session"
    sess = getattr(tool_context, "session", None)
    if sess is not None and getattr(sess, "id", None):
        return str(sess.id)
    return str(getattr(tool_context, "invocation_id", "no-session"))


def _idempotency_key(session_id: str, slot_id: str) -> str:
    """Stable key for (session_id, slot_id) so retries or duplicate clicks never double-book."""
    return hashlib.sha256(f"{session_id}|{slot_id}".encode("utf-8")).hexdigest()[:24]


def _user_context(tool_context: ToolContext | None, require_contact: bool = False) -> tuple[dict[str, str] | None, str]:
    state = getattr(tool_context, "state", None) if tool_context is not None else None
    if state is None:
        return None, "Session context unavailable."
    customer_id = str(state.get("user:id") or "").strip()
    store = str(state.get("user:store") or "").strip()
    contact = str(state.get("user:contact") or "").strip()
    missing = []
    if not customer_id:
        missing.append("user:id")
    if not store:
        missing.append("user:store")
    if require_contact and not contact:
        missing.append("user:contact")
    if missing:
        return None, f"Missing required session state: {', '.join(missing)}"
    return {"customer_id": customer_id, "store": store, "contact": contact}, ""


def list_appointment_slots(date: str, tool_context: ToolContext | None = None) -> dict[str, Any]:
    """List available 30-minute in-store beauty advisor appointment slots for a given date.

    The customer's store is read automatically from authenticated session state (`user:store`).
    Args:
        date: Target date in YYYY-MM-DD format (e.g. "2026-11-25").
    Returns:
        {"status": "ok", "store": str, "date": str, "slots": [{"slot_id", "store", "store_name", "duration_min"}]}
        or {"status": "error"|"unavailable", "message": str}.
    """
    if not _DATE_RE.match((date or "").strip()):
        return {"status": "error", "error_code": "invalid_date", "message": "Format de date attendu : YYYY-MM-DD."}
    ctx, err = _user_context(tool_context, require_contact=False)
    if ctx is None:
        return {"status": "error", "error_code": "missing_user_context", "message": err}

    data, reason, code = _request_with_retry(
        "GET",
        "/appointments/slots",
        params={"store": ctx["store"], "date": date.strip()},
        idempotent=True,
    )
    if data is None:
        status = "unavailable" if reason in ("timeout", "connect_error") or code in TRANSIENT_STATUS_CODES else "error"
        return {
            "status": status,
            "reason": reason,
            "message": "Impossible de consulter les créneaux disponibles pour le moment.",
        }
    return {"status": "ok", **data}


def book_advisor_appointment(
    slot_id: str,
    topic: str = "consultation beauté",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Book an in-store appointment with a beauty advisor (two-phase hold + idempotent confirm).

    Requires explicit user confirmation (`require_confirmation=True`). Customer identity, store,
    and contact details are read exclusively from authenticated session state (`user:id`,
    `user:store`, `user:contact`) and never accepted as model arguments.
    Args:
        slot_id: Exact slot identifier returned by `list_appointment_slots` (e.g. "2026-11-25T14:00").
        topic: Short consultation topic (e.g. "diagnostic routine peau sensible").
    Returns:
        {"status": "confirmed", "appointment_id", "slot_id", "store", "store_name", "topic", "duplicate"}
        or {"status": "error"|"unavailable", "reason": str, "message": str}.
    """
    clean_slot = (slot_id or "").strip()
    clean_topic = (topic or "consultation beauté").strip()[:120]
    if not _SLOT_RE.match(clean_slot):
        return {
            "status": "error",
            "error_code": "invalid_slot",
            "message": "Identifiant de créneau invalide (format attendu : YYYY-MM-DDTHH:MM).",
        }

    ctx, err = _user_context(tool_context, require_contact=True)
    if ctx is None:
        return {"status": "error", "error_code": "missing_user_context", "message": err}

    idem_key = _idempotency_key(_session_id(tool_context), clean_slot)

    # Phase 1: Hold slot (idempotent for same customer + store + slot)
    hold_data, reason, code = _request_with_retry(
        "POST",
        "/appointments/hold",
        json_body={
            "store": ctx["store"],
            "slot_id": clean_slot,
            "customer_id": ctx["customer_id"],
            "topic": clean_topic,
        },
        idempotent=True,
    )
    if hold_data is None:
        if code == 409:
            return {
                "status": "conflict",
                "reason": reason,
                "message": "Ce créneau vient d'être réservé. Veuillez choisir un autre horaire.",
            }
        status = "unavailable" if reason in ("timeout", "connect_error") or code in TRANSIENT_STATUS_CODES else "error"
        return {
            "status": status,
            "reason": reason,
            "message": "Impossible de bloquer ce créneau pour le moment.",
        }

    hold_id = hold_data["hold_id"]
    if tool_context is not None and getattr(tool_context, "state", None) is not None:
        tool_context.state["temp:last_hold_id"] = hold_id

    # Phase 2: Confirm slot with stable Idempotency-Key (safe to retry on transient failures)
    confirm_data, reason, code = _request_with_retry(
        "POST",
        "/appointments/confirm",
        headers={"Idempotency-Key": idem_key},
        json_body={
            "hold_id": hold_id,
            "store": ctx["store"],
            "slot_id": clean_slot,
            "customer_id": ctx["customer_id"],
            "contact": ctx["contact"],
            "topic": clean_topic,
        },
        idempotent=True,
    )
    if confirm_data is None:
        status = "unavailable" if reason in ("timeout", "connect_error") or code in TRANSIENT_STATUS_CODES else "error"
        return {
            "status": status,
            "reason": reason,
            "message": "Impossible de confirmer le rendez-vous pour le moment.",
        }

    appt_id = confirm_data["appointment_id"]
    is_dup = bool(confirm_data.get("duplicate", False))

    if tool_context is not None and getattr(tool_context, "state", None) is not None:
        tool_context.state["temp:last_appointment_id"] = appt_id

    if not is_dup:
        bus().publish(
            APPOINTMENTS_TOPIC,
            {
                "type": "appointment.confirmed",
                "appointment_id": appt_id,
                "slot_id": clean_slot,
                "store": ctx["store"],
                "customer_id": ctx["customer_id"],
                "topic": clean_topic,
                "idempotency_key": idem_key,
            },
            claim_id=appt_id,
        )

    # Zero PII leak: never echo contact email/phone back in the tool payload
    return {
        "status": "confirmed",
        "appointment_id": appt_id,
        "slot_id": confirm_data["slot_id"],
        "store": confirm_data["store"],
        "store_name": confirm_data.get("store_name", ctx["store"]),
        "topic": clean_topic,
        "duplicate": is_dup,
    }
