"""Shared async HTTP client for the Flytoday APIs.

Every call goes through the site's same-origin gateway, `https://www.flytoday.ir/api/gateway/<path>`, with the
real backend named in the `x-proxy-origin` header (the backends answer 403 to direct calls from most networks).
`api` caps concurrency and turns HTTP failures into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json as json_mod
import os
import random
import re
import time
from typing import Annotated, Any

import httpx
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

SITE = "https://www.flytoday.ir"
GATEWAY = f"{SITE}/api/gateway"
API = "https://api.flytoday.ir/api"  # flights, hotels, stays, trains, buses (default backend)
PLACES = "https://placesearch.flytoday.ir/api"  # place / airport / station autocomplete
STATIC = "https://staticapi.flytoday.ir/api"  # static and SEO data

HEADERS = {
    # User-Agent, sec-ch-ua and TLS come from the impersonated Chrome profile (see _get_client).
    # The site's app calls the gateway with same-origin fetch from its own pages.
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "Origin": SITE,
    "Referer": f"{SITE}/",
    "x-app": "www.flytoday.ir",
    "x-origin": SITE,
    "x-currency": "IRR",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "priority": "u=1, i",
}

# Every backend sits behind one gateway: keep the total load low.
MAX_CONCURRENCY = 2
JITTER = (0.15, 0.6)  # seconds before each uncached call
MAX_RETRIES = 2
MAX_RETRY_WAIT = 10  # cap for Retry-After and backoff, seconds
RETRY_DELAY = 0.5

FullText = Annotated[bool, Field(description="Skip text clips and item caps; return full texts.")]

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_browser_lock: asyncio.Lock | None = None
_cleared: tuple[httpx.AsyncClient, float] | None = None
_cache: dict[tuple[str, str], tuple[float, Any]] = {}


class _XhrTransport(AsyncCurlTransport):
    """The Chrome profile sends navigation-only headers; a None value makes curl drop them (API calls are XHR)."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        p = super()._create_request_params(req)
        p["headers"] = [*p["headers"], (b"upgrade-insecure-requests", None), (b"sec-fetch-user", None)]
        return p


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, None for network errors; `final` forbids a retry."""

    def __init__(
        self, message: str, status: int | None = None, retry_after: float | None = None, final: bool = False
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.final = final


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit, _proxy_index, _browser_lock, _cleared
    _transport, _client, _limit, _proxy_index, _browser_lock, _cleared = transport, None, None, 0, None, None
    _cache.clear()


def _proxies() -> list[str]:
    """FLYTODAY_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("FLYTODAY_MCP_PROXY", "").split(",") if p.strip()]


# A bare HTML 403 is an ordinary gateway refusal; only a page with a bot-check marker is worth a browser.
_CHALLENGE = re.compile(r"just a moment|captcha|challenge|arvan|attention required|enable javascript", re.I)


def _is_challenge(r: httpx.Response) -> bool:
    """A bot-check page: 403/429/503 with an HTML body that carries a known challenge marker."""
    return (
        r.status_code in (403, 429, 503)
        and "html" in r.headers.get("content-type", "") + r.text[:20].lower()
        and bool(_CHALLENGE.search(r.text[:5000]))
    )


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client.

    Only the client that failed may rotate: a slower request that failed on an old client must not skip a good proxy.
    The old client is closed late because other requests may still be using it.
    """
    global _client, _limit, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _proxy_index = None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(70)  # longer than the longest request timeout (60 s default)
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Ignore system proxy settings; route through a proxy only when the user sets FLYTODAY_MCP_PROXY.
        # curl_cffi presents Chrome's TLS/HTTP2 fingerprint and default headers; cookies and redirects stay with
        # httpx. The proxy goes to the transport (a client-level proxy is ignored once a transport is given).
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _XhrTransport(
                impersonate="chrome",
                default_headers=True,
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=httpx.Timeout(60, connect=10),
            limits=httpx.Limits(max_connections=2 * MAX_CONCURRENCY),
            follow_redirects=True,
            trust_env=False,
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
    """Call `<origin><path>` through the gateway and return the parsed JSON body (None for HTTP 204).

    A timeout, network error, 5xx or 429 is retried up to MAX_RETRIES times with jittered exponential backoff, or
    after Retry-After when sent (every POST here is a read-only query). Other 4xx (403 too), HTTP 599 (trains.py
    retries that itself) and the captcha 429 are never retried.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await _call(path, params, origin, method, json, headers, timeout)
        except ApiError as e:
            if attempt == MAX_RETRIES or e.final or e.status == 599:
                raise
            if e.status is not None and e.status < 500 and e.status != 429:
                raise
            wait = e.retry_after if e.retry_after is not None else RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT))


async def _send(client, limit, method, path, kwargs) -> httpx.Response:
    try:
        async with limit:
            await asyncio.sleep(random.uniform(*JITTER))
            return await client.request(method, f"{GATEWAY}{path}", **kwargs)
    except httpx.TimeoutException as e:
        raise ApiError("Flytoday did not answer in time. Try again in a moment.") from e
    except httpx.RequestError as e:
        if isinstance(e, httpx.ConnectError | httpx.ProxyError):
            _next_proxy(client)
        raise ApiError(
            f"Could not reach www.flytoday.ir ({type(e).__name__}). Check the internet connection, "
            "or set FLYTODAY_MCP_PROXY to an HTTP proxy."
        ) from e


async def _call(path, params, origin, method, json, headers, timeout) -> Any:
    client, limit = _get_client()
    kwargs: dict[str, Any] = {
        "params": {k: v for k, v in (params or {}).items() if v is not None},
        "json": json,
        "headers": {"x-proxy-origin": origin, **(headers or {})},
    }
    if timeout:
        kwargs["timeout"] = timeout
    r = await _send(client, limit, method, path, kwargs)
    if _is_challenge(r):
        _next_proxy(client)
        client, limit = _get_client()
        await _clear_challenge(client)
        r = await _send(client, limit, method, path, kwargs)  # one retry; a second block is final
        if _is_challenge(r):
            _next_proxy(client)
    if r.status_code in (403, 429):
        _next_proxy(client)

    if r.status_code >= 400:
        raise ApiError(
            _status_message(r),
            r.status_code,
            _retry_after(r),
            final=r.status_code == 429 and bool(r.headers.get("x-captcha-required")),
        )
    if r.status_code == 204 or not r.content:
        return None
    try:
        return r.json()
    except ValueError as e:
        raise ApiError(f"Flytoday returned a non-JSON response (HTTP {r.status_code}).") from e


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After on a 429/503, else None (HTTP-date form is ignored)."""
    v = r.headers.get("retry-after", "")
    return float(v) if r.status_code in (429, 503) and v.replace(".", "", 1).isdigit() else None


async def _browser_cookies(proxy: str | None) -> list[dict[str, Any]]:
    """Open the home page in headless patchright, wait for the challenge to clear, return the cookies."""
    try:
        from patchright.async_api import async_playwright
    except ImportError as e:
        raise ApiError(
            "Flytoday served a bot-check page. Install the browser extra (`pip install flytoday-mcp[browser]`, or "
            "`uvx --with patchright==1.63.0 flytoday-mcp`) and run `patchright install chromium`.",
            403,
            final=True,
        ) from e
    u = httpx.URL(proxy) if proxy else None
    opts = u and {"server": f"{u.scheme}://{u.host}:{u.port}", "username": u.username, "password": u.password}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=opts)
        try:
            page = await browser.new_page()
            await page.goto(SITE, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_function(
                "!/just a moment|access denied|attention required|arvancloud/i.test(document.title)", timeout=30_000
            )
            return await browser.contexts[0].cookies()
        finally:
            await browser.close()


async def _clear_challenge(client: httpx.AsyncClient) -> None:
    """Rung 2: let a real browser pass the bot check once and share its cookies with the httpx jar."""
    global _browser_lock, _cleared
    _browser_lock = _browser_lock or asyncio.Lock()
    async with _browser_lock:  # one browser at a time; callers that waited reuse the cookies just fetched
        if _cleared and _cleared[0] is client and time.monotonic() - _cleared[1] < 30:
            return
        proxies = _proxies()
        try:
            cookies = await _browser_cookies(proxies[_proxy_index % len(proxies)] if proxies else None)
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(
                f"Flytoday bot check was not cleared by the browser ({type(e).__name__}).", 403, final=True
            ) from e
        for c in cookies:
            client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
        _cleared = (client, time.monotonic())


async def cached(path: str, params: dict[str, Any] | None = None, *, ttl: float = 6 * 3600, **kwargs: Any) -> Any:
    """`api` for near-static lookup lists, memoized for `ttl` seconds per (path, params, kwargs). Callers only read."""
    key = (path, json_mod.dumps([params, kwargs], sort_keys=True, default=str))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    data = await api(path, params, **kwargs)
    _cache[key] = (time.monotonic(), data)
    return data


def clip(text: Any, n: int | None) -> str:
    """Third-party free text cut to n characters, with an ellipsis marker when something was dropped."""
    s = str(text or "")
    return s if n is None or len(s) <= n else s[: n - 1] + "…"


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
