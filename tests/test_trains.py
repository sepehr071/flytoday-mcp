import httpx
import pytest
from conftest import body, fixture

from flytoday_mcp import trains

pytestmark = pytest.mark.anyio


async def error(client, name, args) -> str:
    """The text of a failed tool call."""
    result = await client.call_tool(name, args)
    assert result.is_error
    return result.content[0].text


SEARCH = "/V2/DomesticRails/Search"
ARGS = {"origin": 1, "destination": 191, "date": "2026-10-14"}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(trains, "RETRY_DELAY", 0)


async def test_find_station_search_and_popular(client, api):
    api["/Rail/DomesticSearch"] = fixture("trains_stations.json")
    api["/Rail/DomesticPopularStations"] = fixture("trains_popular.json")
    data = (await client.call_tool("ft_find_station", {"query": " مش ", "limit": 2})).structured_content
    assert data["stations"] == [
        {"station_code": 191, "name_fa": "مشهد", "name": "mashhad"},
        {"station_code": 301, "name_fa": "اسلامشهر", "name": "eslamshahr"},
    ]
    r = api.calls[0]
    assert (r.method, r.headers["x-proxy-origin"], body(r)) == (
        "POST",
        "https://placesearch.flytoday.ir/api",
        {"searchTerm": "مش", "pageSize": 2, "pageNumber": 0},
    )
    popular = (await client.call_tool("ft_find_station", {})).structured_content
    assert popular["stations"][0]["station_code"] == 1 and api.calls[1].method == "GET"


async def test_search_trains_shapes_and_sorts(client, api):
    api[SEARCH] = fixture("trains_search.json")
    data = (await client.call_tool("ft_search_trains", {**ARGS, "adults": 2, "children": 1})).structured_content
    assert (data["from"], data["to"], data["classes_listed"], data["matching"]) == ("تهران", "مشهد", 7, 3)
    assert [t["departure"][-5:] for t in data["trains"]] == ["07:25", "09:20", "16:50"]  # earliest; sold out hidden
    second = data["trains"][1]
    assert second["company"] == "رجا" and second["price_toman"] == 760000 and second["seats_left"] == 18
    assert second["arrival"] == "2026-10-14 21:45" and second["duration"] == "12:25"
    assert second["fare_source_code"] and second["wagon_id"] and data["search_id"] == 12833316
    assert body(api.calls[0]) == {
        "sexCode": 3,
        "adultsCount": 2,
        "childCount": 1,
        "infantCount": 0,
        "isExclusive": False,
        "tripType": "OneWay",
        "originDestination": [{"origin": 1, "destination": 191, "moveDate": "2026-10-14"}],
    }
    cheap = (await client.call_tool("ft_search_trains", {**ARGS, "sort": "cheapest", "limit": 2})).structured_content
    assert [t["price_toman"] for t in cheap["trains"]] == [760000, 1020000]
    every = (await client.call_tool("ft_search_trains", {**ARGS, "available_only": False})).structured_content
    assert every["matching"] == 7


async def test_search_trains_retries_599(client, api):
    down = httpx.Response(599, json={"message": "link down"})
    answers = [down, down, httpx.Response(200, json=fixture("trains_search.json"))]
    api[SEARCH] = lambda request: answers.pop(0)
    data = (await client.call_tool("ft_search_trains", ARGS)).structured_content
    assert data["matching"] == 3 and len(api.calls) == 3


async def test_search_trains_gives_up_after_three_retries(client, api):
    api[SEARCH] = lambda request: httpx.Response(599, json={"message": "link down"})
    assert "599" in await error(client, "ft_search_trains", ARGS)
    assert len(api.calls) == 4


async def test_search_trains_no_result_and_past_date(client, api):
    api[SEARCH] = {"id": 1, "itinerariesList": [{"noResult": True, "allTrainsMoved": True, "itineraries": []}]}
    data = (await client.call_tool("ft_search_trains", {**ARGS, "date": "2026-12-01"})).structured_content
    assert data["no_result"] is True and data["trains"] == [] and "sale window" in data["note"]
    assert "in the past" in await error(client, "ft_search_trains", {**ARGS, "date": "2026-10-01"})


async def test_train_calendar_groups_days(client, api):
    api["/V1/DomesticRails/FareLookUp"] = fixture("trains_fare_lookup.json")
    data = (await client.call_tool("ft_train_calendar", {"origin": 1, "destination": 191})).structured_content
    by = {d["date"]: d for d in data["days"]}
    assert by["2026-10-07"] == {"date": "2026-10-07", "sold_out": True}
    assert by["2026-10-10"] == {"date": "2026-10-10", "cheapest_toman": 760000, "sold_out": False}
    assert data["cheapest"]["cheapest_toman"] <= 760000
    q = api.calls[0].url.params
    assert (q["fromStation"], q["toStation"]) == ("1", "191")


async def test_train_stops_with_wagon(client, api):
    code = "ab12" * 8
    api[f"/V1/DomesticRails/StopStations/{code}"] = fixture("trains_stops.json")
    api["/V1/DomesticRails/WagonInfo/580510315"] = fixture("trains_wagon.json")
    args = {"fare_source_code": code, "wagon_id": 580510315}
    data = (await client.call_tool("ft_train_stops", args)).structured_content
    assert data["stops"][0] == {"station_code": 1, "name_fa": "تهران", "depart": "07:25"}
    assert data["stops"][1]["arrive"] == "09:15" and data["stops"][1]["depart"] == "09:35"
    assert data["stops"][-1] == {"station_code": 191, "name_fa": "مشهد", "arrive": "16:10"}
    assert data["wagon"]["extra_services"][0]["price_toman"] == 142000 and "سیستم تهویه" in data["wagon"]["amenities"]
    assert "fare_source_code" in await error(client, "ft_train_stops", {"fare_source_code": "not hex!"})
