"""Tracing for the chatbot backend. One call at startup, env-driven, never crashes the app.

TELEMETRY=gcp  -> spans exported to Cloud Trace (OTLP to telemetry.googleapis.com, ADK's exporter)
TELEMETRY=off  -> spans created but not exported (default; zero cost)
OTEL_EXPORTER_OTLP_ENDPOINT set -> also exported there (Langfuse, New Relic, any OTLP backend) with no code change.

Lessons carried over from the Cloud Run deployment of the internal copilot:
- sampler always_on: a platform in front of you (Cloud Run) may send traceparent sampled=0, and the default
  parent-based sampler would silently drop every span (IDs present, nothing exported, no error).
- the resource must carry gcp.project_id or the Telemetry API rejects every batch (HTTP 400).
- prompt/response content is NOT captured in spans by default (customer messages = personal data).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def setup_tracing(service: str = "advisor-api") -> None:
    os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "NO_CONTENT")
    os.environ.setdefault("OTEL_TRACES_SAMPLER", "always_on")
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({
            "service.name": service, "gcp.project_id": os.getenv("GOOGLE_CLOUD_PROJECT", ""),
            "deployment.environment": os.getenv("DEPLOY_ENV", "local")}))
        n = 0
        if os.getenv("TELEMETRY", "off") == "gcp":
            from google.adk.telemetry.google_cloud import get_gcp_exporters
            for sp in get_gcp_exporters(enable_cloud_tracing=True, enable_cloud_metrics=False,
                                        enable_cloud_logging=False).span_processors:
                provider.add_span_processor(sp)
                n += 1
        if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            n += 1
        trace.set_tracer_provider(provider)
        log.warning("tracing: provider set, %d exporter(s), telemetry=%s", n, os.getenv("TELEMETRY", "off"))
    except Exception:  # noqa: BLE001 - telemetry must never take the chatbot down
        log.exception("tracing setup failed; continuing without export")
