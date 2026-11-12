"""Internal operations tools for Beauty Ops Copilot.

Enables the operations team to inspect pending claims, validate refunds,
and arbitrate customer disputes through the internal copilot.
"""

from __future__ import annotations

from typing import Any
from advisor import claims


def list_pending_claims() -> dict[str, Any]:
    """List all customer return and refund claims currently pending human review or retry.
    Returns: {"status": "ok", "count": int, "claims": list[dict]}
    """
    items = claims.pending()
    return {
        "status": "ok",
        "count": len(items),
        "claims": [
            {
                "claim_id": c["claim_id"],
                "status": c["status"],
                "amount_eur": c.get("amount_eur"),
                "rule": c.get("rule"),
                "message": c.get("message"),
            }
            for c in items
        ],
    }


def review_claim(claim_id: str, decision: str, reason: str = "") -> dict[str, Any]:
    """Review and arbitrate a pending customer return/refund claim.
    CRITICAL FINANCIAL ACTION: Approving this executes a real customer refund.
    Args:
        claim_id: The claim identifier (e.g. "CLM-B730C8").
        decision: Must be "approve" to execute refund or "reject" to decline.
        reason: Justification recorded in the audit trail.
    Returns: {"status": str, "claim_id": str, "outcome": str, "amount_eur": float, "refund_id": str | None, "message": str}
    """
    if decision not in ("approve", "reject"):
        return {"status": "error", "message": "Decision must be 'approve' or 'reject'"}

    outcome, st = claims.handle_decision(
        {
            "claim_id": claim_id,
            "decision": decision,
            "reviewer": "operations_copilot",
            "reason": reason,
        }
    )
    return {
        "status": st.get("status"),
        "claim_id": claim_id,
        "outcome": outcome,
        "amount_eur": st.get("amount_eur"),
        "refund_id": st.get("refund_id"),
        "message": st.get("message"),
    }
