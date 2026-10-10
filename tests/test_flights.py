import httpx
import pytest
from conftest import body, fixture

pytestmark = pytest.mark.anyio

SPLIT = "/v1/Flight/SplittedSearch"
DATA = "/v1/Flight/SearchData/49389403"
ARGS = {"origin": "thr", "destination": "MHD", "date": "2026-10-21"}


async def call(client, name, args):
    return (await client.call_tool(name, args)).structured_content


async def test_ft_find_airport_query(client, api):
    api["/flight/search"] = fixture("flights_locations.json")
    data = await call(client, "ft_find_airport", {"query": "استانبول", "limit": 2})
    assert data["places"][0] == {
        "code": "IST",
        "type": "City",
        "name": "استانبول",
        "country": "ترکیه",
        "airports": [
            {"code": "IST", "type": "Airport", "name": "فرودگاه استانبول", "city": "استانبول", "country": "ترکیه"}
        ],
    }
    assert len(data["places"]) == 2
    req = api.calls[0]
    assert req.headers["x-proxy-origin"] == "https://placesearch.flytoday.ir/api"
    assert body(req) == {"searchTerm": "استانبول", "isDomestic": False, "pageNumber": 1, "pageSize": 2}


async def test_ft_find_airport_popular(client, api):
    api["/Flight/PopularLocations"] = lambda r: httpx.Response(
        200,
        json=fixture(
            "flights_popular_dom.json" if r.url.params["isDomestic"] == "true" else "flights_popular_intl.json"
        ),
    )
    data = await call(client, "ft_find_airport", {})
    assert data["popular_domestic"][0] == {"code": "THR", "name": "Tehran", "name_fa": "تهران", "country": "Iran"}
    assert data["popular_international"] and data["popular_international"][0]["code"]
    assert {c.headers["x-proxy-origin"] for c in api.calls} == {"https://placesearch.flytoday.ir/api"}


async def test_ft_search_flights_one_way(client, api):
    api[SPLIT] = fixture("flights_search.json")
    data = await call(client, "ft_search_flights", {**ARGS, "limit": 5})
    assert data["search_id"] == 49389403
    assert data["paging"] == {"page": 1, "page_size": 5, "total": 10, "pages": 2}
    assert data["cheapest_toman"] == 11500000
    first = data["flights"][0]
    assert first == {
        "airline": "Ava Air",
        "airline_fa": "آوا ایر",
        "airline_code": "AXV",
        "flight_no": "AXV7702",
        "from": "THR",
        "to": "MHD",
        "departure": "2026-10-21T07:00:00",
        "arrival": "2026-10-21T08:30:00",
        "duration_min": 90,
        "stops": 0,
        "seats_left": 6,
        "cabin": "اکونومی",
        "type": "charter",
        "price_toman": 11500000,  # API Rial / 10
        "total_toman": 11500000,
        "baggage": "20 KG (1PC)",
        "fare_source_code": fixture("flights_search.json")["pricedItineraries"][0]["fareSourceCode"],
        "key": "6d4fe324adfe4d08fc877b3038dd2b73cad8e2ff0e939936f3703cfc9f973822",
    }
    prices = [f["price_toman"] for f in data["flights"]]
    assert prices == sorted(prices)
    req = api.calls[0]
    assert req.method == "POST" and req.url.params["sortBy"] == "Cheapest"
    assert (req.url.params["pageNumber"], req.url.params["pageSize"], req.url.params["index"]) == ("1", "5", "0")
    assert "airlineCodes" not in req.url.params and "stopQuantity" not in req.url.params
    sent = body(req)
    assert sent["adultCount"] == 1 and sent["travelPreference"] == {
        "cabinType": "Y",
        "maxStopsQuantity": "All",
        "airTripType": "OneWay",
    }
    assert sent["originDestinationInformations"] == [
        {
            "originLocationCode": "THR",
            "originType": "1",
            "destinationLocationCode": "MHD",
            "destinationType": "1",
            "departureDateTime": "2026-10-21",
        }
    ]


async def test_ft_search_flights_party_total(client, api):
    api[SPLIT] = fixture("flights_search_party.json")
    args = {**ARGS, "adults": 2, "children": 1, "infants": 1, "cabin": "business"}
    data = await call(client, "ft_search_flights", args)
    first = data["flights"][0]
    assert first["price_toman"] == 11878100  # one adult
    assert first["total_toman"] == 34751400  # 2 adults + child + infant
    sent = body(api.calls[0])
    assert (sent["adultCount"], sent["childCount"], sent["infantCount"]) == (2, 1, 1)
    assert sent["travelPreference"]["cabinType"] == "C"


async def test_ft_search_flights_filters(client, api):
    api[SPLIT] = fixture("flights_search.json")
    args = {
        **ARGS,
        "sort": "fastest",
        "airlines": ["ep", "is"],
        "direct_only": True,
        "ticket_type": "system",
        "depart_after": "18:00",
        "max_price_toman": 15000000,
    }
    await call(client, "ft_search_flights", args)
    q = api.calls[0].url.params
    assert q["sortBy"] == "Fastest"
    assert q.get_list("airlineCodes") == ["EP", "IS"]
    assert (q["stopQuantity"], q["ticketTypes"], q["maxPrice"]) == ("0", "System", "150000000")
    assert (q["outboundDepartureTime.from"], q["outboundDepartureTime.to"]) == ("18:00:00", "23:59:00")
    assert q["outboundDepartureTime.isValid"] == "true"
    await call(client, "ft_search_flights", {**ARGS, "sort": "earliest"})
    assert api.calls[1].url.params["sortBy"] == "EarliestDepartureOutbound"


async def test_ft_search_flights_no_flights(client, api):
    api[SPLIT] = lambda r: httpx.Response(204)
    data = await call(client, "ft_search_flights", ARGS)
    assert data["flights"] == [] and data["paging"]["total"] == 0 and "ft_flight_calendar" in data["note"]
    assert "search_id" not in data


async def test_ft_search_flights_page_two_uses_search_id(client, api):
    api[DATA] = fixture("flights_search.json")
    data = await call(client, "ft_search_flights", {**ARGS, "page": 2, "search_id": 49389403, "limit": 5})
    req = api.calls[0]
    assert req.method == "GET" and req.url.params["pageNumber"] == "2" and req.url.params["index"] == "0"
    assert data["search_id"] == 49389403 and data["paging"]["page"] == 2
    result = await client.call_tool("ft_search_flights", {**ARGS, "page": 2})
    assert result.is_error and "search_id" in result.content[0].text and len(api.calls) == 1


async def test_ft_search_flights_round_trip(client, api):
    api[SPLIT] = fixture("flights_rt_out.json")
    api["/v1/Flight/SearchData/49389498"] = fixture("flights_rt_back.json")
    data = await call(client, "ft_search_flights", {**ARGS, "return_date": "2026-10-24", "limit": 5})
    out_key = data["flights"][0]["key"]
    assert data["flights"][0]["total_toman"] == 23820000  # cheapest round-trip total including this outbound
    assert data["return_for"] == out_key and data["return_date"] == "2026-10-24"
    assert [f["flight_no"] for f in data["return_flights"]] == ["IS4321", "B9965", "B9965"]
    assert data["return_flights"][0]["from"] == "MHD" and data["return_flights"][0]["total_toman"] == 23820000
    sent = body(api.calls[0])
    assert sent["travelPreference"]["airTripType"] == "Return"
    legs = sent["originDestinationInformations"]
    assert [(g["originLocationCode"], g["destinationLocationCode"], g["departureDateTime"]) for g in legs] == [
        ("THR", "MHD", "2026-10-21"),
        ("MHD", "THR", "2026-10-24"),
    ]
    second = api.calls[1].url.params
    assert (second["index"], second["key"]) == ("1", out_key)
    # returns of another outbound: straight to SearchData, no new search
    other = "a" * 64
    data = await call(
        client,
        "ft_search_flights",
        {**ARGS, "return_date": "2026-10-24", "search_id": 49389498, "outbound_key": other},
    )
    assert len(api.calls) == 3 and api.calls[2].url.params["key"] == other and data["return_for"] == other


async def test_ft_search_flights_validation(client, api):
    for args, msg in [
        ({**ARGS, "date": "2026-10-01"}, "past"),
        ({**ARGS, "return_date": "2026-10-20"}, "before"),
        ({**ARGS, "origin": "TEHRAN"}, "pattern"),
        ({**ARGS, "adults": 10}, "less than or equal to 9"),
        ({**ARGS, "outbound_key": "a" * 64}, "search_id"),
    ]:
        result = await client.call_tool("ft_search_flights", args)
        assert result.is_error and msg in result.content[0].text, args
    assert not api.calls


async def test_ft_flight_calendar(client, api):
    api["/V1/Flight/FareLookUp"] = fixture("flights_calendar.json")
    data = await call(client, "ft_flight_calendar", {**ARGS, "window": "month"})
    assert len(data["days"]) == 25
    assert data["days"][0] == {"date": "2026-10-07", "price_toman": 10638000, "airline": "TKN"}
    cheapest = min(d["price_toman"] for d in data["days"])
    assert data["cheapest"]["price_toman"] == cheapest
    assert [d["date"] for d in data["days"] if d.get("cheapest")] == [data["cheapest"]["date"]]
    sent = body(api.calls[0])
    assert sent == {
        "originLocationCode": "THR",
        "destinationLocationCode": "MHD",
        "departureDate": "2026-10-21",
        "returnDate": None,
        "lookUpType": 3,
    }
    await call(client, "ft_flight_calendar", {**ARGS, "window": "week"})
    assert body(api.calls[1])["lookUpType"] == 2


async def test_ft_flight_calendar_empty(client, api):
    api["/V1/Flight/FareLookUp"] = {"result": []}
    data = await call(client, "ft_flight_calendar", ARGS)
    assert data["days"] == [] and data["cheapest"] is None and "note" in data


async def test_ft_flight_details_domestic(client, api):
    api["/V1/flight/farerules"] = fixture("flights_farerules_dom.json")
    api["/V1/flight/baggages"] = fixture("flights_baggages_dom.json")
    data = await call(client, "ft_flight_details", {"fare_source_code": "6539363039376132383731"})
    assert [r["airline"] for r in data["rules"]] == ["AXV"]  # the empty WebFare row is dropped
    assert data["rules"][0]["route"] == "THR-MHD"
    cancel = data["rules"][0]["details"][0]
    assert cancel["category"] == "CANCELLATION" and "cancellation penalty" in cancel["text"]
    assert data["baggage"] == [{"from": "THR", "to": "MHD", "flight_no": "7702", "baggage": "20 KG (1PC)"}]
    assert "entry_notes" not in data
    assert {c.url.params["fareSourceCode"] for c in api.calls} == {"6539363039376132383731"}
    assert len(api.calls) == 2


async def test_ft_flight_details_international(client, api):
    api["/V1/flight/farerules"] = fixture("flights_farerules_intl.json")
    api["/V1/flight/baggages"] = fixture("flights_baggages_intl.json")
    api["/V1/Flight/AirTravelRestriction"] = fixture("flights_restriction.json")
    data = await call(client, "ft_flight_details", {"fare_source_code": "abcdef0123456789", "search_id": 49390000})
    tiers = data["rules"][0]["details"][0]["penalties"]
    assert tiers[0] == {"from": "زمان صدور", "to": "15 دقیقه پس از آن", "penalty": "0 %"}  # direction marks stripped
    assert [b["baggage"] for b in data["baggage"]] == ["20 KG (2PC)", "30 KG"]
    assert data["entry_notes"][0]["group"] == "Customs" and "iatatravelcentre" in data["entry_notes"][0]["text"]
    assert api.calls[-1].url.params["searchId"] == "49390000"
    # domestic fares answer 204 to the restriction call
    api["/V1/Flight/AirTravelRestriction"] = lambda r: httpx.Response(204)
    assert "entry_notes" not in await call(
        client, "ft_flight_details", {"fare_source_code": "abcdef0123456789", "search_id": 1}
    )
