import httpx
import pytest
from conftest import body, fixture

from flytoday_mcp import hotels

pytestmark = pytest.mark.anyio

AVAIL = "/V3/hotel/Availability"
PORTFOLIO = "/V1/hotel/Portfolio"
HOTEL_ARGS = {"check_in": "2026-10-21", "check_out": "2026-10-23"}
SEARCH_ARGS = {**HOTEL_ARGS, "city_id": 10}


@pytest.fixture(autouse=True)
def fresh_cards():
    hotels._CARDS.clear()
    yield
    hotels._CARDS.clear()


async def error(client, name, args) -> str:
    result = await client.call_tool(name, args)
    assert result.is_error
    return result.content[0].text


def gateway_path(request: httpx.Request) -> str:
    return request.url.path.removeprefix("/api/gateway")


def hotel_routes(api):
    api[AVAIL] = fixture("hotels_search.json")
    api[PORTFOLIO] = fixture("hotels_portfolio.json")


async def test_find_place_domestic_separates_hotel_and_stay_ids(client, api):
    api["/Hotel/Search"] = fixture("hotels_place.json")
    api["/Accommodation/SmartSearch"] = fixture("hotels_smart.json")
    data = (await client.call_tool("ft_find_hotel_place", {"query": "mashhad", "limit": 3})).structured_content
    assert data["hotels"][0] == {
        "kind": "city",
        "city_id": 10,
        "hotels": "485",
        "name_fa": "مشهد",
        "name": "Mashhad",
        "country_code": "IR",
    }
    assert data["hotels"][1]["kind"] == "hotel" and data["hotels"][1]["hotel_id"] == 209
    assert data["hotels"][1]["stars"] == 5.0
    assert data["stay_regions"][0]["region_code"] == "149" and data["stay_regions"][0]["name"] == "Ramsar"
    hotel_call, stay_call = api.calls
    assert hotel_call.url.params["searchTerm"] == "mashhad" and hotel_call.url.params["isDomestic"] == "true"
    assert hotel_call.headers["x-proxy-origin"].startswith("https://placesearch")
    assert body(stay_call) == {"searchTerm": "mashhad", "isDomestic": True}


async def test_find_place_international_has_region_code_and_no_stays(client, api):
    api["/Hotel/Search"] = fixture("hotels_place_intl.json")
    result = await client.call_tool("ft_find_hotel_place", {"query": "Istanbul", "international": True})
    data = result.structured_content
    assert data["hotels"][0] == {
        "kind": "region",
        "region_code": "178267",
        "hotels": "7554",
        "name_fa": "استانبول و حومه",
        "name": "Istanbul (and vicinity)",
        "country_code": "TR",
    }
    assert "stay_regions" not in data and len(api.calls) == 1
    assert api.calls[0].url.params["isDomestic"] == "false"


async def test_find_place_popular_keeps_only_the_requested_side(client, api):
    api["/Hotel/PopularPlace"] = fixture("hotels_popular.json")
    api["/Accommodation/PopularPlace"] = fixture("hotels_stay_popular.json")
    dom = (await client.call_tool("ft_find_hotel_place", {})).structured_content
    assert [h["kind"] for h in dom["hotels"]] == ["city"] * 3 and dom["hotels"][0]["city_id"] == 1
    assert dom["popular_stay_areas"][0]["name"] == "North Beach"
    assert "region_code" not in dom["popular_stay_areas"][0]
    intl = (await client.call_tool("ft_find_hotel_place", {"international": True})).structured_content
    assert [h["kind"] for h in intl["hotels"]] == ["region"] * 3 and "popular_stay_areas" not in intl


async def test_search_hotels_request_units_and_name_join(client, api):
    hotel_routes(api)
    args = {
        **SEARCH_ARGS,
        "children_ages": [5],
        "stars": [4, 5],
        "refundable_only": True,
        "max_price_toman": 25_000_000,
        "limit": 4,
        "page": 2,
    }
    data = (await client.call_tool("ft_search_hotels", args)).structured_content
    portfolio, avail = api.calls
    assert body(portfolio) == {"cityId": 10, "isDomestic": True}
    q = avail.url.params
    assert (q["pageNumber"], q["pageSize"], q["sortMode"], q["requireRefundable"]) == ("2", "4", "Price", "true")
    assert q.get_list("selectedStarRatings") == ["4", "5"] and q["maxPrice"] == "250000000"  # Rial
    b = body(avail)
    assert b["cityId"] == 10 and b["hotelId"] is None and b["isDomestic"] is True and b["countryCode"] == "IR"
    assert b["occupancies"] == [{"adultCount": 2, "childCount": 1, "childAges": [5]}]
    assert (b["checkIn"], b["checkOut"]) == ("2026-10-21", "2026-10-23")
    assert data["nights"] == 2 and data["hotel_search_id"] == 15704764 and data["total_hotels"] == 222
    first = data["hotels"][0]
    assert first["hotel_id"] == 24 and first["name"] == "الماس 1 مشهد" and first["name_en"] == "Almas 1 Mashhad"
    assert first["stars"] == 4.0 and first["score"] == 7.7
    assert first["price_toman"] == 12_200_000 and first["per_night_toman"] == 6_100_000  # 122000000 Rial, 2 nights
    assert first["refundable"] is True and first["meal"] == "صبحانه" and first["rooms_left"] == 1
    assert [h["hotel_id"] for h in data["hotels"]] == [24, 209, 26, 230]


async def test_search_hotels_reuses_the_portfolio_cache(client, api):
    hotel_routes(api)
    await client.call_tool("ft_search_hotels", SEARCH_ARGS)
    await client.call_tool("ft_search_hotels", {**SEARCH_ARGS, "sort": "score"})
    assert [gateway_path(c) for c in api.calls] == [PORTFOLIO, AVAIL, AVAIL]
    assert api.calls[2].url.params["sortMode"] == "HighestScore"
    assert api.calls[2].url.params.get("selectedStarRatings") is None


async def test_search_hotels_international_cards_from_region_portfolio(client, api):
    search = fixture("hotels_search.json")
    for item, hotel_id in zip(search["pricedItineraries"], [12214366, 858346, 741221], strict=False):
        item["hotelId"] = hotel_id
    search["pricedItineraries"] = search["pricedItineraries"][:3]
    api[AVAIL] = search
    api[PORTFOLIO] = fixture("hotels_portfolio_foreign.json")
    args = {**HOTEL_ARGS, "region_code": "178267", "international": True, "country_code": "tr", "limit": 25}
    data = (await client.call_tool("ft_search_hotels", args)).structured_content
    assert [gateway_path(c) for c in api.calls] == [PORTFOLIO, AVAIL]  # one portfolio call, as on the site
    assert body(api.calls[0]) == {"regionCode": "178267", "cityId": None, "isDomestic": False}
    b = body(api.calls[1])
    assert (b["cityId"], b["regionCode"], b["countryCode"], b["isDomestic"]) == (None, "178267", "TR", False)
    avist, bufes, palmiye = data["hotels"]
    # the site shows English names abroad; scores and review counts as on the 2026-10-07 results page
    assert avist["name"] == "Avist Hotel Avcilar" and "score" not in avist
    assert (bufes["name"], bufes["score"], bufes["reviews"]) == ("Bufes Hotel", 3.2, 2)
    assert (palmiye["stars"], palmiye["score"], palmiye["reviews"], palmiye["name_fa"]) == (3.0, 5.4, 5, "پالمیه تکسیم")


async def test_search_hotels_validates_before_calling(client, api):
    hotel_routes(api)
    assert "in the past" in await error(client, "ft_search_hotels", {**SEARCH_ARGS, "check_in": "2026-10-01"})
    assert "after check_in" in await error(client, "ft_search_hotels", {**SEARCH_ARGS, "check_out": "2026-10-21"})
    assert "At most 30" in await error(client, "ft_search_hotels", {**SEARCH_ARGS, "check_out": "2026-12-01"})
    assert "city_id" in await error(client, "ft_search_hotels", HOTEL_ARGS)
    intl = {**HOTEL_ARGS, "international": True, "region_code": "178267"}
    assert "country_code" in await error(client, "ft_search_hotels", intl)
    assert api.calls == []


async def test_search_hotels_captcha_is_a_clear_tool_error(client, api):
    api[PORTFOLIO] = fixture("hotels_portfolio.json")
    api[AVAIL] = lambda r: httpx.Response(429, headers={"x-captcha-required": "true"}, json={"message": "Rate limit"})
    assert "captcha" in await error(client, "ft_search_hotels", SEARCH_ARGS)


async def test_search_hotels_empty_page_204(client, api):
    api[PORTFOLIO] = fixture("hotels_portfolio.json")
    api[AVAIL] = lambda r: httpx.Response(204)
    data = (await client.call_tool("ft_search_hotels", SEARCH_ARGS)).structured_content
    assert data["hotels"] == [] and "No hotels" in data["note"]


async def test_hotel_offers_join_roominfo_and_basics(client, api):
    api[AVAIL] = fixture("hotels_one.json")
    api["/v1/Hotel/RoomInfo"] = fixture("hotels_roominfo.json")
    api["/V3/hotel/info"] = fixture("hotels_info.json")
    data = (await client.call_tool("ft_hotel", {"hotel_id": 24, **HOTEL_ARGS})).structured_content
    avail = body(api.calls[0])
    assert avail["hotelId"] == 24 and avail["cityId"] is None and avail["isDomestic"] is True
    sent = {gateway_path(c): body(c) for c in api.calls[1:]}
    assert sent["/v1/Hotel/RoomInfo"] == {"hotelSearchId": 15704877, "language": "fa"}
    assert sent["/V3/hotel/info"] == {"hotelId": 24, "isDomestic": True}
    assert data["name"] == "الماس 1 مشهد" and data["stars"] == 4.0 and data["check_in_time"] == "14:00"
    assert data["hotel_search_id"] == 15704877 and "رستوران" in data["facilities"]
    prices = [o["price_toman"] for o in data["offers"]]
    assert prices == [12_200_000, 14_700_000, 14_700_000, 20_600_000, 25_400_000, 28_200_000]
    cheapest = data["offers"][0]
    assert cheapest["per_night_toman"] == 6_100_000 and cheapest["rooms_left"] == 1 and cheapest["room_id"] == "33078"
    assert cheapest["cancellation"].startswith("free until 2026-10-18; from 2026-10-18 penalty 3950000")
    assert [o["rooms_left"] for o in data["offers"]][-2:] == [0, 0]  # sold-out offers last
    room = next(r for r in data["rooms"] if r["room_id"] == "32931")
    assert room["name"] == "اتاق دو تخته دبل" and room["size_m2"] == 28 and room["beds"] == "1 دونفره استاندارد"
    assert {r["room_id"] for r in data["rooms"]} == {o["room_id"] for o in data["offers"]}
    assert len(str(data).encode()) < 15_000


async def test_hotel_no_offers_still_returns_basics(client, api):
    api[AVAIL] = lambda r: httpx.Response(204)
    api["/V3/hotel/info"] = fixture("hotels_info.json")
    data = (await client.call_tool("ft_hotel", {"hotel_id": 24, **HOTEL_ARGS})).structured_content
    assert data["offers"] == [] and data["name"] == "الماس 1 مشهد" and "No rooms" in data["note"]
    assert [gateway_path(c) for c in api.calls] == [AVAIL, "/V3/hotel/info"]  # no RoomInfo without offers


async def test_hotel_international_flags(client, api):
    api[AVAIL] = fixture("hotels_one.json")
    api["/v1/Hotel/RoomInfo"] = fixture("hotels_roominfo.json")
    api["/V3/hotel/info"] = fixture("hotels_info_foreign.json")
    args = {"hotel_id": 366714, "international": True, "country_code": "TR", **HOTEL_ARGS}
    await client.call_tool("ft_hotel", args)
    b = body(api.calls[0])
    assert (b["hotelId"], b["countryCode"], b["isDomestic"]) == (366714, "TR", False)
    info = next(c for c in api.calls if gateway_path(c) == "/V3/hotel/info")
    assert body(info) == {"hotelId": 366714, "isDomestic": False}
    bad = {"hotel_id": 1, "international": True, **HOTEL_ARGS}
    assert "country_code" in await error(client, "ft_hotel", bad)


async def test_hotel_reviews(client, api):
    api["/v3/Comment/GetCommentList"] = fixture("hotels_reviews.json")
    args = {"hotel_id": 24, "page": 2, "order": "oldest", "limit": 3}
    data = (await client.call_tool("ft_hotel_reviews", args)).structured_content
    assert dict(api.calls[0].url.params) == {
        "resourceType": "DomesticHotel",
        "resourceId": "24",
        "pageNumber": "2",
        "pageSize": "3",
        "commentSource": "Flytoday",
        "orderByMethod": "1",
    }
    assert data["total_reviews"] == 247 and len(data["reviews"]) == 3
    first = data["reviews"][0]
    assert first["date"] == "2026-07-15" and first["score"] == 8 and first["nights"] == 12
    await client.call_tool("ft_hotel_reviews", {"hotel_id": 366714, "international": True})
    q = api.calls[1].url.params
    assert q["resourceType"] == "ForeignHotel" and q["orderByMethod"] == "0"


async def test_search_stays_prices_are_toman_already(client, api):
    api["/v1/Accommodation/Availability"] = fixture("hotels_stays.json")
    args = {
        **HOTEL_ARGS,
        "region_code": "149",
        "guests": 4,
        "type": "villa",
        "max_price_toman": 20_000_000,
        "limit": 5,
        "page": 3,
    }
    data = (await client.call_tool("ft_search_stays", args)).structured_content
    assert body(api.calls[0]) == {
        "occupancyCount": 4,
        "skip": 10,
        "take": 5,
        "sort": "cheapest",
        "regionCode": "149",
        "checkIn": "2026-10-21",
        "checkOut": "2026-10-23",
        "includeMap": False,
        "accommodationTypes": ["Villa"],
        "maxPrice": 20_000_000,
    }
    assert data["total_matching"] == 11 and data["nights"] == 2
    first, villa = data["stays"][0], data["stays"][1]
    assert first["price_per_night_toman"] == 4_500_000 and first["total_toman"] == 9_000_000  # not divided by 10
    assert first["name"] == "برگل1 - تکخواب برگل 1" and first["type"] == "آپارتمان" and first["city"] == "رامسر"
    assert villa["property_id"] == 3804 and villa["pool"] is True and villa["free_cancellation_until"] == "2026-10-11"
    assert villa["guests"] == 6 and villa["max_guests"] == 10 and villa["cancellation"] == "Moderate"


async def test_search_stays_all_iran_without_dates(client, api):
    api["/v1/Accommodation/Availability"] = fixture("hotels_stays.json")
    data = (await client.call_tool("ft_search_stays", {"sort": "discount", "type": "apartment"})).structured_content
    assert body(api.calls[0]) == {
        "occupancyCount": 2,
        "skip": 0,
        "take": 10,
        "sort": "mostDiscount",
        "includeMap": False,
        "accommodationTypes": ["Apartments"],
    }
    assert "nights" not in data and data["stays"][0]["property_id"] == 4802
    assert "both check_in and check_out" in await error(client, "ft_search_stays", {"check_in": "2026-10-21"})
    past = {"check_in": "2026-10-01", "check_out": "2026-10-03"}
    assert "in the past" in await error(client, "ft_search_stays", past)


async def test_stay_details_calendar_and_reviews(client, api):
    api["/v1/Accommodation/Info"] = fixture("hotels_stay_info.json")
    api["/v1/Accommodation/Calendar"] = fixture("hotels_stay_calendar.json")
    api["/v1/Accommodation/Reviews"] = fixture("hotels_stay_reviews.json")
    args = {"property_id": 3804, "to_date": "2026-10-14", "guests": 4}
    data = (await client.call_tool("ft_stay", args)).structured_content
    calls = {gateway_path(c).rsplit("/", 1)[-1]: c for c in api.calls}
    assert body(calls["Info"]) == {"propertyId": 3804, "isDomestic": True}
    assert dict(calls["Calendar"].url.params) == {
        "propertyId": "3804",
        "fromDate": "2026-10-07",
        "endDate": "2026-10-14",
        "occupancyCount": "4",
    }
    assert calls["Reviews"].url.params["sort"] == "Newest"
    assert (
        data["name"] == "ساحلی طهرانچی" and data["max_guests"] == 6 and data["base_price_per_night_toman"] == 8_100_000
    )
    days = data["calendar"]
    assert [d["date"] for d in days][:2] == ["2026-10-07", "2026-10-08"] and days[-1]["date"] == "2026-10-14"
    assert days[0] == {"date": "2026-10-07", "price_toman": 9_000_000, "available": False}  # isBlockout
    assert days[2] == {"date": "2026-10-09", "price_toman": 7_000_000, "available": True}
    assert data["reviews_total"] == 1 and data["review_score"] == 9 and data["latest_reviews"][0]["score"] == 9
    assert data["cancellation"] == "Moderate" and data["check_in_time"] == "14:00" and data["host"] == "Verified"
    backwards = {"property_id": 1, "from_date": "2026-10-10", "to_date": "2026-10-09"}
    assert "must not be before" in await error(client, "ft_stay", backwards)
    assert "at most 45" in await error(client, "ft_stay", {"property_id": 1, "to_date": "2026-12-30"})


async def test_search_hotels_marks_stay_rows(client, api):
    """Apartments listed among hotels (isAccommodation) open with ft_stay; their availableRoom 0 means 'not counted'."""
    hotel_routes(api)
    data = fixture("hotels_search.json")
    data["pricedItineraries"][-1].update(isAccommodation=True, availableRoom=0)
    api["/V3/hotel/Availability"] = data
    row = (await client.call_tool("ft_search_hotels", SEARCH_ARGS)).structured_content["hotels"][-1]
    assert row["is_stay"] is True and row["property_id"] == 230
    assert "hotel_id" not in row and "rooms_left" not in row
