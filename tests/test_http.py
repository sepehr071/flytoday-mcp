import re
from pathlib import Path

import httpx
import pytest

from flytoday_mcp import http

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
