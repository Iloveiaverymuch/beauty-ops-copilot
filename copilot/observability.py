"""Telemetry setup: one call, driven by env, safe by default.

ADK already emits OTel spans (invoke_agent, call_llm, execute_tool) and GenAI metrics. This module only decides
WHERE they go:
  TELEMETRY=gcp      -> Cloud Trace (+ Cloud Monitoring metrics) via ADK's get_gcp_exporters (OTLP to
                        telemetry.googleapis.com, authenticated with ADC / the service account)
  OTEL_EXPORTER_OTLP_* env vars -> ADK adds a generic OTLP exporter automatically: that's how New Relic is wired,
                        with zero code (endpoint + api-key header in env, see README)
  TELEMETRY=off (default) -> nothing exported (adk web still shows its local trace tab)

PRIVACY: prompts/responses (= M365 mail bodies here) are replaced by "<elided>" unless
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true. We force the safe default explicitly.
"""
import logging
import os

_done = False
log = logging.getLogger(__name__)


def setup_telemetry() -> None:
    global _done
    if _done:
        return
    _done = True
    os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false")
    mode = os.getenv("TELEMETRY", "off")
    has_otlp = bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))
    if mode == "off" and not has_otlp:
        return
    from google.adk.telemetry.google_cloud import get_gcp_exporters
    from google.adk.telemetry.setup import maybe_set_otel_providers
    from opentelemetry.sdk.resources import Resource

    os.environ.setdefault("OTEL_SERVICE_NAME", "beauty-ops-copilot")
    hooks = []
    if mode == "gcp":
        hooks.append(get_gcp_exporters(enable_cloud_tracing=True, enable_cloud_metrics=True,
                                       enable_cloud_logging=False))  # logs off: gen_ai events would carry content
    resource = Resource.create({
        "service.name": os.environ["OTEL_SERVICE_NAME"],
        "service.version": os.getenv("SERVICE_VERSION", "dev"),
        "deployment.environment": os.getenv("DEPLOY_ENV", "local"),
        "gcp.project_id": os.getenv("GOOGLE_CLOUD_PROJECT", ""),
    })
    maybe_set_otel_providers(otel_hooks_to_setup=hooks, otel_resource=resource)
    log.info("telemetry: mode=%s otlp=%s content_capture=%s", mode, has_otlp,
             os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"])
