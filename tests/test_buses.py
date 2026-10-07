import pytest
from conftest import body, fixture

pytestmark = pytest.mark.anyio


async def error(client, name, args) -> str:
    """The text of a failed tool call."""
    result = await client.call_tool(name, args)
    assert result.is_error
    return result.content[0].text


SEARCH = "/V1/Bus/Search"
ARGS = {"origin": "0001", "destination": "0002", "date": "2026-10-14"}


async def test_find_bus_city_search_and_popular(client, api):
    api["/Bus/Search"] = fixture("buses_cities.json")
    api["/Bus/BusPopularCities"] = fixture("buses_popular.json")
    data = (await client.call_tool("ft_find_bus_city", {"query": "اصف", "limit": 2})).structured_content
    assert data["cities"][0] == {"city_id": "0002", "name_fa": "اصفهان", "name": "Isfahan"}
    assert body(api.calls[0]) == {"searchTerm": "اصف", "pageSize": 2, "pageNumber": 0}
    popular = (await client.call_tool("ft_find_bus_city", {"limit": 10})).structured_content
    assert popular["cities"][0]["city_id"] == "0001"
    assert api.calls[1].url.params["count"] == "10"


async def test_search_buses_shapes_and_sorts(client, api):
    api[SEARCH] = fixture("buses_search.json")
    data = (await client.call_tool("ft_search_buses", ARGS)).structured_content
    assert (data["buses_listed"], data["matching"]) == (7, 6)  # the sold-out bus is hidden
    assert [b["departure"][-5:] for b in data["buses"]] == ["00:30", "01:00", "01:30", "10:00", "11:00", "11:30"]
    b = data["buses"][0]
    assert b["company"] == "همسفر چابکسواران پایانه بیهقی" and b["price_toman"] == 712000 and b["seats_left"] == 25
    assert b["from_terminal"] == "پایانه بیهقی ( تهران )" and b["facilities"] == ["VIP"]
    assert b["fare_source_code"] and data["search_id"]
    assert "continues_to" not in b and "full_price_toman" not in b
    assert data["buses"][3]["continues_to"]  # the bus goes on to another city
    assert body(api.calls[0]) == {
        "originDestinations": [{"originId": "0001", "destinationId": "0002", "departureDate": "2026-10-14"}]
    }
    cheap = (await client.call_tool("ft_search_buses", {**ARGS, "sort": "cheapest", "limit": 2})).structured_content
    assert [b["price_toman"] for b in cheap["buses"]] == [536500, 647000]
    every = (await client.call_tool("ft_search_buses", {**ARGS, "available_only": False})).structured_content
    assert every["matching"] == 7


async def test_search_buses_facility_filter(client, api):
    api[SEARCH] = fixture("buses_search.json")
    vip = (await client.call_tool("ft_search_buses", {**ARGS, "facility": "vip"})).structured_content
    assert vip["matching"] == 3 and all("VIP" in b["facilities"] for b in vip["buses"])
    mon = (await client.call_tool("ft_search_buses", {**ARGS, "facility": "monitor"})).structured_content
    assert mon["matching"] == 1
    none = (await client.call_tool("ft_search_buses", {**ARGS, "facility": "sleeper"})).structured_content
    assert none["buses"] == [] and "filters" in none["note"]


async def test_search_buses_validates_input(client, api):
    api[SEARCH] = fixture("buses_search.json")
    assert "in the past" in await error(client, "ft_search_buses", {**ARGS, "date": "2026-10-01"})
    assert "origin" in await error(client, "ft_search_buses", {**ARGS, "origin": "Tehran"})
    assert api.calls == []


async def test_bus_calendar_takes_the_daily_minimum(client, api):
    api["/V1/Bus/CalendarLookup"] = fixture("buses_calendar.json")
    data = (await client.call_tool("ft_bus_calendar", {"origin": "0001", "destination": "0002"})).structured_content
    assert data["days"] == [
        {"date": "2026-10-07", "price_toman": 393000},
        {"date": "2026-10-08", "price_toman": 393000},
        {"date": "2026-10-09", "price_toman": 530000},
        {"date": "2026-10-10", "price_toman": 552000},
    ]
    assert data["cheapest"]["date"] == "2026-10-07"
    assert dict(api.calls[0].url.params) == {"originId": "0001", "destinationId": "0002"}
