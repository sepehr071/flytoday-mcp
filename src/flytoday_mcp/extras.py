"""Extras tools: CIP airport lounges, eSIM data plans, visas, activities and tours."""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .http import PLACES, FullText, api, cached, not_past, today, toman
from .registry import tool

# Visa prices come with a Persian currency word; ریال is Rial, so those are converted to Toman.
CURRENCY_CODES = {"دلار": "USD", "یورو": "EUR", "درهم": "AED", "ریال عمان": "OMR", "ریال": "IRR"}
PASSENGER = {"Adt": "adult", "Chd": "child", "Inf": "infant"}
MAX_RANGE_DAYS = 90


def _norm(text: Any) -> str:
    """Lowercase, Persian letter forms, no spaces, hyphens or zero-width non-joiners (so 'Costa Rica' ~ 'costa-rica')."""
    s = str(text or "").lower().translate(str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه"}))
    return re.sub(r"[\s\-_\u200c.()]+", "", s)


def _first_fa(items: Any) -> str:
    """First Persian text of a [{lang, name}] list."""
    for item in items or []:
        if item.get("lang") == "Fa" and item.get("name"):
            return item["name"]
    return (items or [{}])[0].get("name") or ""


def _find_country(query: str, rows: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, Any] | None:
    """Exact normalized match on any key first, then a substring match when exactly one row fits."""
    q = _norm(query)
    for row in rows:
        if q in {_norm(row.get(k)) for k in keys}:
            return row
    partial = [row for row in rows if any(q in _norm(row.get(k)) for k in keys)]
    return partial[0] if len(partial) == 1 else None


# ---------------------------------------------------------------- CIP


@tool("CIP airport services")
async def ft_cip(
    airport: Annotated[
        str,
        Field(
            pattern=r"^[A-Za-z]{3}$",
            description="Airport IATA code that sells CIP lounges: 'IKA' (Tehran Imam Khomeini, international) or 'MHD' (Mashhad, domestic and international).",
        ),
    ],
    date: Annotated[
        dt.date,
        Field(
            description="Flight date YYYY-MM-DD, today or later and at most about 3 months ahead, e.g. '2026-10-20'."
        ),
    ],
    direction: Annotated[
        Literal["departure", "arrival"],
        Field(description="'departure' = leaving from this airport, 'arrival' = landing here."),
    ] = "departure",
    flight_type: Annotated[
        Literal["domestic", "international"], Field(description="Kind of flight.")
    ] = "international",
    adults: Annotated[int, Field(ge=1, le=9, description="Adults, e.g. 1.")] = 1,
    children: Annotated[int, Field(ge=0, le=9, description="Children, e.g. 0.")] = 0,
    infants: Annotated[int, Field(ge=0, le=9, description="Infants, e.g. 0.")] = 0,
    full_text: FullText = False,
) -> dict[str, Any]:
    """CIP / VIP airport lounge service with prices for a date and passenger mix, plus paid add-ons.

    Only IKA and MHD sell CIP. `nationality` says which passports an offer is for (IKA lists one offer for
    Iranian passports and one for all others). price_per_passenger_toman is per person (tax included); total_toman is for the
    whole party; add-ons (wheelchair, pet, transfers) list their adult price. Nothing is held or booked.
    Next: the user books on flytoday.ir.
    """
    not_past(date)
    code = airport.lower()
    places = (await cached("/v1/CIP/Airports") or {}).get("locations") or []
    known = {p["iata"]: p for p in places}
    if code not in known:
        raise ToolError(
            f"{airport.upper()} has no CIP service. Airports with CIP: {', '.join(sorted(k.upper() for k in known))}."
        )
    kind = flight_type.capitalize()
    way = "Outbound" if direction == "departure" else "Inbound"
    if kind not in known[code]["flightTypes"] or way not in known[code]["flightDirectionTypes"]:
        raise ToolError(
            f"{code.upper()} sells CIP only for {'/'.join(t.lower() for t in known[code]['flightTypes'])} flights."
        )
    data = await api(
        "/v1/CIP/Search",
        method="POST",
        json={
            "lang": 0,
            "date": date.isoformat(),
            "airportCode": code,
            "flightDirectionType": 1 if direction == "departure" else 0,
            "flightType": 1 if flight_type == "domestic" else 2,
            "adult": adults,
            "child": children,
            "infant": infants,
        },
    )
    clip, cap = (None, None) if full_text else (300, 8)
    offers = []
    for it in (data or {}).get("pricedItineraries") or []:
        info = it.get("cipInfo") or {}
        per = [
            {
                "passenger": PASSENGER.get(p["passengerCode"], p["passengerCode"]),
                "quantity": p["passengerQuantity"],
                "price_per_passenger_toman": toman(p["totalFare"]),
                "tax_toman": toman(p.get("taxes")),
            }
            for p in it.get("ptcFareBreakdowns") or []
        ]
        offer = {
            "title": _first_fa(info.get("titles")),
            "nationality": _nationality(info),
            "passengers": per,
            "total_toman": sum(p["quantity"] * p["price_per_passenger_toman"] for p in per),
            "add_ons": [
                {
                    "type": s.get("serviceType"),
                    "title": s.get("title"),
                    "adult_price_toman": toman(s["ptcFareBreakdowns"][0]["totalFare"])
                    if s.get("ptcFareBreakdowns")
                    else None,
                    "per": s.get("behavior"),
                }
                for s in it.get("services") or []
            ],
            "includes": _first_fa(info.get("desciptions")),
            "cancellation_rules": [_text(x, clip) for x in (info.get("rules") or {}).get("adult") or []][:cap],
            "notes": [_text(x, clip) for x in (info.get("rules") or {}).get("extraRule") or []][:cap],
        }
        offers.append({k: v for k, v in offer.items() if v is not None})
    return {
        "airport": code.upper(),
        "airport_name": known[code]["name"],
        "date": date.isoformat(),
        "direction": direction,
        "flight_type": flight_type,
        "offers": offers,
    }


def _nationality(info: dict[str, Any]) -> str | None:
    """IKA sells the same lounge twice: one offer for Iranian passports only, one for every other passport."""
    if info.get("priceInclude"):
        return "only " + ", ".join(info["priceInclude"])
    if info.get("priceExclude"):
        return "all except " + ", ".join(info["priceExclude"])
    return None


# ---------------------------------------------------------------- eSIM


@tool("eSIM plans")
async def ft_esim_plans(
    country: Annotated[
        str,
        Field(
            min_length=2,
            max_length=60,
            description="Country in English or Persian, or ISO code or slug, e.g. 'turkey', 'ترکیه', 'AE'.",
        ),
    ],
    limit: Annotated[int, Field(ge=1, le=50, description="Max plans, cheapest first.")] = 15,
) -> dict[str, Any]:
    """eSIM data plans for a country with data amount, validity days and price, cheapest first.

    Covers 200+ countries (no regional or global bundles). fare_code identifies the plan on flytoday.ir; plans
    cannot be bought here. coverage_countries is how many countries the plan works in.
    """
    geo = (await cached("/V1/ESim/GeoLocations") or {}).get("geoLocations") or []
    hit = _find_country(country, geo, ("slug", "code", "name"))
    if not hit:
        raise ToolError(f"No eSIM country matches '{country}'. Use the English name ('turkey') or the Persian name.")
    data = await api("/V1/ESim/Search", {"packageType": "COUNTRY", "slug": hit["slug"], "limit": 1000, "page": 1})
    plans = sorted((data or {}).get("packages") or [], key=lambda p: p["price"])
    return {
        "country": hit["name"],
        "slug": hit["slug"],
        "plans_total": len(plans),
        "plans": [
            {
                "fare_code": p["fareCode"],
                "title": p["title"],
                "data_gb": None if p.get("isUnlimited") else round(p["dataAmount"] / 1024, 2),
                "unlimited": bool(p.get("isUnlimited")),
                "days": p["validityInDay"],
                "price_toman": toman(p["price"]),
                "operator": (p.get("operator") or {}).get("title"),
                "coverage_countries": p.get("coverageCount"),
                **({"voice_minutes": p["voiceCount"]} if p.get("voiceCount") else {}),
                **({"sms": p["textCount"]} if p.get("textCount") else {}),
            }
            for p in plans[:limit]
        ],
    }


# ---------------------------------------------------------------- visa


def _text(html: Any, cap: int | None = 600) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html or ""))).strip()[:cap]


def _visa_content(raw: Any) -> str:
    """VisaDetail.content is an object with HTML in introductionText."""
    return _text(raw.get("introductionText") if isinstance(raw, dict) else raw)


@tool("Visa prices and requirements")
async def ft_visa(
    country: Annotated[
        str | None,
        Field(
            max_length=60,
            description="Country in English, Persian or slug, e.g. 'turkey', 'پاکستان', 'schengen'. Omit to list every country.",
        ),
    ] = None,
) -> dict[str, Any]:
    """Visa services of Flytoday: the country list with from-prices, or one country's prices, documents and process.

    Without country: every country with visa type, from_price (in price_currency, often USD or EUR, not Toman)
    and processing time. With country: price per visa type (foreign currency and Toman at Flytoday's rate),
    validity, processing time and documents. Schengen is one entry, 'schengen'.
    """
    listing = await cached("/v2/Visa/Search", method="POST", json={}) or {}
    items = listing.get("items") or []
    if country is None:
        return {
            "total": len(items),
            "countries": [
                {
                    "country": i["country"]["name"],
                    "name_fa": i["country"]["nameFa"],
                    "slug": i["country"]["slug"],
                    "visa_type": (i.get("category") or {}).get("nameFa"),
                    "from_price": _price(i),
                    "price_currency": CURRENCY_CODES.get(i.get("currency"), i.get("currency")),
                    **(
                        {"price_toman": toman(float(i["price"].replace(",", "")))}
                        if i.get("currency") == "ریال" and _price(i)
                        else {}
                    ),
                    "processing_time": i.get("duration"),
                    **({"schengen": True} if i["country"].get("hasSchengen") else {}),
                }
                for i in items
            ],
        }
    flat = [{**i["country"], "_": i} for i in items]
    hit = _find_country(country, flat, ("slug", "name", "nameFa", "code"))
    if not hit:
        raise ToolError(
            f"No visa service for '{country}'. Call ft_visa without a country to list {len(items)} countries."
        )
    d = await api("/v3/Visa/VisaDetail", {"countryName": hit["slug"]}, method="POST", json={}) or {}
    cur = CURRENCY_CODES.get(d.get("currency"), d.get("currency"))
    types = []
    for table in d.get("tables") or []:
        for row in table.get("rows") or []:
            for p in row.get("prices") or []:
                pd = p.get("priceDetail") or {}
                types.append(
                    {
                        "visa_type": (row.get("type") or {}).get("nameFa"),
                        "duration": (table.get("headers") or ["", ""])[-1]
                        if len(table.get("headers") or []) > 1
                        else None,
                        "item_id": p.get("itemId"),
                        "price_currency": cur,
                        "adult_price": pd.get("adultPrice"),
                        "child_price": pd.get("childPrice"),
                        "infant_price": pd.get("infantPrice"),
                        "adult_price_toman": toman(pd.get("adultPriceIRR")),
                        "child_price_toman": toman(pd.get("childPriceIRR")),
                        "infant_price_toman": toman(pd.get("infantPriceIRR")),
                        "exchange_rate_toman": toman(pd.get("exchangeRate")),
                        "condition": p.get("condition"),
                    }
                )
    return {
        "country": hit["name"],
        "name_fa": hit["nameFa"],
        "slug": hit["slug"],
        "validity_after_issuance": d.get("validityAfterIssuance"),
        "processing_time": d.get("process"),
        "visa_types": types,
        "documents": [
            {
                "category": doc.get("categoryFa"),
                "items": [i.get("description") or i.get("visaDocumentTypeFa") for i in doc.get("items") or []],
            }
            for doc in d.get("documents") or []
        ],
        "intro": _visa_content(d.get("content")),
    }


def _price(item: dict[str, Any]) -> str | None:
    """Listing prices are strings; '_' means no public price."""
    p = item.get("price")
    return None if p in (None, "", "_") else p


# ---------------------------------------------------------------- activities


def _span(
    date_from: dt.date | None, date_to: dt.date | None, default_from: int, default_days: int
) -> tuple[dt.date, dt.date]:
    start = date_from or today() + dt.timedelta(days=default_from)
    not_past(start, "date_from")
    start = max(start, today() + dt.timedelta(days=1))  # the API answers an empty list when the range starts today
    end = date_to or start + dt.timedelta(days=default_days)
    if end < start or (end - start).days > MAX_RANGE_DAYS:
        raise ToolError(f"date_to must be on or after date_from and at most {MAX_RANGE_DAYS} days later.")
    return start, end


def _adult(rows: Any) -> dict[str, Any]:
    return next((r for r in rows or [] if r.get("passengerType") == "Adult"), (rows or [{}])[0])


def _region_id(loc: dict[str, Any]) -> int:
    """Regions carry their id in sequenceId (regionId is 0); segments and activities carry the parent's regionId."""
    return loc.get("regionId") or (loc.get("sequenceId") if loc.get("activityType") == "ActivityRegion" else 0) or 0


def _region(loc: dict[str, Any]) -> dict[str, Any]:
    name = loc["name"] if loc.get("activityType") == "ActivityRegion" else loc.get("cityName")
    return {"region_id": _region_id(loc), "name": name, "country": loc.get("countryName")}


@tool("Search activities")
async def ft_activities(
    query: Annotated[
        str | None,
        Field(
            max_length=60,
            description="Destination, English or Persian, e.g. 'Dubai', 'دبی', 'Istanbul'. Needed unless region_id is given.",
        ),
    ] = None,
    region_id: Annotated[
        int | None, Field(ge=1, description="Region id from an earlier call, e.g. 1234 (Dubai); skips the lookup.")
    ] = None,
    date_from: Annotated[
        dt.date | None,
        Field(description="First day YYYY-MM-DD; tomorrow or later (today is moved to tomorrow); default tomorrow."),
    ] = None,
    date_to: Annotated[
        dt.date | None,
        Field(description="Last day YYYY-MM-DD, up to 90 days after date_from; default date_from + 30 days."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=50, description="Max activities, cheapest first.")] = 20,
) -> dict[str, Any]:
    """Tours, tickets and experiences in a destination that are on sale in the date range, with from-prices.

    Only activities with a price in the range are listed (many are unavailable on given dates), cheapest
    first. price_toman is the adult from-price; list_price_toman is the box-office price. region is the
    destination matched; other_regions lists alternatives to retry with region_id. Next: ft_activity(activity_id).
    """
    if query is None and region_id is None:
        raise ToolError("Give a destination as query (e.g. 'Dubai') or a region_id.")
    start, end = _span(date_from, date_to, 1, 30)
    region: dict[str, Any] = {"region_id": region_id}
    others: list[dict[str, Any]] = []
    if region_id is None:
        found = await api(
            "/Activity/Search",
            origin=PLACES,
            method="POST",
            json={"searchTerm": query, "isDomestic": False, "pageNumber": 1, "pageSize": 10},
        )
        locs = [loc for loc in (found or {}).get("locations") or [] if _region_id(loc)]
        if not locs:
            raise ToolError(f"No activity destination matches '{query}'. Try the English city name, e.g. 'Dubai'.")
        best = next((loc for loc in locs if loc.get("activityType") == "ActivityRegion"), locs[0])
        region = _region(best)
        others = [_region(loc) for loc in locs if _region_id(loc) != region["region_id"]]
        others = [o for n, o in enumerate(others) if o not in others[:n]]
    data = await api(
        "/v1/Activity/search",
        method="POST",
        json={"locationId": region["region_id"], "dateFrom": start.isoformat(), "dateTo": end.isoformat()},
        timeout=120,  # a big region can take over a minute
    )
    rows = []
    for o in (data or {}).get("outlines") or []:
        if not o.get("amountFrom"):
            continue
        a = _adult(o["amountFrom"])
        dur = o.get("duration") or {}
        rows.append(
            {
                "activity_id": o["activityId"],
                "name": (o.get("name") or {}).get("En") or (o.get("name") or {}).get("Fa"),
                "duration": f"{dur['value']} {dur['type'].lower()}" if dur.get("value") else None,
                "price_toman": toman(a["amount"]),
                "list_price_toman": toman(a.get("boxOfficeAmount")),
                "discount_percent": a.get("boxOfficeDiscountPercentage") or None,
                "free_cancellation": bool(o.get("freeCancellation")),
            }
        )
    rows.sort(key=lambda r: r["price_toman"])
    return {
        "region": region,
        "other_regions": others[:4],
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "priced_total": len(rows),
        "activities": rows[:limit],
    }


@tool("Activity details")
async def ft_activity(
    activity_id: Annotated[int, Field(ge=1, description="Activity id from ft_activities, e.g. 13049.")],
    date_from: Annotated[
        dt.date | None, Field(description="Price calendar start YYYY-MM-DD; default today + 3 days.")
    ] = None,
    date_to: Annotated[
        dt.date | None,
        Field(description="Price calendar end YYYY-MM-DD, up to 90 days after date_from; default date_from + 57 days."),
    ] = None,
) -> dict[str, Any]:
    """One activity: description, highlights, options with adult prices, and the cheapest days in the range.

    price_toman fields are adult prices. The range matters: an activity with no availability in it comes back
    with available=false, so use a wide range (the default is about two months). Slow upstream (up to a minute).
    Booking happens on flytoday.ir.
    """
    start, end = _span(date_from, date_to, 3, 57)
    details, content = await asyncio.gather(
        api(
            "/v1/Activity/details",
            method="POST",
            json={"activityId": activity_id, "dateFrom": start.isoformat(), "dateTo": end.isoformat()},
            timeout=120,
        ),
        api("/v1/Activity/Content", {"activityId": activity_id}),
    )
    d, c = details or {}, (content or {}).get("content") or {}
    if not d and not c:
        raise ToolError(f"No activity with id {activity_id}.")
    base = d.get("amountsFrom") or {}
    options = []
    for it in d.get("itineraries") or []:
        rates = [(_adult(r.get("ptcBreakDowns")), r) for r in it.get("activityRates") or []]
        rates = [(a, r) for a, r in rates if a.get("amount")]
        if not rates:
            continue
        a, r = min(rates, key=lambda x: x[0]["amount"])
        options.append(
            {
                "name": (it.get("name") or {}).get("En"),
                "languages": [v.get("languageName") for v in (it.get("languages") or {}).values()],
                "price_toman": toman(a["amount"]),
                "free_cancellation": bool(r.get("freeCancellation")),
            }
        )
    days = sorted((k, v) for k, v in (d.get("activityAmountsFromCalendar") or {}).items() if not v.get("notAvailable"))
    return {
        "activity_id": activity_id,
        "name": (c.get("name") or {}).get("En"),
        "name_fa": (c.get("name") or {}).get("Fa"),
        "region": (c.get("region") or {}).get("name"),
        "available": bool(base) and not base.get("notAvailable"),
        "price_toman": toman(base.get("amount")),
        "list_price_toman": toman(base.get("boxOfficeAmount")),
        "discount_percent": base.get("boxOfficeDiscountPercentage") or None,
        "rating": d.get("rating"),
        "reviews": d.get("commentsCount"),
        "summary": " ".join(c.get("summary") or [])[:700],
        "highlights": (c.get("highlights") or [])[:6],
        "features": [f["description"] for f in c.get("features") or []][:10],
        "options": options[:8],
        "available_days": len(days),
        "cheapest_days": [
            {"date": k, "price_toman": toman(v["amount"])} for k, v in sorted(days, key=lambda kv: kv[1]["amount"])[:5]
        ],
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
    }
