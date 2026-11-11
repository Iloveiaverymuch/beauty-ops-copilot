"""Pub/Sub worker: consumes reviewer decisions and resumes the claims graph.

    CLAIMS_BUS=pubsub uv run --env-file copilot/.env python -m claims.worker

Delivery semantics: Pub/Sub is at-least-once, so the handler is idempotent (claims.service.handle_decision looks at
the checkpoint: waiting for a human -> resume; refund failed -> retry; anything else -> duplicate, ack).
Failure handling: if the refund API is still down, the message is NACKed -> Pub/Sub redelivers it later with the
subscription's retry backoff, and the redelivery re-runs only the failed refund node (same Idempotency-Key).
"""
from __future__ import annotations

import json
import logging
import os

from google.cloud import pubsub_v1

from claims.service import handle_decision

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("claims.worker")
SUBSCRIPTION = os.getenv("CLAIMS_DECISIONS_SUBSCRIPTION", "claims-decisions-worker")


def callback(message: pubsub_v1.subscriber.message.Message) -> None:
    try:
        data = json.loads(message.data)
        outcome, st = handle_decision(data)
        log.info("claim=%s outcome=%s status=%s delivery_attempt=%s", data.get("claim_id"), outcome, st["status"],
                 message.delivery_attempt)
        if st["status"] == "refund_failed":
            message.nack()          # broker-managed retry with backoff
        else:
            message.ack()
    except Exception:  # noqa: BLE001 - a poison message must not kill the worker; dead-letter topic in production
        log.exception("handler failed")
        message.nack()


def main() -> None:
    sub = pubsub_v1.SubscriberClient()
    path = sub.subscription_path(os.environ["GOOGLE_CLOUD_PROJECT"], SUBSCRIPTION)
    log.info("listening on %s", path)
    future = sub.subscribe(path, callback=callback,
                           flow_control=pubsub_v1.types.FlowControl(max_messages=4))   # bounded concurrency
    try:
        future.result()
    except KeyboardInterrupt:
        future.cancel()


if __name__ == "__main__":
    main()
