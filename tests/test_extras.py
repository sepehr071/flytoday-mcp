import re

import pytest
from conftest import body, fixture

pytestmark = pytest.mark.anyio

PLACES = "https://placesearch.flytoday.ir/api"


async def fails(client, pattern, name, args):
    """The tool must answer with an error whose text matches pattern."""
    result = await client.call_tool(name, args)
    assert result.is_error and re.search(pattern, result.content[0].text), result.content[0].text


def cip_routes(api):
    api["/v1/CIP/Airports"] = fixture("extras_cip_airports.json")
    api["/v1/CIP/Search"] = fixture("extras_cip_search.json")


async def test_ft_cip(client, api):
    cip_routes(api)
    args = {"airport": "IKA", "date": "2026-10-17", "adults": 2, "infants": 1}
    data = (await client.call_tool("ft_cip", args)).structured_content
    sent = body(api.calls[1])
    assert sent == {
        "lang": 0,
        "date": "2026-10-17",
        "airportCode": "ika",
        "flightDirectionType": 1,
        "flightType": 2,
        "adult": 2,
        "child": 0,
        "infant": 1,
    }
    assert api.calls[1].headers["x-proxy-origin"] == "https://api.flytoday.ir/api"
    assert (data["airport"], data["direction"], data["flight_type"]) == ("IKA", "departure", "international")
    # same lounge twice: Iranian passports only, every other passport (the site shows both)
    assert [o["nationality"] for o in data["offers"]] == ["only IR", "all except IR"]
    offer = data["offers"][0]
    assert offer["passengers"][0] == {
        "passenger": "adult",
        "quantity": 1,  # the fixture was recorded with one adult
        "price_per_passenger_toman": 9900000,
        "tax_toman": 900000,
    }
    assert offer["total_toman"] == 9900000
    assert {"type": "Wheelchair", "title": "خدمات ویژه، ویلچر", "adult_price_toman": 2970000, "per": "PerPax"} in offer[
        "add_ons"
    ]
    assert offer["cancellation_rules"] and offer["notes"]


async def test_ft_cip_arrival_domestic_mhd(client, api):
    cip_routes(api)
    await client.call_tool(
        "ft_cip", {"airport": "mhd", "date": "2026-10-17", "direction": "arrival", "flight_type": "domestic"}
    )
    sent = body(api.calls[1])
    assert (sent["airportCode"], sent["flightDirectionType"], sent["flightType"]) == ("mhd", 0, 1)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"airport": "THR", "date": "2026-10-17"}, "Airports with CIP: IKA, MHD"),
        ({"airport": "IKA", "date": "2026-10-17", "flight_type": "domestic"}, "only for international"),
        ({"airport": "IKA", "date": "2026-10-06"}, "in the past"),
    ],
)
async def test_ft_cip_rejects(client, api, args, message):
    cip_routes(api)
    await fails(client, message, "ft_cip", args)


async def test_ft_esim_plans_by_persian_and_english(client, api):
    api["/V1/ESim/GeoLocations"] = fixture("extras_esim_geo.json")
    api["/V1/ESim/Search"] = fixture("extras_esim_search.json")
    for name in ("ترکیه", "Turkey", "tr"):
        data = (await client.call_tool("ft_esim_plans", {"country": name, "limit": 3})).structured_content
        assert (data["slug"], data["country"], data["plans_total"]) == ("turkey", "ترکیه", 15)
        assert data["plans"][0] == {
            "fare_code": "MTAwLDE3ODUzNyxtZXJoYWJhLTdkYXlzLTFnYg",
            "title": "1 GB - 7 days",
            "data_gb": 1.0,
            "unlimited": False,
            "days": 7,
            "price_toman": 658735,
            "operator": "Merhaba",
            "coverage_countries": 1,
        }
        assert len(data["plans"]) == 3
    params = api.calls[1].url.params
    assert (params["packageType"], params["slug"], params["limit"], params["page"]) == (
        "COUNTRY",
        "turkey",
        "1000",
        "1",
    )


async def test_ft_esim_unlimited_and_unknown_country(client, api):
    api["/V1/ESim/GeoLocations"] = fixture("extras_esim_geo.json")
    api["/V1/ESim/Search"] = fixture("extras_esim_search.json")
    data = (await client.call_tool("ft_esim_plans", {"country": "turkey", "limit": 50})).structured_content
    unlimited = [p for p in data["plans"] if p["unlimited"]]
    assert unlimited and all(p["data_gb"] is None for p in unlimited)
    assert [p["price_toman"] for p in data["plans"]] == sorted(p["price_toman"] for p in data["plans"])
    await fails(client, "No eSIM country matches", "ft_esim_plans", {"country": "atlantis"})


async def test_ft_visa_list(client, api):
    api["/v2/Visa/Search"] = fixture("extras_visa_search.json")
    data = (await client.call_tool("ft_visa", {})).structured_content
    by_slug = {c["slug"]: c for c in data["countries"]}
    assert data["total"] == len(by_slug) == 10
    assert by_slug["Pakistan"] == {
        "country": "Pakistan",
        "name_fa": "پاکستان",
        "slug": "Pakistan",
        "visa_type": "توریستی",
        "from_price": "90",
        "price_currency": "USD",
        "processing_time": "از 2 روز تا 14 روز",
    }
    assert by_slug["Thailand"]["price_currency"] == "EUR"
    assert by_slug["Iran"]["price_toman"] == 1000000 and by_slug["Iran"]["price_currency"] == "IRR"
    assert by_slug["Dubai"]["from_price"] is None  # '_' means no public price
    assert api.calls[0].method == "POST"


async def test_ft_visa_country(client, api):
    api["/v2/Visa/Search"] = fixture("extras_visa_search.json")
    api["/v3/Visa/VisaDetail"] = fixture("extras_visa_detail.json")
    for name in ("pakistan", "پاکستان", "PK"):
        data = (await client.call_tool("ft_visa", {"country": name})).structured_content
        assert (data["country"], data["slug"], data["processing_time"]) == (
            "Pakistan",
            "Pakistan",
            "از 2 روز تا 14 روز",
        )
        assert data["visa_types"][0] == {
            "visa_type": "توریستی",
            "duration": "1 ماهه",
            "item_id": 80,
            "price_currency": "USD",
            "adult_price": 90,
            "child_price": 90,
            "infant_price": 90,
            "adult_price_toman": 24835500,
            "child_price_toman": 24835500,
            "infant_price_toman": 24835500,
            "exchange_rate_toman": 275950,
            "condition": "پس از اقدام ویزا ، امکان مصاحبه از سمت کنسولگری می باشد .",
        }
        assert data["documents"][0]["category"] == "پاسپورت" and data["documents"][0]["items"]
        assert data["intro"].startswith("سفر به پاکستان") and "<" not in data["intro"]
    assert api.calls[1].url.params["countryName"] == "Pakistan"
    await fails(client, "No visa service for 'narnia'", "ft_visa", {"country": "narnia"})


async def test_ft_activities(client, api):
    api["/Activity/Search"] = fixture("extras_activity_regions.json")
    api["/v1/Activity/search"] = fixture("extras_activity_prices.json")
    data = (
        await client.call_tool("ft_activities", {"query": "Dubai", "date_from": "2026-10-20", "limit": 4})
    ).structured_content
    full = (await client.call_tool("ft_activities", {"region_id": 1234, "limit": 50})).structured_content
    assert api.calls[0].headers["x-proxy-origin"] == PLACES
    assert body(api.calls[0]) == {"searchTerm": "Dubai", "isDomestic": False, "pageNumber": 1, "pageSize": 10}
    assert body(api.calls[1]) == {"locationId": 1234, "dateFrom": "2026-10-20", "dateTo": "2026-11-19"}
    # a range starting today comes back empty upstream: the default starts tomorrow (today pinned to 2026-10-07)
    assert body(api.calls[2]) == {"locationId": 1234, "dateFrom": "2026-10-08", "dateTo": "2026-11-07"}
    await client.call_tool("ft_activities", {"region_id": 1234, "date_from": "2026-10-07"})
    assert body(api.calls[3])["dateFrom"] == "2026-10-08"
    assert data["region"] == {"region_id": 1234, "name": "Dubai", "country": "United Arab Emirates"}
    assert [r["name"] for r in data["other_regions"]] == ["Dubai Marina", "Dubai Coast", "Downtown Dubai"]
    assert data["other_regions"][0]["region_id"] == 231
    prices = [a["price_toman"] for a in data["activities"]]
    assert len(prices) == 4 and prices == sorted(prices)
    half_day = next(a for a in full["activities"] if a["activity_id"] == 13040)
    assert half_day["price_toman"] == 3704731 and half_day["list_price_toman"] == 4038526
    assert half_day["free_cancellation"] is False
    assert data["priced_total"] == 12


async def test_ft_activities_persian_query_resolves_from_segments(client, api):
    api["/Activity/Search"] = fixture("extras_activity_locations.json")
    api["/v1/Activity/search"] = fixture("extras_activity_prices.json")
    data = (await client.call_tool("ft_activities", {"query": "دبی"})).structured_content
    assert data["region"] == {"region_id": 1234, "name": "Dubai", "country": "United Arab Emirates"}
    assert data["other_regions"] == []


async def test_ft_activities_region_id_skips_lookup(client, api):
    api["/v1/Activity/search"] = fixture("extras_activity_prices.json")
    data = (await client.call_tool("ft_activities", {"region_id": 1234})).structured_content
    assert len(api.calls) == 1 and data["region"] == {"region_id": 1234}


async def test_ft_activities_rejects(client, api):
    for args, message in [
        ({}, "query"),
        ({"query": "Dubai", "date_from": "2026-10-06"}, "in the past"),
        ({"query": "Dubai", "date_from": "2026-10-10", "date_to": "2026-10-09"}, "date_to must be"),
        ({"query": "Dubai", "date_from": "2026-10-10", "date_to": "2027-03-01"}, "date_to must be"),
    ]:
        await fails(client, message, "ft_activities", args)
    assert api.calls == []
    api["/Activity/Search"] = {"locations": []}
    await fails(client, "No activity destination", "ft_activities", {"query": "Nowhere"})


async def test_ft_activity(client, api):
    api["/v1/Activity/details"] = fixture("extras_activity_details.json")
    api["/v1/Activity/Content"] = fixture("extras_activity_content.json")
    data = (await client.call_tool("ft_activity", {"activity_id": 13049})).structured_content
    details = next(c for c in api.calls if c.url.path.endswith("/details"))
    assert body(details) == {"activityId": 13049, "dateFrom": "2026-10-10", "dateTo": "2026-12-06"}
    content = next(c for c in api.calls if c.url.path.endswith("/Content"))
    assert content.url.params["activityId"] == "13049"
    assert data["name"] == "Desert Safari, Dune Bashing and BBQ Dinner"
    assert (data["available"], data["price_toman"], data["list_price_toman"]) == (True, 10116737, 11466840)
    assert data["discount_percent"] == 11 and data["region"] == "Dubai"
    assert data["options"][0] == {
        "name": "Shared Tour  - Budget Camp",
        "languages": ["انگلیسی"],
        "price_toman": 10116737,
        "free_cancellation": False,
    }
    assert data["available_days"] == 12 and data["cheapest_days"][0]["price_toman"] == 10116737
    assert data["summary"] and data["highlights"]


async def test_ft_activity_unavailable_and_past(client, api):
    api["/v1/Activity/details"] = {
        "searchId": 1,
        "amountsFrom": None,
        "itineraries": [],
        "activityAmountsFromCalendar": {},
    }
    api["/v1/Activity/Content"] = fixture("extras_activity_content.json")
    data = (await client.call_tool("ft_activity", {"activity_id": 13049})).structured_content
    assert (data["available"], data["price_toman"], data["options"], data["cheapest_days"]) == (False, None, [], [])
    await fails(client, "in the past", "ft_activity", {"activity_id": 13049, "date_from": "2026-01-01"})
