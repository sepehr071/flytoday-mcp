import pytest
from conftest import days
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]

DATES = {"check_in": days(14), "check_out": days(16)}


async def hotel_call(client, name, args):
    """Hotel availability answers a captcha (HTTP 429) after about 12 calls per 10 minutes and then blocks the IP."""
    try:
        return await call(client, name, args)
    except AssertionError as e:
        if "wants a captcha" in str(e):
            pytest.skip("hotel availability is captcha-blocked for this IP")
        raise


async def test_ft_find_hotel_place(client):
    data = await call(client, "ft_find_hotel_place", {"query": "مشهد"})
    assert data["hotels"][0]["city_id"] == 10
    assert any(h.get("hotel_id") for h in data["hotels"])
    ramsar = await call(client, "ft_find_hotel_place", {"query": "رامسر"})
    assert "149" in [r["region_code"] for r in ramsar["stay_regions"]]
    istanbul = await call(client, "ft_find_hotel_place", {"query": "Istanbul", "international": True})
    assert istanbul["hotels"][0]["region_code"] and istanbul["hotels"][0]["country_code"] == "TR"
    popular = await call(client, "ft_find_hotel_place", {})
    assert popular["hotels"] and popular["popular_stay_areas"]


async def test_ft_search_hotels_and_ft_hotel(client):
    data = await hotel_call(client, "ft_search_hotels", {**DATES, "city_id": 10, "limit": 5})
    assert data["nights"] == 2 and data["hotels"]
    for h in data["hotels"]:
        assert h["name"] and 1_000_000 < h["price_toman"] < 200_000_000
        assert h["per_night_toman"] == h["price_toman"] // 2
    prices = [h["price_toman"] for h in data["hotels"]]
    assert prices == sorted(prices)
    assert len(str(data)) < 15_000
    open_hotel = next(h for h in data["hotels"] if h.get("rooms_left"))  # the cheapest can be sold out
    one = await hotel_call(client, "ft_hotel", {**DATES, "hotel_id": open_hotel["hotel_id"]})
    assert one["name"] and one["offers"] and one["rooms"] is not None
    assert len(str(one).encode()) < 15_000


async def test_ft_hotel_reviews(client):
    data = await call(client, "ft_hotel_reviews", {"hotel_id": 24, "limit": 3})
    assert data["total_reviews"] > 10 and len(data["reviews"]) == 3
    assert all(1 <= r["score"] <= 10 for r in data["reviews"])


async def test_ft_search_stays_and_ft_stay(client):
    data = await call(client, "ft_search_stays", {**DATES, "region_code": "149", "limit": 5})
    assert data["total_matching"] > 0 and data["stays"]
    for s in data["stays"]:
        assert 500_000 < s["price_per_night_toman"] < 100_000_000  # Toman per night, not divided
        assert s["total_toman"] >= s["price_per_night_toman"] * 2 - 1
    nightly = [s["price_per_night_toman"] for s in data["stays"]]
    assert nightly == sorted(nightly)
    stay = await call(client, "ft_stay", {"property_id": data["stays"][0]["property_id"], "from_date": days(10)})
    assert stay["name"] and len(stay["calendar"]) >= 10 and stay["calendar"][0]["price_toman"] > 0
    assert len(str(stay).encode()) < 15_000
