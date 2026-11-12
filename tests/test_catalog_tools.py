from copilot.tools.catalog import get_campaign, search_products


def test_search_by_ingredient():
    r = search_products("niacinamide")
    assert r["status"] == "ok" and {i["sku"] for i in r["items"]} >= {"GAR-SER-001", "GAR-SER-002"}


def test_search_brand_filter_and_limit():
    r = search_products("serum", brand="garnier", limit=1)
    assert r["count"] == 1 and r["items"][0]["brand"] == "Garnier"


def test_empty_query_without_filter_is_error_not_exception():
    assert search_products("  ")["status"] == "error"


def test_exact_sku_lookup_in_one_call():
    # Regression for D2.1 cat_price: SKU wasn't searchable -> 5 tool calls before an indirect answer.
    r = search_products("GAR-SER-002")
    assert r["count"] == 1 and r["items"][0]["price_eur"] == 11.5


def test_empty_query_with_filters_lists_matches():
    r = search_products("", brand="Garnier", category="serum")
    assert {i["sku"] for i in r["items"]} == {"GAR-SER-001", "GAR-SER-002"}


def test_campaign_filters():
    assert get_campaign(quarter="2026-Q4")["count"] == 2
    assert get_campaign(campaign_id="NOPE")["status"] == "error"
    assert get_campaign()["status"] == "error"


def test_agent_builds():
    from copilot.agent import root_agent
    assert root_agent.name == "copilot" and {a.name for a in root_agent.sub_agents} == {"workspace_agent", "catalog_agent"}


def test_price_and_category_filters_in_tool_not_model():
    assert search_products("retinol", max_price_eur=10)["count"] == 0
    r = search_products("niacinamide", brand="Garnier", category="serum")
    assert {i["sku"] for i in r["items"]} == {"GAR-SER-001", "GAR-SER-002"}


def test_alias_and_truncation_flag():
    assert search_products("retinoid")["items"][0]["sku"] == "CRV-SER-001"
    r = search_products("niacinamide", limit=1)
    assert r["truncated"] is True and r["total_matches"] >= 4


def test_near_miss_when_filters_exclude_everything():
    r = search_products("retinol", max_price_eur=10)
    assert r["count"] == 0 and r["near_miss"]["cheapest"]["sku"] == "CRV-SER-001"
    assert "near_miss" not in search_products("unicorn")


def test_router_modes_wire_agenttool_only_in_agenttool_mode():
    from google.adk.tools import AgentTool
    from copilot.agent import build_root
    for mode, expect in (("transfer", False), ("agenttool", True)):
        agent = next(a for a in build_root(mode).sub_agents if a.name in ("workspace_agent", "m365_agent"))
        assert any(isinstance(t, AgentTool) for t in agent.tools) is expect
