"""One live call per tool (about 12 requests in total). Run: FLYTODAY_MCP_PROXY=... pytest -m live tests/test_live_extras.py"""

import pytest
from conftest import days, live_call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_live_cip(client):
    data = await live_call(client, "ft_cip", {"airport": "IKA", "date": days(10)})
    offer = data["offers"][0]
    assert offer["total_toman"] > 0 and offer["passengers"][0]["passenger"] == "adult"


async def test_live_esim(client):
    data = await live_call(client, "ft_esim_plans", {"country": "turkey", "limit": 5})
    assert data["slug"] == "turkey" and data["plans"][0]["price_toman"] > 0 and data["plans"][0]["days"] > 0


async def test_live_visa(client):
    listing = await live_call(client, "ft_visa", {})
    assert listing["total"] > 20 and any(c["slug"] == "Pakistan" for c in listing["countries"])
    data = await live_call(client, "ft_visa", {"country": "pakistan"})
    assert data["visa_types"][0]["adult_price_toman"] > 0 and data["documents"]


async def test_live_activities(client):
    data = await live_call(client, "ft_activities", {"query": "Dubai", "limit": 5})
    assert data["region"]["region_id"] and data["activities"][0]["price_toman"] > 0
    detail = await live_call(client, "ft_activity", {"activity_id": data["activities"][0]["activity_id"]})
    assert detail["name"] and detail["price_toman"] is not None
