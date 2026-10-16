"""Hotel and stay tools: place lookup, hotel search and rooms, reviews, villa / apartment (stay) search and calendar.

Hotels price in Rial for the WHOLE stay (tools output Toman, whole stay and per night). Stays price in Toman per
night already. The hotel availability endpoint is the most rate-limited call of the site: one call per tool use.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import http
from .http import PLACES, api, not_past, toman
from .registry import tool

MAX_NIGHTS = 30
SORTS = {"price": "Price", "score": "HighestScore"}
STAY_SORTS = {"cheapest": "cheapest", "discount": "mostDiscount"}
# Names the stay availability filter accepts (a wrong name is silently ignored by the API).
STAY_TYPES = {
    "villa": "Villa",
    "apartment": "Apartments",
    "ecotourism": "EcotourismResorts",
    "cottage": "Chalets",
    "suite": "Suite",
}

Date = Annotated[dt.date, Field(description="Gregorian date YYYY-MM-DD, e.g. '2026-10-21'.")]
Adults = Annotated[int, Field(ge=1, le=8, description="Adults in the (single) room.")]
ChildAges = Annotated[
    list[int] | None,
    Field(max_length=4, description="Ages (0-11) of the children, one entry each, e.g. [5, 8]. Omit for none."),
]
Intl = Annotated[bool, Field(description="true for a hotel outside Iran (id from ft_find_hotel_place international).")]
Country = Annotated[
    str | None,
    Field(pattern=r"^[A-Za-z]{2}$", description="ISO country code of the DESTINATION for international, e.g. 'TR'."),
]

# The site builds every hotel / stay cover photo as <cdn>/<id>/main.jpg (Portfolio imageLinks is null);
# Iranian hotels and stays use the domestic CDN, their ids mean other hotels on the foreign one.
IMAGE_CDN = {
    True: "https://cdn-a-hid.cdnfl2.ir/upload/hotelimagesdomestic",
    False: "https://cdn-a-hi.cdnfl2.ir/upload/hotelimages",
}

# (is_domestic, city id or region code) -> hotel id -> card. Mashhad's portfolio is 380 KB; names do not change.
_CARDS: dict[tuple[bool, str], dict[int, dict[str, Any]]] = {}
_LOCKS: dict[tuple[bool, str], asyncio.Lock] = {}


def _nights(check_in: dt.date, check_out: dt.date) -> int:
    not_past(check_in, "check_in")
    nights = (check_out - check_in).days
    if nights < 1:
        raise ToolError("check_out must be after check_in.")
    if nights > MAX_NIGHTS:
        raise ToolError(f"At most {MAX_NIGHTS} nights per search; got {nights}.")
    return nights


def _image(hotel_id: Any, domestic: bool = True) -> str | None:
    return f"{IMAGE_CDN[domestic]}/{hotel_id}/main.jpg" if hotel_id else None


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _card(h: dict[str, Any], domestic: bool = True) -> dict[str, Any]:
    """Portfolio entry (flat names) or V3/hotel/info `hotelInfo` ({en, fa} names) as one card.

    Abroad the site shows only the English name (some Persian names are wrong) and counts numberOfComment."""
    name = h.get("name")
    en, fa = (name.get("en"), name.get("fa")) if isinstance(name, dict) else (name, h.get("nameLocal"))
    return _clean(
        {
            "name": fa or en if domestic else en or fa,
            "name_en": en if domestic else None,
            "name_fa": None if domestic else fa,
            "stars": _float(h.get("rating")) or None,  # 0 = unrated
            "score": _float(h.get("reviewScore")),
            "reviews": (h.get("sumOfCommentsAndReview") if domestic else h.get("numberOfComment")) or None,
            "address": h.get("address"),
        }
    )


async def _cards(domestic: bool, place: str) -> dict[int, dict[str, Any]]:
    """Names, stars and scores for every hotel of a city (Iran) or region (abroad), from the portfolio the site
    itself loads with each search; once per place and process."""
    cache = _CARDS.setdefault((domestic, place), {})
    async with _LOCKS.setdefault((domestic, place), asyncio.Lock()):  # parallel first calls share one download
        if not cache:
            # ponytail: a big foreign region is one 5.5 MB reply (Istanbul, 7714 hotels), like on the site; per-hotel
            # V3/hotel/info was lighter but gave other scores and review counts than the site shows
            where = {"cityId": int(place)} if domestic else {"regionCode": place, "cityId": None}
            data = await api("/V1/hotel/Portfolio", method="POST", json={**where, "isDomestic": domestic})
            cache.update({h["id"]: _card(h, domestic) for h in (data or {}).get("hotelInfo") or []})
    return cache


def _avail_body(
    check_in: dt.date,
    check_out: dt.date,
    adults: int,
    child_ages: list[int] | None,
    international: bool,
    country_code: str | None,
    **where: Any,
) -> dict[str, Any]:
    if international and not country_code:
        raise ToolError("country_code (destination, e.g. 'TR') is required for international hotels.")
    ages = child_ages or []
    return {
        "checkIn": check_in.isoformat(),
        "checkOut": check_out.isoformat(),
        "hotelId": None,
        "cityId": None,
        **where,
        "occupancies": [{"adultCount": adults, "childCount": len(ages), "childAges": ages}],
        "dateLang": "fa",
        "NationalityId": "IR",
        "countryCode": country_code.upper() if international and country_code else "IR",
        "cityName": "",
        "hotelName": "",
        "key": "",
        "isDomestic": not international,
        "isJalali": True,
    }


def _hotel_place(loc: dict[str, Any]) -> dict[str, Any] | None:
    g = loc.get("geographies") or {}
    country = (g.get("country") or {}).get("id")
    extra = loc.get("extra") or {}
    if "hotel" in g:
        h = g["hotel"]
        row = {
            "kind": "hotel",
            "hotel_id": int(h["id"]),
            "city_id": (g.get("city") or {}).get("id"),
            "stars": _float(extra.get("rating")),
        }
    elif "region" in g:
        h = g["region"]
        row = {"kind": "region", "region_code": str(h["id"]), "hotels": extra.get("properties")}
    elif "city" in g:
        h = g["city"]
        row = {"kind": "city", "city_id": int(h["id"]), "hotels": extra.get("properties")}
    else:
        return None
    row.update(name_fa=h.get("nameFa"), name=h.get("name"), country_code=country)
    return _clean(row)


@tool("Find hotel or stay place")
async def ft_find_hotel_place(
    query: Annotated[
        str | None,
        Field(
            max_length=60, description="City, region or hotel name, Persian or English ('مشهد', 'Istanbul', 'رامسر')."
        ),
    ] = None,
    international: Annotated[
        bool, Field(description="Look for places outside Iran (hotels only; stays are Iran only).")
    ] = False,
    limit: Annotated[int, Field(ge=1, le=15, description="Max rows per list.")] = 8,
) -> dict[str, Any]:
    """Find the place ids the hotel and stay tools need. Which id goes where:

    `hotels` rows: kind 'city' (Iran) -> `city_id` for ft_search_hotels; kind 'region' (abroad, with international=true)
    -> `region_code` plus `country_code` for ft_search_hotels(international=true); kind 'hotel' -> `hotel_id` for
    ft_hotel and ft_hotel_reviews. `stay_regions` rows (Iran only, villas and apartments) -> `region_code` for
    ft_search_stays; they are NOT the same ids as the hotel city ids. Without a query it lists the popular hotel
    places (and popular stay areas, which have no region_code: search them by name).
    """
    domestic = not international
    if query and query.strip():
        term = query.strip()
        calls = [
            api(
                "/Hotel/Search",
                {"searchTerm": term, "pageNumber": 1, "pageSize": limit, "isDomestic": str(domestic).lower()},
                origin=PLACES,
            )
        ]
        if domestic:
            calls.append(
                api(
                    "/Accommodation/SmartSearch",
                    origin=PLACES,
                    method="POST",
                    json={"searchTerm": term, "isDomestic": True},
                )
            )
        hotel_data, *stay_data = await asyncio.gather(*calls)
    else:
        hotel_data = await api("/Hotel/PopularPlace", origin=PLACES)
        stay_data = [None]
        out_popular: list[dict[str, Any]] = []
        if domestic:
            popular = await api("/Accommodation/PopularPlace", origin=PLACES)
            for loc in (popular or {}).get("locations") or []:
                r = (loc.get("geographies") or {}).get("region") or {}
                out_popular.append(
                    _clean(
                        {
                            "name_fa": r.get("nameFa"),
                            "name": r.get("name"),
                            "properties": (loc.get("extra") or {}).get("properties"),
                        }
                    )
                )
    locs = [
        loc
        for loc in (hotel_data or {}).get("locations") or []
        if bool(loc.get("isDomestic")) == domestic  # the popular list mixes Iran and abroad
    ]
    out: dict[str, Any] = {"hotels": [p for loc in locs[:limit] if (p := _hotel_place(loc))]}
    if domestic and query and query.strip():
        regions = [x for x in (stay_data[0] or []) if x.get("type") == 2]
        out["stay_regions"] = [
            _clean(
                {
                    "region_code": x.get("sourceId"),
                    "name_fa": x.get("nameFa"),
                    "name": x.get("name"),
                    "province": (x.get("province") or {}).get("nameFa"),
                    "properties": x.get("propertyCount"),
                }
            )
            for x in regions[:limit]
        ]
    elif domestic:
        out["popular_stay_areas"] = out_popular[:limit]
    if not out["hotels"] and not out.get("stay_regions"):
        out["note"] = "No place matches; try a shorter or Persian name, or set international accordingly."
    return out


@tool("Search hotels")
async def ft_search_hotels(
    check_in: Date,
    check_out: Date,
    city_id: Annotated[
        int | None, Field(ge=1, description="Iranian city id from ft_find_hotel_place, e.g. 10 (Mashhad).")
    ] = None,
    region_code: Annotated[
        str | None,
        Field(
            pattern=r"^\d{1,10}$", description="Foreign region code from ft_find_hotel_place, e.g. '178267' (Istanbul)."
        ),
    ] = None,
    adults: Adults = 2,
    children_ages: ChildAges = None,
    international: Intl = False,
    country_code: Country = None,
    sort: Annotated[
        Literal["price", "score"], Field(description="price = cheapest first, score = best guest score first.")
    ] = "price",
    stars: Annotated[list[int] | None, Field(max_length=5, description="Only these star ratings, e.g. [4, 5].")] = None,
    refundable_only: Annotated[bool, Field(description="Only offers with free cancellation.")] = False,
    max_price_toman: Annotated[
        int | None, Field(ge=1, description="Max price of the WHOLE stay in Toman, e.g. 30000000.")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=25, description="Hotels per page.")] = 10,
    page: Annotated[int, Field(ge=1, le=20, description="Page number, 1-based.")] = 1,
) -> dict[str, Any]:
    """Priced hotels of a city (Iran) or region (abroad) for dates, one row per hotel with its cheapest room offer.

    Give city_id for Iran, or region_code + country_code with international=true. price_toman is the whole stay
    for one room (per_night_toman = price / nights); image is the cover photo. Next: ft_hotel(hotel_id) or
    ft_hotel_reviews. Rows with is_stay=true are apartments or villas: open them with ft_stay(property_id).
    rooms_left is given only when the site counts rooms. Captcha-limited hard per IP: search once, then
    filter and page; never loop.
    """
    nights = _nights(check_in, check_out)
    if international:
        if not region_code:
            raise ToolError(
                "region_code is required for international hotels (ft_find_hotel_place international=true)."
            )
        where, place = {"regionCode": region_code}, region_code
    else:
        if not city_id:
            raise ToolError("city_id is required for Iranian hotels (ft_find_hotel_place).")
        where, place = {"cityId": city_id}, str(city_id)
    body = _avail_body(check_in, check_out, adults, children_ages, international, country_code, **where)
    params = {
        "pageNumber": page,
        "pageSize": limit,
        "sortMode": SORTS[sort],
        "selectedStarRatings": stars or None,
        "requireRefundable": "true" if refundable_only else None,
        "maxPrice": max_price_toman * 10 if max_price_toman else None,  # Rial for the whole stay
    }
    cards = await _cards(not international, place)  # first: never waste the rate-limited search on a failure
    data = await api("/V3/hotel/Availability", params, method="POST", json=body) or {}
    items = data.get("pricedItineraries") or []
    hotels = []
    for i in items:
        card, room = cards.get(i["hotelId"], {}), (i.get("rooms") or [{}])[0]
        price = toman(i.get("price"))
        stay = bool(i.get("isAccommodation"))  # a stays property: the site links it to /stays/profile
        hotels.append(
            _clean(
                {
                    "hotel_id": None if stay else i["hotelId"],
                    "property_id": i["hotelId"] if stay else None,
                    "is_stay": stay or None,
                    **card,
                    "price_toman": price,
                    "per_night_toman": price // nights if price is not None else None,
                    "refundable": not i.get("nonRefundable"),
                    "meal": room.get("mealType"),
                    "room": room.get("name"),
                    "rooms_left": i.get("availableRoom") or None,  # 0 = not counted (the site still sells it)
                    "perks": i.get("promotion"),
                    "image": _image(i["hotelId"], not international),
                }
            )
        )
    out: dict[str, Any] = {
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "nights": nights,
        "hotel_search_id": data.get("hotelSearchId"),
        "total_hotels": (data.get("filter") or {}).get("resultCount"),
        "page": page,
        "hotels": hotels,
    }
    if not hotels:
        out["note"] = (
            "No hotels for these dates and filters; loosen the filters, try other dates or check country_code."
        )
    return out


def _cancellation(offer: dict[str, Any]) -> str:
    if offer.get("nonRefundable"):
        return "non-refundable"
    rules = (offer.get("policyCancellation") or {}).get("refundRules") or []
    steps = [(str(r.get("date"))[:10], toman(r.get("penaltyAmount")) or 0) for r in rules]
    penalties = [(d, p) for d, p in steps if p > 0]
    if not penalties:
        return "free cancellation"
    parts = [f"from {d} penalty {p}" for d, p in penalties[:4]]
    return ("free until " + penalties[0][0] + "; " if steps[0][1] == 0 else "") + ", ".join(parts) + " Toman"


@tool("Hotel rooms and prices")
async def ft_hotel(
    hotel_id: Annotated[
        int, Field(ge=1, description="Hotel id from ft_search_hotels or ft_find_hotel_place, e.g. 24.")
    ],
    check_in: Date,
    check_out: Date,
    adults: Adults = 2,
    children_ages: ChildAges = None,
    international: Intl = False,
    country_code: Country = None,
) -> dict[str, Any]:
    """One hotel for dates: every room and meal-plan offer with whole-stay price, rooms left and cancellation terms,
    the room details (size, beds, features) and the hotel basics (address, stars, score, facilities, check-in time).

    price_toman is the whole stay for one room; per_night_toman = price / nights. Offers are cheapest first,
    sold-out ones (rooms_left 0) last. Use after ft_search_hotels; the user books on flytoday.ir. Rate limited like
    ft_search_hotels: one availability call per use.
    """
    nights = _nights(check_in, check_out)
    body = _avail_body(check_in, check_out, adults, children_ages, international, country_code)
    body["hotelId"] = hotel_id
    data = await api("/V3/hotel/Availability", method="POST", json=body) or {}
    offers_raw = data.get("pricedItineraries") or []
    search_id = data.get("hotelSearchId")
    calls = [api("/V3/hotel/info", method="POST", json={"hotelId": hotel_id, "isDomestic": not international})]
    if offers_raw and search_id:
        calls.append(api("/v1/Hotel/RoomInfo", method="POST", json={"hotelSearchId": search_id, "language": "fa"}))
    info_data, *room_data = await asyncio.gather(*calls)
    info = (info_data or {}).get("hotelInfo") or {}
    room_info = {r["roomInfoId"]: r for r in ((room_data[0] if room_data else None) or {}).get("roomInfo") or []}

    offers, used = [], {}
    for o in sorted(offers_raw, key=lambda o: (not o.get("availableRoom"), o.get("price") or 0)):
        rooms = o.get("rooms") or [{}]
        price = toman(o.get("price"))
        ids = [r.get("roomInfoId") for r in rooms]
        used.update({i: room_info[i] for i in ids if i in room_info})
        offers.append(
            _clean(
                {
                    "room": " + ".join(str(r.get("name")) for r in rooms),
                    "room_id": ids[0] if len(ids) == 1 else None,
                    "meal": rooms[0].get("mealType"),
                    "rooms_left": o.get("availableRoom"),
                    "price_toman": price,
                    "per_night_toman": price // nights if price is not None else None,
                    "refundable": not o.get("nonRefundable"),
                    "cancellation": _cancellation(o),
                }
            )
        )
    policy = info.get("hotelPolicy") or {}
    out: dict[str, Any] = {
        "hotel_id": hotel_id,
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "nights": nights,
        "hotel_search_id": search_id,
        **_clean(
            {
                **_card(info),
                "city": (info.get("city") or {}).get("fa"),
                "latitude": _float(info.get("latitude")),
                "longitude": _float(info.get("longitude")),
                "image": _image(hotel_id, not international),
                "check_in_time": (policy.get("beginTime") or {}).get("fa"),
                "check_out_time": (policy.get("checkOutTime") or {}).get("fa"),
                "facilities": [
                    f["name"]["fa"] for f in info.get("topFacilities") or [] if (f.get("name") or {}).get("fa")
                ][:10],
                "scores": {
                    s["displayName"]: s["score"] for s in info.get("commentScores") or [] if s.get("displayName")
                },
                "perks": (offers_raw[0].get("promotion") if offers_raw else None),
            }
        ),
        "offers": offers[:20],
        "rooms": [
            _clean(
                {
                    "room_id": rid,
                    "name": r.get("name"),
                    "size_m2": r.get("squareMeters"),
                    "beds": r.get("bedGroups"),
                    "max_guests": r.get("maxAllowedTotal"),
                    "features": [t["name"] for t in r.get("topRoomFacilities") or []][:5],
                }
            )
            for rid, r in used.items()
        ],
    }
    if not offers:
        out["note"] = "No rooms available for these dates (or wrong country_code for an international hotel)."
    return out


@tool("Hotel reviews")
async def ft_hotel_reviews(
    hotel_id: Annotated[int, Field(ge=1, description="Hotel id, e.g. 24.")],
    international: Intl = False,
    page: Annotated[int, Field(ge=1, le=100, description="Page number, 1-based.")] = 1,
    order: Annotated[Literal["newest", "oldest"], Field(description="Review order.")] = "newest",
    limit: Annotated[int, Field(ge=1, le=20, description="Reviews per page.")] = 5,
    full_text: http.FullText = False,
) -> dict[str, Any]:
    """Guest reviews of a hotel: score (1-10), text, pros, cons, trip type and nights. Mostly Persian text.

    Use after ft_search_hotels / ft_hotel to judge a hotel; total_reviews tells how many pages exist.
    """
    data = (
        await api(
            "/v3/Comment/GetCommentList",
            {
                "resourceType": "ForeignHotel" if international else "DomesticHotel",
                "resourceId": hotel_id,
                "pageNumber": page,
                "pageSize": limit,
                "commentSource": "Flytoday",
                "orderByMethod": 1 if order == "oldest" else 0,
            },
        )
        or {}
    )
    reviews = [
        _clean(
            {
                "date": str(r.get("date"))[:10].replace("/", "-"),
                "score": r.get("averageScore"),
                "text": http.clip(r.get("text"), None if full_text else 400),
                "pros": http.clip(r.get("pros"), None if full_text else 200),
                "cons": http.clip(r.get("cons"), None if full_text else 200),
                "traveler": (r.get("author") or {}).get("typeEn")
                if (r.get("author") or {}).get("typeEn") != "None"
                else None,
                "nights": r.get("totalNightsStayed") or None,
            }
        )
        for r in data.get("reviews") or []
    ]
    out: dict[str, Any] = {
        "hotel_id": hotel_id,
        "page": page,
        "total_reviews": data.get("totalCount"),
        "reviews": reviews,
    }
    if not reviews:
        out["note"] = "No reviews on this page."
    return out


def _stay_row(i: dict[str, Any]) -> dict[str, Any]:
    info, pb = i.get("accommodationInfo") or {}, i.get("priceBreakdown") or {}
    free = str(i.get("freeCancellationDate") or "")
    nights = pb.get("nightlyRents") or []
    discounts = [n["discountPercentage"] for n in nights if n.get("discountPercentage")]
    reviews = info.get("reviewsCount") or 0
    std, extra = info.get("standardCapacity") or 0, info.get("extraCapacity") or 0
    return _clean(
        {
            "property_id": i.get("propertyId"),
            "name": info.get("nameFa") or info.get("name"),
            "name_en": info.get("name"),
            "type": info.get("accommodationName"),
            "city": info.get("cityValue"),
            "province": info.get("destinationValue"),
            "price_per_night_toman": i.get("price"),  # Toman already, unlike hotels
            "total_toman": pb.get("total"),
            "nights": pb.get("nights"),
            "guests": std or None,
            "max_guests": std + extra if std else None,
            "rooms": info.get("roomCount"),
            "score": _float(info.get("reviewScore")) if reviews else None,
            "reviews": reviews or None,
            "discount_percent": max(discounts) if discounts else None,
            "cancellation": i.get("cancellationType"),
            "free_cancellation_until": f"{free[:4]}-{free[4:6]}-{free[6:8]}" if len(free) == 8 else None,
            "pool": True if i.get("hasPool") else None,
            "pets_allowed": True if i.get("isPetAllowed") else None,
            "image": _image(i.get("propertyId")),
        }
    )


@tool("Search villas and stays")
async def ft_search_stays(
    check_in: Date | None = None,
    check_out: Date | None = None,
    region_code: Annotated[
        str | None,
        Field(
            pattern=r"^\d{1,6}$",
            description="Stay region code from ft_find_hotel_place `stay_regions`, e.g. '149' (Ramsar). Omit for all Iran.",
        ),
    ] = None,
    guests: Annotated[int, Field(ge=1, le=30, description="Number of guests.")] = 2,
    type: Annotated[
        Literal["villa", "apartment", "ecotourism", "cottage", "suite"] | None,
        Field(description="Property type; omit for all types."),
    ] = None,
    sort: Annotated[
        Literal["cheapest", "discount"], Field(description="cheapest = lowest nightly price first.")
    ] = "cheapest",
    max_price_toman: Annotated[
        int | None, Field(ge=1, description="Max price PER NIGHT in Toman, e.g. 5000000.")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=25, description="Properties per page.")] = 10,
    page: Annotated[int, Field(ge=1, le=50, description="Page number, 1-based.")] = 1,
) -> dict[str, Any]:
    """Villas, apartments and other private stays in Iran for dates, cheapest first.

    Give both dates (strongly advised: only properties free on the dates are listed and total_toman is filled)
    or none (base prices). price_per_night_toman and total_toman are Toman (the stay API is not in Rial like
    hotels). property_id feeds ft_stay (calendar, details, reviews). Prices change a lot by date.
    """
    if (check_in is None) != (check_out is None):
        raise ToolError("Give both check_in and check_out, or neither.")
    nights = _nights(check_in, check_out) if check_in and check_out else None
    body: dict[str, Any] = {
        "occupancyCount": guests,
        "skip": (page - 1) * limit,
        "take": limit,
        "sort": STAY_SORTS[sort],
        "regionCode": region_code,
        "checkIn": check_in.isoformat() if check_in else None,
        "checkOut": check_out.isoformat() if check_out else None,
        "includeMap": False,
        "accommodationTypes": [STAY_TYPES[type]] if type else None,
        "maxPrice": max_price_toman,
    }
    data = (
        await api(
            "/v1/Accommodation/Availability", method="POST", json={k: v for k, v in body.items() if v is not None}
        )
        or {}
    )
    stays = [_stay_row(i) for i in data.get("pricedItineraries") or []]
    out: dict[str, Any] = {
        "check_in": check_in.isoformat() if check_in else None,
        "check_out": check_out.isoformat() if check_out else None,
        "nights": nights,
        "total_matching": data.get("totalCount"),
        "page": page,
        "stays": stays,
    }
    out = _clean(out)
    if not stays:
        out["note"] = (
            "No stays for these dates and filters (only properties free on every night are listed); try other dates."
        )
    return out


@tool("Stay details and calendar")
async def ft_stay(
    property_id: Annotated[int, Field(ge=1, description="Property id from ft_search_stays, e.g. 3804.")],
    from_date: Annotated[dt.date | None, Field(description="First calendar day YYYY-MM-DD; default today.")] = None,
    to_date: Annotated[
        dt.date | None, Field(description="Last calendar day; default 14 days after from_date, at most 45.")
    ] = None,
    guests: Annotated[int, Field(ge=1, le=30, description="Number of guests (changes the price).")] = 2,
    full_text: http.FullText = False,
) -> dict[str, Any]:
    """One villa / apartment: details (capacity, rooms, facilities, rules, host), the per-night price calendar with
    blocked days, and the review summary with the latest reviews.

    Calendar prices are Toman per night for that date (the base price in the details is only a reference).
    A day is available=false when the host blocked it or it is already reserved. The user books on flytoday.ir.
    """
    start = from_date or http.today()
    end = to_date or start + dt.timedelta(days=14)
    not_past(start, "from_date")
    if end < start:
        raise ToolError("to_date must not be before from_date.")
    if (end - start).days > 45:
        raise ToolError("The calendar covers at most 45 days per call.")
    info_data, cal, rev = await asyncio.gather(
        api("/v1/Accommodation/Info", method="POST", json={"propertyId": property_id, "isDomestic": True}),
        api(
            "/v1/Accommodation/Calendar",
            {
                "propertyId": property_id,
                "fromDate": start.isoformat(),
                "endDate": end.isoformat(),
                "occupancyCount": guests,
            },
        ),
        api(
            "/v1/Accommodation/Reviews",
            {"propertyId": property_id, "pageSize": 3, "pageNumber": 1, "sort": "Newest"},
        ),
    )
    i = (info_data or {}).get("accommodationInfo") or {}
    policy = i.get("accommodationPolicy") or {}
    facilities = [f["nameFa"] for f in i.get("importantFacilities") or [] if f.get("hasFacility") and f.get("nameFa")]
    for cat in i.get("facilityCategory") or []:
        for sub in cat.get("subCategories") or []:
            facilities += sub.get("facilities") or []
    days = []
    for d in (cal or {}).get("dates") or []:
        day = str(d.get("date"))[:10]
        if start.isoformat() <= day <= end.isoformat():
            days.append(
                _clean(
                    {
                        "date": day,
                        "price_toman": d.get("price"),
                        "available": bool(d.get("hasData")) and not d.get("isBlockout") and not d.get("isReserve"),
                        "min_nights": d.get("minStayNight") or None,
                    }
                )
            )
    summary = (rev or {}).get("summary") or {}
    out: dict[str, Any] = {
        "property_id": property_id,
        **_clean(
            {
                "name": i.get("nameFa") or i.get("name"),
                "name_en": i.get("name"),
                "type": i.get("accommodation"),
                "city": i.get("cityFa"),
                "province": i.get("provinceFa"),
                "rent": i.get("rentTypeText"),
                "area": i.get("areaTextureText"),
                "guests": i.get("mainOccupancy"),
                "max_guests": i.get("maxAdult"),
                "bedrooms": i.get("bedroomCount"),
                "bathrooms": i.get("bathroomCount"),
                "building_m2": i.get("buildingMeterage"),
                "check_in_time": policy.get("beginTime"),
                "check_out_time": policy.get("checkOutTime"),
                "image": _image(property_id),
                "base_price_per_night_toman": i.get("price"),
                "cancellation": (cal or {}).get("cancellationType"),
                "host": (i.get("supplier") or {}).get("hostBadgeType"),
                "facilities": facilities[:25],
                "rules": [
                    f"{r['titleFa']}: {r['optionTitleFa']}"
                    for r in i.get("accommodationRules") or []
                    if r.get("titleFa")
                ],
            }
        ),
        "calendar": days,
        "reviews_total": (rev or {}).get("totalCount"),
        "review_score": summary.get("overallScore") if summary.get("reviewCount") else None,
        "latest_reviews": [
            _clean(
                {
                    "date": str(r.get("createdAt"))[:10],
                    "score": r.get("overallRating"),
                    "text": http.clip(r.get("text"), None if full_text else 400),
                    "nights": r.get("nightCount") or None,
                }
            )
            for r in (rev or {}).get("reviews") or []
        ],
    }
    return _clean(out)
