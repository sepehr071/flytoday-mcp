"""Shared async HTTP client for the Flytoday APIs.

Every call goes through the site's same-origin gateway, `https://www.flytoday.ir/api/gateway/<path>`, with the
real backend named in the `x-proxy-origin` header (the backends answer 403 to direct calls from most networks).
`api` caps concurrency and turns HTTP failures into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

SITE = "https://www.flytoday.ir"
GATEWAY = f"{SITE}/api/gateway"
API = "https://api.flytoday.ir/api"  # flights, hotels, stays, trains, buses (default backend)
PLACES = "https://placesearch.flytoday.ir/api"  # place / airport / station autocomplete
STATIC = "https://staticapi.flytoday.ir/api"  # static and SEO data

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa",
    "x-app": "www.flytoday.ir",
    "x-origin": SITE,
    "x-currency": "IRR",
}

# Every backend sits behind one gateway: keep the total load low.
MAX_CONCURRENCY = 2

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, None for network errors."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit
    _transport, _client, _limit = transport, None, None


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Ignore system proxy settings; route through a proxy only when the user sets FLYTODAY_MCP_PROXY.
        _client = httpx.AsyncClient(
            transport=_transport,
            headers=HEADERS,
            timeout=60,
            follow_redirects=True,
            trust_env=False,
            # httpx mounts a proxy over any custom transport, so tests must not get one
            proxy=None if _transport else os.environ.get("FLYTODAY_MCP_PROXY") or None,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


async def api(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    origin: str = API,
    method: str = "GET",
    json: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> Any:
    """Call `<origin><path>` through the gateway and return the parsed JSON body (None for HTTP 204)."""
    client, limit = _get_client()
    kwargs: dict[str, Any] = {
        "params": {k: v for k, v in (params or {}).items() if v is not None},
        "json": json,
        "headers": {"x-proxy-origin": origin, **(headers or {})},
    }
    if timeout:
        kwargs["timeout"] = timeout
    r = None
    for attempt in range(2):  # one retry on a dropped connection
        try:
            async with limit:
                r = await client.request(method, f"{GATEWAY}{path}", **kwargs)
            break
        except httpx.TimeoutException as e:
            raise ApiError("Flytoday did not answer in time. Try again in a moment.") from e
        except httpx.RequestError as e:
            if attempt == 0 and isinstance(e, httpx.ConnectError | httpx.RemoteProtocolError):
                await asyncio.sleep(1)
                continue
            raise ApiError(
                f"Could not reach www.flytoday.ir ({type(e).__name__}). Check the internet connection, "
                "or set FLYTODAY_MCP_PROXY to an HTTP proxy."
            ) from e
    assert r is not None

    if r.status_code >= 400:
        raise ApiError(_status_message(r), r.status_code)
    if r.status_code == 204 or not r.content:
        return None
    try:
        return r.json()
    except ValueError as e:
        raise ApiError(f"Flytoday returned a non-JSON response (HTTP {r.status_code}).") from e


def toman(rial: Any) -> int | None:
    """Flytoday money fields are Rial (x-currency IRR); 1 Toman = 10 Rial."""
    try:
        value = int(float(rial))
    except (TypeError, ValueError):
        return None
    return value // 10


def today() -> dt.date:
    """Today in Tehran (UTC+3:30, no DST since 2022)."""
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=3, minutes=30))).date()


def not_past(day: dt.date, name: str = "date") -> None:
    """Reject a date before today in Tehran."""
    if day < today():
        raise ToolError(f"{name} {day} is in the past (today in Tehran is {today()}).")


def _status_message(r: httpx.Response) -> str:
    code = r.status_code
    if code == 403:
        return "Flytoday blocked the request (HTTP 403). Wait a few minutes; if it persists, set FLYTODAY_MCP_PROXY."
    if code == 429:
        if r.headers.get("x-captcha-required"):  # hotel search: blocked until a browser solves Turnstile
            return (
                "Flytoday wants a captcha for this search from this network (HTTP 429). Searches stay blocked "
                "for an hour or more; try later or set FLYTODAY_MCP_PROXY to another network."
            )
        return "Flytoday is rate limiting searches (HTTP 429). Wait several minutes; do not retry in a loop."
    if code in (502, 503, 504):
        return f"Flytoday is temporarily unavailable (HTTP {code}). Try again in a few minutes."
    try:
        detail = _error_text(r.json())
    except ValueError:
        detail = "" if r.text.lstrip().startswith("<") else r.text[:200]  # an HTML error page says nothing useful
    detail = f": {detail}" if detail else ""
    if code >= 500:
        return f"Flytoday had a server error (HTTP {code}){detail}. Check the ids and dates, or try again later."
    return f"Flytoday rejected the request (HTTP {code}){detail}"


def _error_text(body: Any) -> str:
    """Gateway errors carry `messageOriginal` (English) next to a generic Persian `message`."""
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, dict) and errors:
            return "; ".join(f"{k}: {' '.join(map(str, v))}" for k, v in errors.items())[:300]
        for key in ("messageOriginal", "message", "title", "error"):
            if body.get(key):
                return str(body[key])[:300]
    return str(body)[:300]
