"""Flight tools: airport lookup, search (one-way, round trip, domestic and international), price calendar, fare details."""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import PLACES, ApiError, api, not_past, toman
from .registry import tool

Airport = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z]{3}$",
        description="IATA city code from ft_find_airport, e.g. 'THR' (covers IKA and THR), 'MHD', 'IST', 'DXB'.",
    ),
]
Date = Annotated[dt.date, Field(description="Gregorian date YYYY-MM-DD, e.g. '2026-10-23'.")]
HHMM = Annotated[str | None, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="Local time HH:MM, e.g. '18:00'.")]

SORTS = {"cheapest": "Cheapest", "fastest": "Fastest", "earliest": "EarliestDepartureOutbound"}
CABINS = {"economy": "Y", "business": "C"}
TICKET_TYPES = {"charter": "Charter", "system": "System"}
NO_FLIGHTS = "No flights found. Try another date (ft_flight_calendar shows which days have fares) or nearby airports."
DIRECTION_MARKS = dict.fromkeys([0x200E, 0x200F])  # left/right-to-right marks embedded in Persian text


def _clean(text: Any, limit: int = 1500) -> str:
    """Plain text: no HTML tags, no Unicode direction marks."""
    text = re.sub(r"<[^>]+>", " ", str(text or "")).translate(DIRECTION_MARKS)
    return re.sub(r"[ \t]+", " ", text).strip()[:limit]


def _place(p: dict[str, Any]) -> dict[str, Any]:
    out = {
        "code": (p.get("iata") or "").upper(),
        "type": p.get("type"),
        "name": p.get("name"),
        "city": p.get("cityName"),
        "country": p.get("countryName"),
    }
    airports = [_place(c) for c in p.get("children") or []]
    if airports:
        out["airports"] = airports
    return {k: v for k, v in out.items() if v}


@tool("Find airport or city")
async def ft_find_airport(
    query: Annotated[
        str | None,
        Field(
            max_length=60,
            description="City or airport name, Persian or English ('mashhad', 'استانبول'); omit for popular places.",
        ),
    ] = None,
    domestic_only: Annotated[bool, Field(description="Only Iranian places.")] = False,
    limit: Annotated[int, Field(ge=1, le=25, description="Max places.")] = 8,
) -> dict[str, Any]:
    """Find the IATA codes the flight tools need, for domestic and international places.

    Each hit has `code`, `type` (City or Airport) and, for a city, its `airports`. Use the city code
    (THR, IST) in ft_search_flights and ft_flight_calendar: a city code covers all of its airports.
    Without a query it lists the popular Iranian and international cities.
    """
    if query:
        data = await api(
            "/flight/search",
            origin=PLACES,
            method="POST",
            json={"searchTerm": query, "isDomestic": domestic_only, "pageNumber": 1, "pageSize": limit},
        )
        return {"places": [_place(p) for p in (data or {}).get("locations", [])[:limit]]}
    popular = await asyncio.gather(
        *(api("/Flight/PopularLocations", {"isDomestic": str(d).lower()}, origin=PLACES) for d in (True, False))
    )
    rows = [
        [
            {"code": p["iata"], "name": p.get("name"), "name_fa": p.get("nameLocal"), "country": p.get("countryName")}
            for p in (data or {}).get("locations", [])
        ]
        for data in popular
    ]
    return {"popular_domestic": rows[0], "popular_international": rows[1]}


def _flight(it: dict[str, Any], airlines: dict[str, str], airlines_fa: dict[str, str]) -> dict[str, Any]:
    """One priced itinerary, compact. Prices are Rial in the API."""
    option = it["originDestinationOptions"][0]
    segs = option["flightSegments"]
    info = it["airItineraryPricingInfo"]
    adult = next(
        (
            b["passengerFare"]["totalFare"]
            for b in info["ptcFareBreakdown"]
            if b["passengerTypeQuantity"]["passengerType"] == "Adt"
        ),
        None,
    )
    code = it.get("validatingAirlineCode") or segs[0].get("marketingAirlineCode")
    seats = [s["seatsRemaining"] for s in segs if s.get("seatsRemaining") is not None]
    out: dict[str, Any] = {
        "airline": airlines.get(code, code),
        "airline_fa": airlines_fa.get(code),
        "airline_code": code,
        "flight_no": " + ".join(s["flightNumber"] for s in segs),
        "from": segs[0]["departureAirportLocationCode"].upper(),
        "to": segs[-1]["arrivalAirportLocationCode"].upper(),
        "departure": segs[0]["departureDateTime"],
        "arrival": segs[-1]["arrivalDateTime"],
        "duration_min": option.get("journeyDurationPerMinute"),
        "stops": len(segs) - 1 + sum(s.get("stopQuantity") or 0 for s in segs),
        "seats_left": min(seats) if seats else None,
        "cabin": segs[0].get("cabinClassName"),
        "type": next(
            (n for n, k in (("charter", "isCharter"), ("system", "isSystem"), ("instance", "isInstance")) if it.get(k)),
            None,
        ),
        "price_toman": toman(adult),
        "total_toman": toman(info["itinTotalFare"]["totalFare"]),
        "baggage": " / ".join(dict.fromkeys(s["baggage"] for s in segs if s.get("baggage"))) or None,
        "pay_later": bool((it.get("payLater") or {}).get("hasPayLater")) or None,
        "cancellation_guarantee": it.get("hasCancellationGuarantee") or None,
        "fare_source_code": it["fareSourceCode"],
        "key": it.get("key"),
    }
    if len(segs) > 1:
        out["segments"] = [
            {
                "flight": s["flightNumber"],
                "from": s["departureAirportLocationCode"].upper(),
                "to": s["arrivalAirportLocationCode"].upper(),
                "departure": s["departureDateTime"],
                "arrival": s["arrivalDateTime"],
            }
            for s in segs
        ]
    return {k: v for k, v in out.items() if v is not None}


def _result(data: dict[str, Any] | None, page: int, size: int) -> dict[str, Any]:
    if not data:  # HTTP 204: no flights
        return {"flights": [], "paging": {"page": page, "page_size": size, "total": 0, "pages": 0}, "note": NO_FLIGHTS}
    rows = (data.get("additionalData") or {}).get("airlines") or []
    airlines = {a["iata"]: a.get("name") or a["iata"] for a in rows}
    airlines_fa = {a["iata"]: a["nameLocal"] for a in rows if a.get("nameLocal")}
    flt = data.get("filter") or {}
    total = flt.get("resultCount", len(data["pricedItineraries"]))
    return {
        "flights": [_flight(it, airlines, airlines_fa) for it in data["pricedItineraries"]],
        "paging": {"page": page, "page_size": size, "total": total, "pages": -(-total // size)},
    }


@tool("Search flights")
async def ft_search_flights(
    origin: Airport,
    destination: Airport,
    date: Date,
    return_date: Annotated[
        dt.date | None,
        Field(description="Return date YYYY-MM-DD for a round trip, e.g. '2026-10-26'; omit for one-way."),
    ] = None,
    adults: Annotated[int, Field(ge=1, le=9, description="Adults (12+).")] = 1,
    children: Annotated[int, Field(ge=0, le=8, description="Children (2-11).")] = 0,
    infants: Annotated[int, Field(ge=0, le=8, description="Infants (under 2).")] = 0,
    cabin: Annotated[Literal["economy", "business"], Field(description="Cabin class.")] = "economy",
    sort: Annotated[
        Literal["cheapest", "fastest", "earliest"],
        Field(description="Order: lowest price, shortest trip, earliest departure."),
    ] = "cheapest",
    airlines: Annotated[
        list[Annotated[str, Field(pattern=r"^[A-Za-z0-9]{2,3}$")]] | None,
        Field(max_length=10, description="Only these airline codes, e.g. ['W5', 'EP'] (codes appear in results)."),
    ] = None,
    direct_only: Annotated[bool, Field(description="Only flights without a stop or connection.")] = False,
    ticket_type: Annotated[
        Literal["charter", "system"] | None, Field(description="Only charter or only system (scheduled) tickets.")
    ] = None,
    depart_after: HHMM = None,
    depart_before: HHMM = None,
    max_price_toman: Annotated[int | None, Field(ge=1, description="Highest fare in Toman (whole party).")] = None,
    limit: Annotated[int, Field(ge=1, le=25, description="Flights per page.")] = 10,
    page: Annotated[int, Field(ge=1, le=20, description="Result page, 1-based. Pages above 1 need search_id.")] = 1,
    search_id: Annotated[
        int | None,
        Field(
            description="searchId from an earlier result (valid about 15 minutes): pages and re-sorts without a new search."
        ),
    ] = None,
    outbound_key: Annotated[
        str | None,
        Field(
            pattern=r"^[0-9a-f]{64}$",
            description="Round trip: `key` of the chosen outbound flight, to list the return flights for it (needs search_id).",
        ),
    ] = None,
) -> dict[str, Any]:
    """Search flights for a date: every flight with times, airline (airline_fa: Persian name), seats left, price and fare_source_code.

    Works for domestic and international routes. price_toman is one adult's fare (taxes included),
    total_toman the whole party. Times are local to each airport. Round trip (return_date): `flights`
    are outbound options whose total_toman is the cheapest round-trip total with that outbound, and
    `return_flights` are the matching return options for the cheapest outbound (`return_for` is its key;
    pass key + search_id as outbound_key + search_id to see the returns of another outbound). Filters
    apply to the outbound leg. Searches are rate limited: page through a result with search_id
    instead of searching again. Call ft_flight_details with a flight's fare_source_code for the
    refund rules and baggage; ft_flight_calendar finds the cheapest day first.
    """
    not_past(date)
    if return_date and return_date < date:
        raise ApiError("return_date is before date.")
    if outbound_key and not (search_id and return_date):
        raise ApiError("outbound_key needs search_id and return_date from the round-trip search.")
    if page > 1 and not search_id:
        raise ApiError("page above 1 needs the search_id of the first page.")
    o, d = origin.upper(), destination.upper()
    params: dict[str, Any] = {
        "sortBy": SORTS[sort],
        "pageNumber": page,
        "pageSize": limit,
        "airlineCodes": [a.upper() for a in airlines] if airlines else None,
        "stopQuantity": 0 if direct_only else None,
        "ticketTypes": TICKET_TYPES.get(ticket_type or ""),
        "maxPrice": max_price_toman * 10 if max_price_toman else None,
    }
    if depart_after or depart_before:
        params["outboundDepartureTime.from"] = f"{depart_after or '00:00'}:00"
        params["outboundDepartureTime.to"] = f"{depart_before or '23:59'}:00"
        params["outboundDepartureTime.isValid"] = "true"
    if outbound_key:  # return options of a chosen outbound: only the sort applies
        params = {"sortBy": "Fastest" if sort == "fastest" else "Cheapest", "pageNumber": page, "pageSize": limit}
        data = await api(f"/v1/Flight/SearchData/{search_id}", {**params, "index": 1, "key": outbound_key})
        return {"search_id": search_id, "return_for": outbound_key, **_result(data, page, limit)}
    if search_id:
        data = await api(f"/v1/Flight/SearchData/{search_id}", {**params, "index": 0})
    else:
        legs = [(o, d, date)] + ([(d, o, return_date)] if return_date else [])
        body = {
            "pricingSourceType": 0,
            "adultCount": adults,
            "childCount": children,
            "infantCount": infants,
            "travelPreference": {
                "cabinType": CABINS[cabin],
                "maxStopsQuantity": "All",
                "airTripType": "Return" if return_date else "OneWay",
            },
            "originDestinationInformations": [
                {
                    "originLocationCode": a,
                    "originType": "1",
                    "destinationLocationCode": b,
                    "destinationType": "1",
                    "departureDateTime": day.isoformat(),
                }
                for a, b, day in legs
            ],
            "isJalali": True,
        }
        data = await api("/v1/Flight/SplittedSearch", {**params, "index": 0}, method="POST", json=body)
    out = {"from": o, "to": d, "date": date.isoformat(), **_result(data, page, limit)}
    if data is None:
        return out
    out["search_id"] = data["searchId"]
    flt = data.get("filter") or {}
    if flt.get("price"):
        out["cheapest_toman"] = toman(flt["price"].get("minPrice"))
    if return_date and out["flights"]:
        out["return_date"] = return_date.isoformat()
        first = out["flights"][0]["key"]
        back = await api(
            f"/v1/Flight/SearchData/{data['searchId']}",
            {"index": 1, "key": first, "pageNumber": 1, "pageSize": limit, "sortBy": "Cheapest"},
        )
        if back:
            out["return_for"] = first
            out["return_flights"] = _result(back, 1, limit)["flights"]
    return out


@tool("Flight price calendar")
async def ft_flight_calendar(
    origin: Airport,
    destination: Airport,
    date: Annotated[Date, Field(description="Centre of the window, Gregorian YYYY-MM-DD, e.g. '2026-10-23'.")],
    window: Annotated[
        Literal["week", "month"], Field(description="week = the date +-3 days, month = the date +-15 days.")
    ] = "month",
) -> dict[str, Any]:
    """Cheapest one-adult fare per departure day around a date, in one cheap call (no search needed).

    Use it to pick the cheapest day, then ft_search_flights for that day. Values are cached minimums
    (price_toman, airline code) and can differ a little from a live search; days without a cached fare
    are left out and past days are dropped. `cheapest` is the lowest day.
    """
    not_past(date)
    data = await api(
        "/V1/Flight/FareLookUp",
        method="POST",
        json={
            "originLocationCode": origin.upper(),
            "destinationLocationCode": destination.upper(),
            "departureDate": date.isoformat(),
            "returnDate": None,
            "lookUpType": 2 if window == "week" else 3,
        },
    )
    days = sorted(
        (
            {"date": r["departureDate"][:10], "price_toman": toman(r["cheapestPrice"]), "airline": r.get("airline")}
            for r in (data or {}).get("result") or []
            if r.get("cheapestPrice")
        ),
        key=lambda r: r["date"],
    )
    best = min(days, key=lambda r: r["price_toman"], default=None)
    for r in days:
        if r is best:
            r["cheapest"] = True
    out: dict[str, Any] = {"days": days, "cheapest": best}
    if not days:
        out["note"] = "No cached fares around this date. Check the codes with ft_find_airport or try another date."
    return out


@tool("Flight fare details")
async def ft_flight_details(
    fare_source_code: Annotated[
        str, Field(min_length=10, max_length=300, description="fare_source_code of a flight from ft_search_flights.")
    ],
    search_id: Annotated[
        int | None,
        Field(description="search_id of that search; adds the entry and transit notes of an international fare."),
    ] = None,
) -> dict[str, Any]:
    """Refund and change rules, confirmed baggage per segment and (international) entry notes of one fare.

    The fare_source_code is valid only while its search lives (about 15 minutes); after that run
    ft_search_flights again. Charter fares list cancellation penalties as text, system fares as
    tiers (`penalties`, % of the fare by time before the flight).
    """
    jobs = [
        api("/V1/flight/farerules", {"fareSourceCode": fare_source_code}),
        api("/V1/flight/baggages", {"fareSourceCode": fare_source_code}),
    ]
    if search_id:
        jobs.append(api("/V1/Flight/AirTravelRestriction", {"fareSourceCode": fare_source_code, "searchId": search_id}))
    rules, bags, *restriction = await asyncio.gather(*jobs)
    out: dict[str, Any] = {
        "rules": [
            {
                "airline": r.get("airline"),
                "route": r.get("cityPair"),
                "details": [
                    {
                        k: v
                        for k, v in {
                            "category": d.get("category"),
                            "text": _clean(d.get("rules") or d.get("rulesLocal")),
                            "penalties": [
                                {
                                    "from": _clean(i.get("from")),
                                    "to": _clean(i.get("to")),
                                    "penalty": _clean(i.get("amount")),
                                }
                                for i in d.get("ruleItems") or []
                            ],
                        }.items()
                        if v
                    }
                    for d in r["ruleDetails"]
                ],
            }
            for r in (rules or {}).get("fareRules") or []
            if r.get("ruleDetails")
        ],
        "baggage": [
            {
                "from": b.get("departure"),
                "to": b.get("arrival"),
                "flight_no": b.get("flightNo"),
                "baggage": b.get("baggage"),
            }
            for b in (bags or {}).get("baggageInfoes") or []
        ],
    }
    if (bags or {}).get("services"):
        out["paid_services"] = bags["services"]
    notes = [
        {"group": n.get("group"), "text": _clean(n.get("englishDescription") or n.get("persianDescription"), 600)}
        for group in ((restriction[0] or {}).get("travelRestrictionItems") or [] if restriction else [])
        for n in group[-1:]
    ]
    if notes:
        out["entry_notes"] = notes
    return out
