"""Unit tests for enterprise ADK patterns:
1. SequentialAgent with output_schema separation (advisor.structured_quote)
2. EventActions state delta extraction
3. Enterprise Cloud connectors architecture manifest
4. ContextCacheConfig wiring
"""
from types import SimpleNamespace
import pytest

from advisor.enterprise_connectors import (
    ENTERPRISE_ARCHITECTURE_MAP,
    get_enterprise_architecture_manifest,
)
from advisor.structured_quote import (
    DevisConfiguratorSchema,
    QuoteLineItem,
    QuoteOptions,
    create_quote_pipeline,
    extract_event_actions_state_deltas,
)
from beauty_advisor.agent import app
from google.adk.agents.context_cache_config import ContextCacheConfig


def test_quote_pydantic_schema_validation():
    item = QuoteLineItem(
        sku="BAT-VASQ-001",
        designation="Meuble Vasque 80cm Chêne Naturel",
        quantity=1,
        unit_price_eur=349.0,
        total_eur=349.0,
    )
    devis = DevisConfiguratorSchema(
        quote_id="DEV-892102",
        customer_need="Rénovation salle de bain moderne",
        lines=[item],
        subtotal_eur=349.0,
        discount_eur=0.0,
        total_eur=349.0,
        options=QuoteOptions(installation_included=True, warranty_months=36),
    )
    assert devis.quote_id == "DEV-892102"
    assert devis.total_eur == 349.0
    assert devis.options.installation_included is True
    assert devis.options.warranty_months == 36


def test_sequential_agent_pipeline_separation():
    pipeline = create_quote_pipeline()
    assert pipeline.name == "quote_sequential_pipeline"
    assert len(pipeline.sub_agents) == 2

    collector, formatter = pipeline.sub_agents
    # Step 1: Collector has calculation tools, NO output_schema
    assert collector.name == "quote_collector"
    assert len(collector.tools) > 0
    assert collector.output_schema is None

    # Step 2: Formatter has strict output_schema, NO tools, include_contents="none"
    assert formatter.name == "quote_formatter"
    assert len(formatter.tools) == 0
    assert formatter.output_schema == DevisConfiguratorSchema
    assert formatter.include_contents == "none"


def test_event_actions_state_delta_extraction():
    class DummyEvent:
        def __init__(self, delta):
            self.actions = SimpleNamespace(state_delta=delta)

    events = [
        DummyEvent({"temp:active_order_id": "CMD-1002"}),
        DummyEvent({"temp:last_claim_id": "CLM-459102"}),
    ]
    deltas = extract_event_actions_state_deltas(events)
    assert deltas["temp:active_order_id"] == "CMD-1002"
    assert deltas["temp:last_claim_id"] == "CLM-459102"


def test_enterprise_connectors_manifest():
    manifest = get_enterprise_architecture_manifest()
    assert manifest["framework"] == "Google Agent Development Kit (ADK) 2.13"
    assert "VertexAiSearchTool" in manifest["connectors"]["search_knowledge"]["adk_native_tool"]
    assert "ToolboxToolset" in manifest["connectors"]["retail_stock_db"]["adk_native_tool"]
    assert "LongRunningFunctionTool" in manifest["connectors"]["advisor_appointment"]["adk_native_tool"]
    assert "ApplicationIntegrationToolset" in manifest["connectors"]["workspace_connectors"]["adk_native_tool"]


def test_context_cache_config_instantiation(monkeypatch):
    monkeypatch.setenv("CONTEXT_CACHE", "1")
    monkeypatch.setenv("CONTEXT_CACHE_TTL_S", "3600")
    cfg = ContextCacheConfig(ttl_seconds=3600)
    assert cfg.ttl_seconds == 3600

