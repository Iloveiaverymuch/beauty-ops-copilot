"""Event bus behind one interface: Google Cloud Pub/Sub in the real setup, an in-process list in tests.

Topics
  claims-decisions : a reviewer's approve/reject (published by the back office, consumed by claims.worker)
  claims-events    : workflow outcomes (claim.opened, claim.pending_review, claim.closed) for notifications/analytics
Pub/Sub delivers AT LEAST ONCE: every consumer must be idempotent (see claims.worker).
"""
from __future__ import annotations

import json
import os
import threading


class LocalBus:
    def __init__(self):
        self.messages: list[tuple[str, dict, dict]] = []

    def publish(self, topic: str, data: dict, **attributes: str) -> str:
        self.messages.append((topic, data, attributes))
        return str(len(self.messages))


class PubSubBus:
    """google-cloud-pubsub publisher. publish() returns a future; .result() waits for the server's message id
    (so a publish failure surfaces here instead of being lost)."""

    def __init__(self, project: str):
        from google.cloud import pubsub_v1
        self.project = project
        self.client = pubsub_v1.PublisherClient()

    def publish(self, topic: str, data: dict, **attributes: str) -> str:
        path = self.client.topic_path(self.project, topic)
        return self.client.publish(path, json.dumps(data, ensure_ascii=False).encode(), **attributes).result(timeout=10)


_bus = None
_lock = threading.Lock()
DECISIONS_TOPIC = os.getenv("CLAIMS_DECISIONS_TOPIC", "claims-decisions")
EVENTS_TOPIC = os.getenv("CLAIMS_EVENTS_TOPIC", "claims-events")


def bus():
    global _bus
    if _bus is None:
        with _lock:
            if _bus is None:
                _bus = PubSubBus(os.environ["GOOGLE_CLOUD_PROJECT"]) if os.getenv("CLAIMS_BUS", "local") == "pubsub" else LocalBus()
    return _bus
