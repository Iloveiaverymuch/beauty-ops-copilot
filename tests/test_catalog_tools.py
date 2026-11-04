from copilot.tools.catalog import get_campaign, search_products


def test_search_by_ingredient():
    r = search_products("niacinamide")
    assert r["status"] == "ok" and {i["sku"] for i in r["items"]} >= {"GAR-SER-001", "GAR-SER-002"}


def test_search_brand_filter_and_limit():
    r = search_products("serum", brand="garnier", limit=1)
    assert r["count"] == 1 and r["items"][0]["brand"] == "Garnier"


def test_empty_query_is_error_not_exception():
    assert search_products("  ")["status"] == "error"


def test_campaign_filters():
    assert get_campaign(quarter="2026-Q4")["count"] == 2
    assert get_campaign(campaign_id="NOPE")["status"] == "error"
    assert get_campaign()["status"] == "error"


def test_agent_builds():
    from copilot.agent import root_agent
    assert root_agent.name == "catalog_agent" and len(root_agent.tools) == 2
