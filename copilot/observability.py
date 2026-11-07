"""Telemetry setup: one call, driven by env, safe by default.

ADK already emits OTel spans (invoke_agent, call_llm, execute_tool) and GenAI metrics. This module only decides
WHERE they go:
  TELEMETRY=gcp      -> Cloud Trace (+ Cloud Monitoring metrics) via ADK's get_gcp_exporters (OTLP to
                        telemetry.googleapis.com, authenticated with ADC / the service account)
  OTEL_EXPORTER_OTLP_* env vars -> ADK adds a generic OTLP exporter automatically: that's how New Relic is wired,
                        with zero code (endpoint + api-key header in env, see README)
  TELEMETRY=off (default) -> nothing exported (adk web still shows its local trace tab)

`adk web` / `adk api_server`: use ADK's native flag `--otel_to_cloud` instead of TELEMETRY=gcp. Those servers
create their TracerProvider before loading the agent, and Google's Telemetry API rejects spans (HTTP 400) whose
resource lacks `gcp.project_id`; a provider's resource can't be changed afterwards. With --otel_to_cloud ADK builds
the provider with the proper GCP resource. (D2.2: zero spans, then 400 Bad Request on every batch.)

PRIVACY: prompts/responses (= M365 mail bodies here) are replaced by "<elided>" unless
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true. We force the safe default explicitly.
"""
import logging
import os

_done = False
log = logging.getLogger(__name__)


def setup_telemetry() -> None:
    """Never raises: telemetry failing must not take the agent down (it degrades to 'no export' + an error log)."""
    global _done
    if _done:
        return
    _done = True
    os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "NO_CONTENT")  # off for BOTH ADK (only 1/true = on) and the GenAI instrumentor (enum)
    try:
        _setup()
    except Exception:  # noqa: BLE001
        log.exception("telemetry setup failed; continuing WITHOUT export")


def _setup() -> None:
    mode = os.getenv("TELEMETRY", "off")
    has_otlp = bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))
    if mode == "off" and not has_otlp:
        return
    from google.adk.telemetry.setup import _get_otel_exporters, maybe_set_otel_providers
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource

    os.environ.setdefault("OTEL_SERVICE_NAME", "beauty-ops-copilot")
    current = trace.get_tracer_provider()
    owns_provider = isinstance(current, trace.ProxyTracerProvider)
    attrs = getattr(getattr(current, "resource", None), "attributes", {}) or {}
    gcp_ok = mode == "gcp" and (owns_provider or "gcp.project_id" in attrs)
    if mode == "gcp" and not gcp_ok:
        log.error("telemetry: TELEMETRY=gcp ignored: an existing TracerProvider (adk web/api_server) has no "
                  "gcp.project_id, so Cloud Trace would reject every batch (HTTP 400). Use `adk web --otel_to_cloud`.")
    hooks = []
    if gcp_ok:
        from google.adk.telemetry.google_cloud import get_gcp_exporters
        hooks.append(get_gcp_exporters(enable_cloud_tracing=True, enable_cloud_metrics=owns_provider,
                                       enable_cloud_logging=False))  # logs off: gen_ai events would carry content
    if owns_provider:
        resource = Resource.create({
            "service.name": os.environ["OTEL_SERVICE_NAME"],
            "service.version": os.getenv("SERVICE_VERSION", "dev"),
            "deployment.environment": os.getenv("DEPLOY_ENV", "local"),
            "gcp.project_id": os.getenv("GOOGLE_CLOUD_PROJECT", ""),  # required by telemetry.googleapis.com
        })
        maybe_set_otel_providers(otel_hooks_to_setup=hooks, otel_resource=resource)
        log.warning("telemetry: new provider (gcp=%s, otlp=%s, content_capture=%s)", gcp_ok, has_otlp,
                    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"])
        return
    procs = [sp for h in hooks for sp in h.span_processors] + list(_get_otel_exporters().span_processors)
    for sp in procs:
        current.add_span_processor(sp)
    log.warning("telemetry: attached %d span processor(s) to existing provider (gcp=%s, otlp=%s)",
                len(procs), gcp_ok, has_otlp)
