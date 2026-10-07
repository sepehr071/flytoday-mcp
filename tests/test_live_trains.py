import pytest
from conftest import days
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_ft_find_station(client):
    data = await call(client, "ft_find_station", {"query": "مشهد"})
    assert 191 in [s["station_code"] for s in data["stations"]]
    assert (await call(client, "ft_find_station", {}))["stations"][0]["station_code"] == 1


async def test_ft_search_trains_and_stops(client):
    """One search, then its stops and wagon (two cheap calls on other endpoints)."""
    data = await call(
        client, "ft_search_trains", {"origin": 1, "destination": 191, "date": days(5), "sort": "cheapest"}
    )
    assert data["from"] == "تهران" and data["to"] == "مشهد" and data["classes_listed"] > 0
    for t in data["trains"]:
        assert t["seats_left"] > 0 and 100_000 < t["price_toman"] < 20_000_000
    if data["trains"]:
        t = data["trains"][0]
        stops = await call(
            client, "ft_train_stops", {"fare_source_code": t["fare_source_code"], "wagon_id": t["wagon_id"]}
        )
        assert stops["stops"][0]["station_code"] == 1 and stops["stops"][-1]["station_code"] == 191
        assert stops["wagon"]["name"]


async def test_ft_train_calendar(client):
    data = await call(client, "ft_train_calendar", {"origin": 1, "destination": 191})
    assert data["days"] and data["cheapest"]["cheapest_toman"] > 100_000
