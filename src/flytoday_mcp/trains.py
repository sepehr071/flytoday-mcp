"""Train tools: station lookup, search, cheapest days, stops."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import API, PLACES, ApiError, api, not_past, toman
from .registry import tool

Station = Annotated[
    int,
    Field(ge=1, le=9_999, description="Train station code from ft_find_station, e.g. 1 (Tehran) or 191 (Mashhad)."),
]
RETRIES = 3  # the rail-company link answers HTTP 599 on about 1 in 4 first calls and works a moment later
RETRY_DELAY = 3.0


def _hm(value: Any) -> str | None:
    """'2026-10-14T07:25:00' or '07:25:00' -> 'HH:MM'."""
    return str(value)[-8:-3] if value else None


@tool("Find train station")
async def ft_find_station(
    query: Annotated[
        str | None,
        Field(max_length=60, description="City or station name, Persian or latin, e.g. 'مشهد' or 'isfahan'."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=30, description="Max stations.")] = 10,
) -> dict[str, Any]:
    """Train station codes (`station_code`) for a city name, or the 8 most popular stations when query is empty.

    Use before ft_search_trains and ft_train_calendar, which need the codes (Tehran 1, Mashhad 191).
    """
    if query and query.strip():
        body = {"searchTerm": query.strip(), "pageSize": limit, "pageNumber": 0}
        data = await api("/Rail/DomesticSearch", origin=PLACES, method="POST", json=body)
    else:
        data = await api("/Rail/DomesticPopularStations", origin=PLACES)
    stations = [
        {"station_code": s.get("stationCode"), "name_fa": s.get("nameFa"), "name": s.get("name")}
        for s in ((data or {}).get("stations") or [])[:limit]
    ]
    out: dict[str, Any] = {"stations": stations}
    if not stations:
        out["note"] = "No station matches; try a shorter or Persian name."
    return out


async def _search(body: dict[str, Any]) -> Any:
    for attempt in range(RETRIES + 1):
        try:
            return await api("/V2/DomesticRails/Search", origin=API, method="POST", json=body)
        except ApiError as e:
            if e.status != 599 or attempt == RETRIES:
                if e.status == 599:
                    raise ApiError(
                        "The rail company link is down (HTTP 599 after retries). Try again later.", 599
                    ) from e
                raise
            await asyncio.sleep(RETRY_DELAY)


@tool("Search trains")
async def ft_search_trains(
    origin: Station,
    destination: Station,
    date: Annotated[
        dt.date, Field(description="Travel date YYYY-MM-DD, e.g. '2026-10-14'. Sales open ~16 days ahead.")
    ],
    adults: Annotated[int, Field(ge=1, le=9, description="Adult passengers.")] = 1,
    children: Annotated[int, Field(ge=0, le=9, description="Children (2-12).")] = 0,
    infants: Annotated[int, Field(ge=0, le=9, description="Infants (under 2).")] = 0,
    sort: Annotated[Literal["cheapest", "earliest"], Field(description="Order of the results.")] = "earliest",
    available_only: Annotated[bool, Field(description="Hide classes with no free seats for this party.")] = True,
    limit: Annotated[int, Field(ge=1, le=50, description="Max train classes.")] = 15,
) -> dict[str, Any]:
    """Trains between two stations on a date: one row per train class (wagon) with price per adult and seats left.

    price_toman is per adult. Sales cover only about the next 16 days (no_result means outside it). Rows carry
    `fare_source_code` and `wagon_id`: pass them to ft_train_stops for stops and amenities. Other days: ft_train_calendar.
    Searches are rate limited by the site: do not repeat them in a loop.
    """
    not_past(date)
    body = {
        "sexCode": 3,  # any; other codes are unverified
        "adultsCount": adults,
        "childCount": children,
        "infantCount": infants,
        "isExclusive": False,
        "tripType": "OneWay",
        "originDestination": [{"origin": origin, "destination": destination, "moveDate": date.isoformat()}],
    }
    data = await _search(body) or {}
    day = (data.get("itinerariesList") or [{}])[0]
    rows = day.get("itineraries") or []
    trains = []
    for i in rows:
        seats = i.get("remainingSeats") or 0
        if available_only and not (seats > 0 and i.get("isCapacityEnough")):
            continue
        row = {
            "company": i.get("railCompanyName"),
            "train_number": i.get("trainNumber"),
            "wagon": " ".join((i.get("wagonName") or "").split()),
            "type": i.get("trainType"),
            "departure": str(i.get("moveDate"))[:16].replace("T", " "),
            "arrival": str(i.get("arrivalDate"))[:16].replace("T", " "),
            "duration": _hm(i.get("travelDuration")),
            "seats_left": seats,
            "price_toman": toman(i.get("finalPrice") or i.get("price")),
            "compartment": bool(i.get("isCompartment")),
            "food": bool(i.get("hasFood")),
            "fare_source_code": i.get("fareSourceCode"),
            "wagon_id": i.get("wagonId"),
        }
        trains.append({k: v for k, v in row.items() if v not in (None, "")})
    if sort == "cheapest":
        trains.sort(key=lambda t: t.get("price_toman") or 0)
    else:
        trains.sort(key=lambda t: t["departure"])
    out: dict[str, Any] = {
        "from": (day.get("origin") or {}).get("nameFa"),
        "to": (day.get("destination") or {}).get("nameFa"),
        "date": date.isoformat(),
        "search_id": data.get("id"),
        "classes_listed": len(rows),
        "matching": len(trains),
        "trains": trains[:limit],
    }
    if day.get("noResult"):
        out["no_result"] = True
        out["note"] = "No trains: the date is outside the sale window (about 16 days). Try ft_train_calendar."
    elif not trains:
        out["note"] = "No class with free seats for this party. Try ft_train_calendar or available_only=false."
    return out


@tool("Train price calendar")
async def ft_train_calendar(origin: Station, destination: Station) -> dict[str, Any]:
    """Cheapest train price per day for the next ~16 days, with sold-out days flagged.

    Use to pick a day; then ft_search_trains for that date.
    """
    data = await api(
        "/V1/DomesticRails/FareLookUp",
        {"fromStation": origin, "toStation": destination, "carIncluded": "false"},
        origin=API,
    )
    by_day: dict[str, list[int]] = {}
    for r in (data or {}).get("data") or []:
        day = by_day.setdefault(str(r.get("departureDate"))[:10], [])
        if not r.get("isFull") and r.get("cheapestPrice"):
            day.append(r["cheapestPrice"])
    days = [
        {"date": d, "cheapest_toman": toman(min(p)), "sold_out": False} if p else {"date": d, "sold_out": True}
        for d, p in sorted(by_day.items())
    ]
    open_days = [d for d in days if not d["sold_out"]]
    out: dict[str, Any] = {"days": days, "cheapest": min(open_days, key=lambda d: d["cheapest_toman"], default=None)}
    if not days:
        out["note"] = "No fares listed for this route."
    return out


@tool("Train stops")
async def ft_train_stops(
    fare_source_code: Annotated[
        str,
        Field(pattern=r"^[0-9A-Fa-f]{16,400}$", description="fare_source_code from a fresh ft_search_trains row."),
    ],
    wagon_id: Annotated[
        int | None, Field(ge=1, description="wagon_id from the same row; adds the class amenities and meals.")
    ] = None,
) -> dict[str, Any]:
    """Stops of one train with arrival and departure times, plus (with wagon_id) the class amenities and extra meals.

    Needs ids from a recent ft_search_trains result; they expire with the search.
    """
    data = await api(f"/V1/DomesticRails/StopStations/{fare_source_code}", origin=API) or {}
    raw = data.get("stations") or []
    stops = []
    for n, s in enumerate(raw):
        # ponytail: the source's enter/exit times are swapped for middle stops, so sort them; the first stop only
        # departs (exitTime) and the last only arrives (enterTime).
        a, b = sorted(filter(None, (_hm(s.get("enterTime")), _hm(s.get("exitTime")))))[:2] or (None, None)
        if n == 0:
            a, b = None, _hm(s.get("exitTime"))
        elif n == len(raw) - 1:
            a, b = _hm(s.get("enterTime")), None
        stop = {"station_code": s.get("stationCode"), "name_fa": s.get("stationNameFa"), "arrive": a, "depart": b}
        stops.append({k: v for k, v in stop.items() if v})
    out: dict[str, Any] = {"stops": stops}
    if wagon_id:
        w = await api(f"/V1/DomesticRails/WagonInfo/{wagon_id}", origin=API) or {}
        out["wagon"] = {
            "name": w.get("wagonName"),
            "amenities": [a.get("title") for a in w.get("wagonAmenities") or []],
            "extra_services": [
                {"title": e.get("title"), "price_toman": toman(e.get("price"))} for e in w.get("extraServices") or []
            ],
        }
    return out
