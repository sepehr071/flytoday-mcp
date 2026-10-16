import asyncio
import datetime
import json
from pathlib import Path

import httpx
import pytest
from mcp import Client

from flytoday_mcp import extras, http
from flytoday_mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str):
    """A recorded response: parsed JSON for .json files, the raw text otherwise."""
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    return json.loads(raw) if name.endswith(".json") else raw


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def days(n: int) -> str:
    """A date n days from today, YYYY-MM-DD (live tests need future dates inside the sales window)."""
    return (datetime.date.today() + datetime.timedelta(days=n)).isoformat()


async def live_call(client, name: str, args: dict) -> dict:
    """Call a tool against the real site, about one request per second, and fail on tool errors."""
    await asyncio.sleep(1)
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_state(request, monkeypatch):
    """The shared httpx client is bound to one event loop; each test gets a new one."""
    if "live" not in request.keywords:  # unit tests use the fixtures' dates: pin "today" to their recording day
        for module in (http, extras):  # extras imports `today` by name
            monkeypatch.setattr(module, "today", lambda: datetime.date(2026, 10, 7))
    http.set_transport(None)
    yield
    http.set_transport(None)


@pytest.fixture
def api():
    """Route table for a fake gateway: api["<backend path>"] = JSON body or a callable(request) -> httpx.Response.

    Keys are the path after /api/gateway, e.g. "/v1/Flight/SplittedSearch" (query string excluded).
    Unknown keys return 404. Every request is appended to api.calls so tests can assert on what was sent
    (the backend is in request.headers["x-proxy-origin"]).
    """

    class Routes(dict):
        calls: list[httpx.Request]

    routes = Routes()
    routes.calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.calls.append(request)
        path = request.url.path.removeprefix("/api/gateway")
        target = routes.get(path)
        if target is None:
            return httpx.Response(404, json={"message": "no fake route", "status": 404})
        if callable(target):
            return target(request)
        return httpx.Response(200, json=target)

    http.set_transport(httpx.MockTransport(handler))
    return routes


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        call_tool = c.call_tool

        async def parsed(*args, **kwargs):
            result = await call_tool(*args, **kwargs)
            if not result.is_error:
                result.structured_content = json.loads(result.content[0].text)
            return result

        c.call_tool = parsed
        yield c
