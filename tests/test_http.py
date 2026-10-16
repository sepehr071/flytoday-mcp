import re
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
    assert len(tries) == 2


async def test_api_retries_post_connect_reset():
    tries = []

    def flaky(request):
        tries.append(request)
        if len(tries) == 1:
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, json={"ok": True})

    http.set_transport(httpx.MockTransport(flaky))
    assert await http.api("/v1/x", method="POST", json={}) == {"ok": True} and len(tries) == 2
