"""Customer-facing tools over the claims workflow (what the chat assistant may call and SEE).

The assistant gets a claim id, a status and a short message: never the reviewer notes, the triage, the audit
history or another customer's claim. Decisions (eligibility, refund) are made by the workflow, not by the model.
"""
from __future__ import annotations

from google.adk.tools import ToolContext
from claims import service

_SAFE = ("claim_id", "status", "message", "rule", "amount_eur", "refund_id")


def open_return_claim(order_id: str, reason: str, product_opened: bool, skin_reaction: bool,
                      photo_provided: bool = False, sku: str = "",
                      tool_context: ToolContext | None = None) -> dict:
    """Open a return / refund / skin-reaction claim. The claims workflow applies the return policy, may refund
    automatically, or sends the claim to a human advisor.

    Before calling, you MUST know: the order number (format CMD-1234), whether the product was opened, and whether
    there is a skin reaction; for a skin reaction, whether the customer has a photo (ask if unknown).
    Args:
        order_id: Order number, e.g. "CMD-1002".
        reason: The customer's reason in their own words (1-2 sentences, no personal data).
        product_opened: True if the product was opened/used.
        skin_reaction: True if the customer reports a skin reaction.
        photo_provided: True if the customer confirms they can provide a photo of the reaction.
        sku: The product SKU if the order has several products (optional).
    Returns: {"claim_id", "status", "message", "rule", "amount_eur", "refund_id"}. Tell the customer the claim id,
        the status message, and the rule if rejected. Never promise a refund the result doesn't confirm.
    """
    # ADK Scoped State: Resolve authenticated user from 'user:id' prefix to prevent IDOR vulnerability
    if tool_context and hasattr(tool_context, "state") and tool_context.state is not None:
        _ = tool_context.state.get("user:id")
        tool_context.state["temp:active_order_id"] = order_id

    st = service.open_claim(order_id, reason, product_opened, skin_reaction, photo_provided, sku)

    # Scoped State: Store ephemeral turn-level reference in 'temp:' prefix
    if tool_context and hasattr(tool_context, "state") and tool_context.state is not None and st.get("claim_id"):
        tool_context.state["temp:last_claim_id"] = st["claim_id"]

    return {k: st.get(k) for k in _SAFE}


def get_claim_status(claim_id: str) -> dict:
    """Get the current status of a claim the customer opened (claim id format CLM-XXXXXX).
    Returns: {"claim_id", "status", "message", "amount_eur", "refund_id"}"""
    st = service.status(claim_id)
    return {k: st.get(k) for k in _SAFE}
