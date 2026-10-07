import pytest
from conftest import days
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_ft_find_airport(client):
    data = await call(client, "ft_find_airport", {"query": "mashhad"})
    assert "MHD" in [p["code"] for p in data["places"]]
    popular = await call(client, "ft_find_airport", {})
    assert popular["popular_domestic"] and popular["popular_international"]


async def test_ft_search_flights_and_details(client):
    args = {"origin": "THR", "destination": "MHD", "date": days(16), "adults": 2}
    data = await call(client, "ft_search_flights", args)  # the only live search of this file
    flights = data["flights"]
    prices = [f["price_toman"] for f in flights]
    assert prices and prices == sorted(prices) and 1_000_000 < prices[0] < 100_000_000  # Toman, not Rial
    assert all(f["total_toman"] >= 2 * f["price_toman"] for f in flights)
    assert data["search_id"] and data["paging"]["total"] >= len(flights)
    pick = flights[0]
    details = await call(client, "ft_flight_details", {"fare_source_code": pick["fare_source_code"]})
    assert details["rules"] and details["baggage"][0]["flight_no"] in pick["flight_no"]


async def test_ft_flight_calendar(client):
    data = await call(client, "ft_flight_calendar", {"origin": "THR", "destination": "MHD", "date": days(16)})
    assert data["days"] and data["cheapest"]["price_toman"] == min(d["price_toman"] for d in data["days"])
