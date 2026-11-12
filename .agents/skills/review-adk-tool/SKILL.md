---
name: review-adk-tool
description: Inspects ADK tools for parameter validation, error handling, state scoping, and HITL safety.
---

# Review ADK Tool Skill

Use this skill when auditing or adding tools invoked by Google ADK agents (`advisor/tools.py`, `claims/tools.py`, `workspace_mcp/server.py`).

## Core Invariants for ADK Tools

1. **Deterministic Function Signatures & Types**:
   - Every parameter must have explicit Python type annotations (`str`, `int`, `float`, `bool`).
   - Docstrings must be unambiguous so Gemini understands the exact tool semantics and parameter constraints.

2. **Scoped State & Anti-IDOR Protections**:
   - Access to user-scoped data must check `tool_context.state["user:id"]` or enforce ownership checks.
   - Ephemeral session mutations should use the `temp:` prefix (e.g., `temp:last_claim_id`).
   - Never accept an untrusted `user_id` parameter directly from model generation if that user_id should be bound to the authenticated session.

3. **Graceful Degradation & Network Fallbacks**:
   - External API calls (e.g. retail stock, delivery API) must specify timeouts and retry limits (bounded retries, e.g., 2 attempts with backoff).
   - On network outage or 5xx response, return a structured fallback response:
     ```python
     return {"status": "unavailable", "reason": "timeout", "message": "Service temporairement indisponible."}
     ```
   - Never raise unhandled HTTP exceptions that crash the ADK runner.

4. **Human-In-The-Loop (HITL) for Destructive Actions**:
   - Any tool executing irreversible actions (financial refunds, claims approval, email dispatch) must declare `require_confirmation=True` or implement explicit two-phase confirmation.
   - Financial refunds must pass an idempotent transaction key (e.g. `Idempotency-Key = claim_id`).

5. **Pure Python & Zero Leaks**:
   - Tools must not write sensitive customer data or internal keys into return strings that could be reflected back to the customer.

