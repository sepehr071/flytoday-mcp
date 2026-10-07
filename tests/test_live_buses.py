import pytest
from conftest import days
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_ft_find_bus_city(client):
    data = await call(client, "ft_find_bus_city", {"query": "اصفهان"})
    assert "0002" in [c["city_id"] for c in data["cities"]]
    assert (await call(client, "ft_find_bus_city", {}))["cities"][0]["city_id"] == "0001"


async def test_ft_search_buses(client):
    data = await call(client, "ft_search_buses", {"origin": "0001", "destination": "0002", "date": days(5), "limit": 5})
    assert data["buses_listed"] > 0 and data["buses"]
    for b in data["buses"]:
        assert b["seats_left"] > 0 and 100_000 < b["price_toman"] < 5_000_000
    assert len(str(data)) < 15_000


async def test_ft_bus_calendar(client):
    data = await call(client, "ft_bus_calendar", {"origin": "0001", "destination": "0002"})
    assert len(data["days"]) > 5 and data["cheapest"]["price_toman"] == min(d["price_toman"] for d in data["days"])
