import asyncio
import re
import sys
from pathlib import Path

import httpx
import pytest
from conftest import fixture
from mcp import Client

from flytoday_mcp import http
from flytoday_mcp.server import mcp

pytestmark = pytest.mark.anyio

PAGE = "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(403, text="<html>Forbidden</html>"), "blocked the request (HTTP 403)"),
        (httpx.Response(429, json={}), "rate limiting searches (HTTP 429)"),
        (httpx.Response(429, headers={"x-captcha-required": "true"}), "wants a captcha"),
        (httpx.Response(502, text=PAGE), "temporarily unavailable (HTTP 502). Try again"),
        (httpx.Response(500, text=PAGE), "server error (HTTP 500). Check the ids"),
        (httpx.Response(404, json={"message": "x", "messageOriginal": "Not Found"}), "(HTTP 404): Not Found"),
        (httpx.Response(200, text=PAGE), "non-JSON response (HTTP 200)"),
    ],
)
async def test_api_error_messages(response, expected):
    http.set_transport(httpx.MockTransport(lambda r: response))
    with pytest.raises(http.ApiError) as e:
        await http.api("/v1/x")
    assert expected in str(e.value) and "<" not in str(e.value)


async def test_api_goes_through_gateway_and_drops_none_params():
    seen = []

    def ok(request):
        seen.append(request)
        return httpx.Response(204)

    http.set_transport(httpx.MockTransport(ok))
    assert await http.api("/Flight/PopularLocations", {"isDomestic": "true", "x": None}, origin=http.PLACES) is None
    r = seen[0]
    assert str(r.url) == "https://www.flytoday.ir/api/gateway/Flight/PopularLocations?isDomestic=true"
    assert r.headers["x-proxy-origin"] == http.PLACES


async def test_api_retries_one_connect_reset():
    tries = []

    def flaky(request):
        tries.append(request)
        if len(tries) == 1:
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, json={"ok": True})

    http.set_transport(httpx.MockTransport(flaky))
    assert await http.api("/v1/x") == {"ok": True} and len(tries) == 2


async def test_readme_tool_tables_match_server(client):
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    listed = re.findall(r"^\| `(ft_\w+)` \|", readme, re.M)
    assert sorted(listed) == sorted(t.name for t in (await client.list_tools()).tools)


async def test_real_client_gets_one_text_block_and_no_output_schema(api):
    api["/Rail/DomesticPopularStations"] = fixture("trains_popular.json")
    async with Client(mcp) as c:  # not the conftest `client` fixture, which re-adds structured content
        tools = (await c.list_tools()).tools
        result = await c.call_tool("ft_find_station", {})
    assert all(t.output_schema is None for t in tools)
    assert all("title" not in p for t in tools for p in t.input_schema["properties"].values())
    assert len(result.content) == 1 and result.content[0].type == "text" and result.structured_content is None
    assert " " not in result.content[0].text.split('"name_fa"')[0]  # compact separators


async def test_cached_lookup_is_fetched_once_and_post_is_retried_once(api):
    api["/v1/CIP/Airports"] = {"locations": []}
    await http.cached("/v1/CIP/Airports")
    await http.cached("/v1/CIP/Airports")
    assert len(api.calls) == 1
    tries = []

    def drop(request):
        tries.append(request)
        raise httpx.ConnectError("reset", request=request)

    http.set_transport(httpx.MockTransport(drop))
    with pytest.raises(http.ApiError):
        await http.api("/v1/x", method="POST", json={})
    assert len(tries) == 3  # retried twice


async def test_api_retries_post_connect_reset():
    tries = []

    def flaky(request):
        tries.append(request)
        if len(tries) == 1:
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, json={"ok": True})

    http.set_transport(httpx.MockTransport(flaky))
    assert await http.api("/v1/x", method="POST", json={}) == {"ok": True} and len(tries) == 2


def test_is_challenge():
    html = {"content-type": "text/html"}
    assert http._is_challenge(httpx.Response(403, text="<html>Just a moment...</html>", headers=html))
    assert http._is_challenge(httpx.Response(503, text="<!doctype html><title>ArvanCloud</title>"))
    assert not http._is_challenge(httpx.Response(403, text="<html>Forbidden</html>", headers=html))
    assert not http._is_challenge(httpx.Response(403, json={"status": 403}))
    assert not http._is_challenge(httpx.Response(200, text="<html></html>", headers=html))


async def test_frontend_headers_are_sent_and_no_user_agent_of_ours():
    seen = []
    http.set_transport(httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(204)))
    await http.api("/v1/x", origin=http.PLACES)
    h = seen[0].headers
    assert h["origin"] == h["x-origin"] == "https://www.flytoday.ir" and h["referer"] == "https://www.flytoday.ir/"
    assert h["sec-fetch-site"] == "same-origin" and h["sec-fetch-mode"] == "cors"
    assert "user-agent" not in h or "Chrome" not in h["user-agent"]


async def test_retries_5xx_and_honours_retry_after_but_not_403_or_captcha(monkeypatch):
    waits = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(http.asyncio, "sleep", fake_sleep)
    hits = []

    def limited(request):
        hits.append(1)
        if len(hits) == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "3"})
        if len(hits) == 2:
            return httpx.Response(503, json={}, headers={"Retry-After": "999"})
        return httpx.Response(200, json={"ok": True})

    http.set_transport(httpx.MockTransport(limited))
    assert await http.api("/v1/x") == {"ok": True}
    assert [w for w in waits if w] == [3, http.MAX_RETRY_WAIT]
    tries = []
    for resp in (httpx.Response(403, text="<html>no</html>"), httpx.Response(429, headers={"x-captcha-required": "1"})):
        tries.clear()
        http.set_transport(httpx.MockTransport(lambda r, resp=resp, tries=tries: tries.append(1) or resp))
        with pytest.raises(http.ApiError):
            await http.api("/v1/x")
        assert len(tries) == 1


async def test_proxy_list_rotates_and_stale_client_does_not(monkeypatch):
    monkeypatch.setenv("FLYTODAY_MCP_PROXY", "http://p1:1, http://p2:2")
    assert http._proxies() == ["http://p1:1", "http://p2:2"]
    old, _ = http._get_client()
    http._next_proxy(old)
    new, _ = http._get_client()
    assert http._proxy_index == 1 and new is not old
    http._next_proxy(old)  # a late failure on the retired client must not skip the proxy now in use
    assert http._proxy_index == 1 and http._client is new


async def test_challenge_cleared_by_browser_once(monkeypatch):
    runs = []

    async def fake_browser(proxy):
        runs.append(proxy)
        await asyncio.sleep(0.01)
        return [{"name": "clearance", "value": "ok", "domain": "www.flytoday.ir", "path": "/"}]

    monkeypatch.setattr(http, "_browser_cookies", fake_browser)

    def guarded(request):
        if "clearance=ok" not in request.headers.get("cookie", ""):
            return httpx.Response(403, text="<html>Just a moment</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, json={"ok": True})

    http.set_transport(httpx.MockTransport(guarded))
    await asyncio.gather(http.api("/v1/x", {"a": 1}), http.api("/v1/x", {"a": 2}))
    assert len(runs) == 1  # the second caller reused the cookies the first one fetched


async def test_challenge_without_patchright(monkeypatch):
    monkeypatch.setitem(sys.modules, "patchright.async_api", None)  # makes the import fail
    http.set_transport(httpx.MockTransport(lambda r: httpx.Response(403, text="<html>Just a moment</html>")))
    with pytest.raises(http.ApiError, match=r"flytoday-mcp\[browser\].*patchright install chromium"):
        await http.api("/v1/x")
