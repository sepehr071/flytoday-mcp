"""Bus tools: city lookup and search."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import PLACES, api, not_past, toman
from .registry import tool

CityId = Annotated[
    str,
    Field(pattern=r"^\d{4}$", description="4-digit bus city id from ft_find_bus_city, e.g. '0001' (Tehran)."),
]
# Facility tags as the site writes them.
FACILITIES = {"vip": "VIP", "sleeper": "تخت خواب شو", "charger": "شارژر", "monitor": "مانیتور"}


@tool("Find bus city")
async def ft_find_bus_city(
    query: Annotated[
        str | None, Field(max_length=60, description="City name, Persian or latin, e.g. 'اصفهان' or 'shiraz'.")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=30, description="Max cities.")] = 10,
) -> dict[str, Any]:
    """Bus city ids (4 digits) for a city name, or the popular cities when query is empty.

    Use before ft_search_buses (Tehran '0001', Isfahan '0002', Shiraz '0004', Mashhad '0006').
    """
    if query and query.strip():
        body = {"searchTerm": query.strip(), "pageSize": limit, "pageNumber": 0}
        data = await api("/Bus/Search", origin=PLACES, method="POST", json=body)
    else:
        data = await api("/Bus/BusPopularCities", {"count": limit}, origin=PLACES)
    cities = []
    for c in ((data or {}).get("locations") or [])[:limit]:
        row = {"city_id": c.get("cityId") or c.get("id"), "name_fa": " ".join((c.get("nameFa") or "").split())}
        row["name"] = c.get("name")
        if c.get("isDomestic") is False:
            row["foreign"] = True
        cities.append(row)
    out: dict[str, Any] = {"cities": cities}
    if not cities:
        out["note"] = "No city matches; try a shorter or Persian name."
    return out


@tool("Bus price calendar")
async def ft_bus_calendar(origin: CityId, destination: CityId) -> dict[str, Any]:
    """Cheapest bus seat price per day for the next weeks (the site's price strip), in one cheap call.

    Values are cached minimums per day (one row per company in the API) and can differ a little from a
    live search; days without a listed bus are left out. Use it to pick a day, then ft_search_buses.
    """
    data = await api("/V1/Bus/CalendarLookup", {"originId": origin, "destinationId": destination})
    by_day: dict[str, int] = {}
    for r in (data or {}).get("data") or []:
        day, price = str(r.get("departureDate"))[:10], r.get("cheapestPrice")
        if price:
            by_day[day] = min(price, by_day.get(day, price))
    days = [{"date": d, "price_toman": toman(p)} for d, p in sorted(by_day.items())]
    out: dict[str, Any] = {"days": days, "cheapest": min(days, key=lambda d: d["price_toman"], default=None)}
    if not days:
        out["note"] = "No cached fares for this route; try ft_search_buses for a date."
    return out


@tool("Search buses")
async def ft_search_buses(
    origin: CityId,
    destination: CityId,
    date: Annotated[dt.date, Field(description="Departure date YYYY-MM-DD, e.g. '2026-10-14'.")],
    sort: Annotated[Literal["cheapest", "earliest"], Field(description="Order of the results.")] = "earliest",
    facility: Annotated[
        Literal["vip", "sleeper", "charger", "monitor"] | None,
        Field(description="Keep only buses with this facility, e.g. 'vip'."),
    ] = None,
    available_only: Annotated[bool, Field(description="Hide sold-out buses.")] = True,
    limit: Annotated[int, Field(ge=1, le=50, description="Max buses.")] = 15,
) -> dict[str, Any]:
    """Buses between two cities on a date: company, terminals, departure, bus type, seats left, price per seat.

    A busy route lists over 100 buses (the raw reply is 1.5 MB): narrow with facility, sort or limit.
    price_toman is per seat. Rows carry `fare_source_code`; `search_id` is at the top. The site's seat map
    is not offered (it belongs to the booking flow). Searches are rate limited: do not repeat them in a loop.
    """
    not_past(date)
    body = {
        "originDestinations": [{"originId": origin, "destinationId": destination, "departureDate": date.isoformat()}]
    }
    data = await api("/V1/Bus/Search", method="POST", json=body) or {}
    day = (data.get("originDestinationItineraries") or [{}])[0]
    rows = day.get("itineraries") or []
    buses = []
    for i in rows:
        seats = i.get("remainingSeat") or 0
        if available_only and (i.get("isFull") or seats <= 0):
            continue
        if facility and FACILITIES[facility] not in (i.get("facilities") or []):
            continue
        dest, final = i.get("destination") or {}, i.get("finalDestination") or {}
        row = {
            "company": i.get("busCompanyNameFa"),
            "bus_type": i.get("busType"),
            "departure": str(i.get("departureDate"))[:16].replace("T", " "),
            "from_terminal": " ".join(((i.get("origin") or {}).get("nameFa") or "").split()),
            "to_terminal": " ".join((dest.get("nameFa") or "").split()),
            "continues_to": final.get("cityNameFa") if final.get("cityId") != dest.get("cityId") else None,
            "seats_left": seats,
            "price_toman": toman(i.get("price")),
            "full_price_toman": toman(i.get("fullPrice")) if i.get("hasDiscount") else None,
            "facilities": i.get("facilities") or None,
            "note": i.get("description") or None,
            "fare_source_code": i.get("fareSourceCode"),
        }
        buses.append({k: v for k, v in row.items() if v not in (None, "")})
    if sort == "cheapest":
        buses.sort(key=lambda b: (b.get("price_toman") or 0, b["departure"]))
    else:
        buses.sort(key=lambda b: b["departure"])
    out: dict[str, Any] = {
        "date": date.isoformat(),
        "search_id": data.get("searchId"),
        "buses_listed": len(rows),
        "matching": len(buses),
        "buses": buses[:limit],
    }
    if day.get("noResult") or not rows:
        out["note"] = "No buses listed for this route and date."
    elif not buses:
        out["note"] = "No bus matches the filters; try another facility or available_only=false."
    return out
