<!-- mcp-name: io.github.sepehr071/flytoday-mcp -->

<div align="center">

<img src="https://raw.githubusercontent.com/sepehr071/flytoday-mcp/main/.github/banner.png" alt="flytoday-mcp: unofficial Flytoday MCP server for Claude, Cursor and Copilot" width="100%">

# flytoday-mcp: Flytoday MCP server for AI agents

**Let your AI agent plan trips on Flytoday (فلای&zwnj;تودی), in Iran and abroad.**<br>
An unofficial, read-only MCP server for [Flytoday](https://www.flytoday.ir), the Iranian online travel agency:<br>
cheapest domestic and international flights, trains and buses, fare rules and baggage, hotels and villas with exact prices, plus CIP lounges, eSIMs and visas, from Claude, Cursor or Copilot.

[![PyPI](https://img.shields.io/pypi/v/flytoday-mcp?color=2563eb)](https://pypi.org/project/flytoday-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/flytoday-mcp)](https://pypi.org/project/flytoday-mcp/)
[![CI](https://github.com/sepehr071/flytoday-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/sepehr071/flytoday-mcp/actions/workflows/ci.yml)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.sepehr071%2Fflytoday--mcp-7c3aed)](https://registry.modelcontextprotocol.io/?q=flytoday-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/flytoday-mcp/blob/main/LICENSE)

[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en/install-mcp?name=flytoday&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJmbHl0b2RheS1tY3AiXX0=)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_flytoday--mcp-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=flytoday&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22flytoday-mcp%22%5D%7D)

[For AI agents](#for-ai-agents) · [Why](#why) · [What it can do](#what-it-can-do) · [Quick start](#quick-start) · [Tools](#tools) · [Good to know](#good-to-know) · [FAQ](#faq) · [Configuration](#configuration) · [فارسی](#فارسی) · [Development](#development)

</div>

---

## For AI agents

- **What:** unofficial, read-only MCP server (model context protocol) for Flytoday (فلای&zwnj;تودی, flytoday.ir), an Iranian travel site: domestic and international flights, hotels in Iran and abroad, villas and apartments, trains, buses, CIP lounges, eSIMs, visas and activities. Queries work in Persian and English; prices are in Toman.
- **Install:** `uvx flytoday-mcp` or `claude mcp add flytoday -- uvx flytoday-mcp`. Transport: stdio. Needs Python 3.10+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- **Auth:** none, no API key or account. Optional `FLYTODAY_MCP_PROXY` (HTTP proxy, or a comma-separated list), only if your network cannot reach flytoday.ir or your IP is rate limited. System proxy settings are ignored.
- **Access:** read-only. No login, seat hold, booking or payment, so no confirmation is needed before calling any tool. All tools are annotated `readOnlyHint: true`.
- **Tools:** 22, all prefixed `ft_`. Start with a find tool (`ft_find_airport`, `ft_find_hotel_place`, `ft_find_station`, `ft_find_bus_city`) to get ids. Flights: `ft_flight_calendar` → `ft_search_flights` → `ft_flight_details`. Hotels: `ft_search_hotels` → `ft_hotel`. Villas: `ft_search_stays` → `ft_stay`.
- **Units:** prices in Toman in fields named `*_toman` (visa and some activity prices in a foreign currency, see `price_currency`); dates Gregorian `YYYY-MM-DD`, past dates are rejected. `search_id` and `fare_source_code` expire after about 15 minutes.
- **Use when:** comparing flights, hotels, villas, trains or buses on Flytoday, or pricing a CIP lounge, eSIM, visa or activity.
- **Do not use when:** you need to book or pay (the user does that on flytoday.ir). Searches are rate limited per IP, so prefer calendars and paging over repeated searches.
- **Machine-readable:** [`server.json`](https://github.com/sepehr071/flytoday-mcp/blob/main/server.json); catalog of all Iranian MCP servers: [iran-mcp](https://github.com/sepehr071/iran-mcp) ([TOOLS.md](https://github.com/sepehr071/iran-mcp/blob/main/TOOLS.md), [servers.json](https://github.com/sepehr071/iran-mcp/blob/main/servers.json), [llms.txt](https://github.com/sepehr071/iran-mcp/blob/main/llms.txt)).

## Why

Flytoday (flytoday.ir, فلای&zwnj;تودی) sells domestic and international flights, hotels, villas, trains, buses, CIP
lounges, eSIMs and visas. Comparing "which day, plane or train or bus, which hotel" still means many page loads.
An agent with `flytoday-mcp` does it in one go:

> **You:** Cheapest way from Tehran to Mashhad around 20 October?
>
> **Agent:** *calls* `ft_flight_calendar(origin="THR", destination="MHD", date="2026-10-21", window="week")`,
> `ft_train_calendar(origin="1", destination="191")`
>
> | Mode | Cheapest day | Price (Toman) |
> |---|---|---|
> | Flight | 2026-10-20, airline IRZ | 11,056,700 |
> | Train | 2026-10-20 (and most days that week) | **760,000** |
>
> The train is far cheaper; want me to list that day's trains with `ft_search_trains`?

<sub>Real tool output from 2026-10-07; prices and seats change all the time. Prices are in Toman.</sub>

## What it can do

- **Flights:** domestic and international, one-way or round trip, party prices, airline, direct, charter/system and time filters
- **Cheapest day** for flights (+-15 days), trains (~16 days) and buses (~6 weeks)
- **Fare details:** refund and change rules, baggage, international entry notes
- **Hotels** in Iran and abroad: priced search, room and meal offers with cancellation terms, guest reviews
- **Villas and apartments** in Iran: price per night and total, per-date calendar with blocked days
- **Trains and buses:** classes, seats left, stops, amenities, VIP and sleeper buses
- **Extras:** CIP lounges at IKA and MHD, eSIM data plans per country, visa prices and documents, tours and tickets
- **Read-only by design:** no login, no seat hold, no booking, no payment

Example prompts:

- "Cheapest day to fly Tehran to Istanbul next month, and the refund rules of that fare?"
- "Hotels in Mashhad for 2 nights from the 21st, cheapest first, with free cancellation."
- "A villa in Ramsar for 4 people next weekend under 5 million Toman a night."
- "Cheapest train from Tehran to Mashhad this week, and where does it stop?"
- "How much is an eSIM for Turkey, and what documents does a visa need?"
- <span dir="rtl">ارزان&zwnj;ترین اتوبوس VIP تهران به اصفهان دوشنبه بعد؟</span>
- <span dir="rtl">ارزان&zwnj;ترین روز برای پرواز تهران به مشهد در هفته آینده کدام است؟</span>
- <span dir="rtl">قوانین استرداد و بار مجاز این بلیط خارجی چیست؟</span>
- <span dir="rtl">یک ویلا در رامسر برای ۴ نفر آخر هفته زیر ۵ میلیون تومان پیدا کن.</span>

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). No API key or account.

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add flytoday -- uvx flytoday-mcp
```

If Flytoday does not answer from your network, add a proxy:

```bash
claude mcp add flytoday -e FLYTODAY_MCP_PROXY=http://127.0.0.1:8080 -- uvx flytoday-mcp
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

Settings → Developer → Edit Config, then add:

```json
{
  "mcpServers": {
    "flytoday": { "command": "uvx", "args": ["flytoday-mcp"] }
  }
}
```

Need a proxy? Add `"env": { "FLYTODAY_MCP_PROXY": "http://127.0.0.1:8080" }` next to `args`.
</details>

<details>
<summary><b>Cursor</b></summary>

Click **Install in Cursor** above, or add the Claude Desktop block to `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project).
</details>

<details>
<summary><b>VS Code (Copilot agent mode)</b></summary>

Click **Install in VS Code** above, or add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "flytoday": { "type": "stdio", "command": "uvx", "args": ["flytoday-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Codex CLI</b></summary>

```bash
codex mcp add flytoday -- uvx flytoday-mcp
```

Or in `~/.codex/config.toml`:

```toml
[mcp_servers.flytoday]
command = "uvx"
args = ["flytoday-mcp"]
```
</details>

<details>
<summary><b>Gemini CLI</b></summary>

Add to `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "flytoday": { "command": "uvx", "args": ["flytoday-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Windsurf</b></summary>

Add the Claude Desktop block to `~/.codeium/windsurf/mcp_config.json`.
</details>

<details>
<summary><b>Anything else</b></summary>

It's a standard stdio MCP server: run `uvx flytoday-mcp`, or `pip install flytoday-mcp` and run `flytoday-mcp`.
</details>

## Tools

Each vertical has its own ids, taken from its find tool: IATA codes for flights (`THR`, `MHD`, `IST`), hotel
`city_id` / `region_code`, stay `region_code`, train station codes (Tehran `1`, Mashhad `191`), 4-digit bus city
ids (Tehran `0001`, Isfahan `0002`).

<details open>
<summary><b>Flights</b> (4)</summary>

| Tool | What it does |
|---|---|
| `ft_find_airport` | Finds IATA codes for Iranian and international cities and airports, or lists the popular ones |
| `ft_search_flights` | Searches one-way or round-trip flights with filters, sorting and paging, in Toman, with fare_source_code and search_id |
| `ft_flight_calendar` | Shows the cheapest fare per departure day around a date and marks the cheapest day |
| `ft_flight_details` | Gives the refund and change rules, baggage and international entry notes of one fare |
</details>

<details open>
<summary><b>Hotels and stays</b> (6)</summary>

| Tool | What it does |
|---|---|
| `ft_find_hotel_place` | Find city, region and hotel ids for hotels, and region codes for villa stays; popular places when empty |
| `ft_search_hotels` | Priced hotels of a city or region for dates: whole-stay and per-night Toman, stars, score, refundable, meal |
| `ft_hotel` | One hotel: room and meal offers with price, rooms left and cancellation terms, room details, basics |
| `ft_hotel_reviews` | Guest reviews of a hotel with score, text, pros and cons |
| `ft_search_stays` | Villas and apartments in Iran for dates, cheapest first, Toman per night and total |
| `ft_stay` | One villa or apartment: details, price calendar with blocked days, reviews |
</details>

<details open>
<summary><b>Trains</b> (4)</summary>

| Tool | What it does |
|---|---|
| `ft_find_station` | Train station codes by city name, or the popular stations |
| `ft_search_trains` | Trains between two stations on a date: company, class, times, seats left, price per adult |
| `ft_train_calendar` | Cheapest train price per day for the next ~16 days, sold-out days flagged |
| `ft_train_stops` | Stops and times of one train, plus class amenities and meals |
</details>

<details open>
<summary><b>Buses</b> (3)</summary>

| Tool | What it does |
|---|---|
| `ft_find_bus_city` | Bus city ids by name, or the popular cities |
| `ft_bus_calendar` | Cheapest bus seat price per day for the next weeks (the site's price strip) |
| `ft_search_buses` | Buses between two cities on a date: company, terminals, type, seats left, price per seat |
</details>

<details open>
<summary><b>CIP, eSIM, visa, activities</b> (5)</summary>

| Tool | What it does |
|---|---|
| `ft_cip` | CIP / VIP airport lounge prices and add-ons at IKA and MHD for a date and passengers, per passport nationality |
| `ft_esim_plans` | eSIM data plans for a country (GB, days, price in Toman), cheapest first |
| `ft_visa` | Visa country list with from-prices, or one country's prices, documents and processing time |
| `ft_activities` | Tours, tickets and activities in a destination with from-prices for a date range |
| `ft_activity` | One activity: description, options, prices and cheapest days |
</details>

All 22 tools are annotated `readOnlyHint: true` and return compact structured JSON, so they don't flood the agent's context.

## How it works

```text
  AI agent  (Claude, Cursor, Copilot, ...)
      │
      │  MCP over stdio
      ▼
  flytoday-mcp  (runs on your machine)
      │
      │  HTTPS (optional proxy)
      ▼
  www.flytoday.ir/api/gateway  ──▶  api.flytoday.ir, placesearch.flytoday.ir
```

`flytoday-mcp` runs locally and calls the same public gateway the flytoday.ir website uses. There's no hosted
server in between, no API key, and nothing about you is sent anywhere else. Requests go out through a
Chrome-impersonating HTTP client, at most two at a time with a short random pause, and failed calls (timeouts, 5xx,
429) are retried up to twice. Static lookup lists (CIP airports, eSIM countries, visa countries) are cached for 6 hours.

## Good to know

- **Prices are in Toman** in every tool, in fields named `*_toman` (the API sends Rial; values are divided by 10).
  Flight `price_toman` is per adult and `total_toman` the whole party; hotel `price_toman` is the whole stay (with
  `per_night_toman`); stays give `price_per_night_toman` and `total_toman`; trains per adult; buses per seat.
  Visa and some activity prices are in a foreign currency (`price_currency`).
- **Dates** in and out are Gregorian `YYYY-MM-DD`; past dates are rejected. Times are local to the place.
- **Flights** give `airline` (English name) and `airline_fa` (Persian name, when the API has one).
- **Hotels and stays** (search rows, `ft_hotel`, `ft_stay`) carry `image`, the cover photo, built like the site as
  `<cdn>/<id>/main.jpg` (the API has no image field).
- **Searches are rate limited per IP** by Flytoday: about 15 flight/train/bus searches, and hotel search asks for a
  captcha after about 12 searches in 10 minutes (blocked for an hour or more). Use the calendars and paging
  (`search_id`) instead of searching again; the tools never loop searches.
- **Ids expire**: `search_id` and `fare_source_code` are valid for about 15 minutes.
- **Sales windows**: trains open about 16 days ahead. An empty result often just means "not on sale yet".
- When the rail companies' link is down, Flytoday answers HTTP 599; `ft_search_trains` retries three times, then says so.
- **What it does not do:** no booking, seat hold, payment, refund or login, no tour or package search, and no bus seat map.
- **Safety:** read-only. The only POSTs are searches and price lookups the site itself makes before booking.
  At most two requests at a time, and no tool repeats a search in a loop.
- **Proxy:** set `FLYTODAY_MCP_PROXY` if your network cannot reach flytoday.ir (see [Configuration](#configuration)).

## FAQ

<details>
<summary><b>Is there a Flytoday API or MCP server for AI agents?</b></summary>

`flytoday-mcp` is an unofficial MCP server for Flytoday: it exposes the public gateway of the flytoday.ir website
as 22 read-only tools, so an agent can compare flights, hotels, villas, trains and buses.
</details>

<details>
<summary><b>How do I use Flytoday with Claude, Cursor or Copilot?</b></summary>

Install it with `uvx flytoday-mcp` (see [Quick start](#quick-start)), then ask in Persian or English, for example
"cheapest day to fly Tehran to Istanbul next month, and the refund rules of that fare".
</details>

<details>
<summary><b>Is it official, safe and free?</b></summary>

It is unofficial and MIT licensed, so free to use. It is read-only: no login, no API key, no booking or payment,
and it runs on your machine.
</details>

<details>
<summary><b>Do I need a proxy?</b></summary>

Usually not: the website's gateway answers from Iran and from abroad. If your network cannot reach
www.flytoday.ir, or your IP is rate limited, set `FLYTODAY_MCP_PROXY` to an HTTP proxy (or a comma-separated list, used one at a time). System proxy variables
(`HTTPS_PROXY`, ...) are ignored on purpose.
</details>

<details>
<summary><b>Can it book a ticket for me?</b></summary>

No, and that's deliberate. It has no login and never holds a seat, reserves, orders or pays. The agent finds the
best option; you book on flytoday.ir.
</details>

<details>
<summary><b>I get "wants a captcha" or HTTP 429</b></summary>

Flytoday limits searches per IP. Wait (the hotel captcha block can last an hour or more) or use another network
through `FLYTODAY_MCP_PROXY`. Calendars, details, reviews and stay searches are not limited.
If Flytoday serves a bot-check page instead, install the browser extra (`pip install flytoday-mcp[browser]`, or
`uvx --with patchright==1.63.0 flytoday-mcp`) and run `patchright install chromium`; the server then clears it once
in a headless browser.
</details>

<details>
<summary><b>Why is there no bus seat map?</b></summary>

The site's seat map comes from the first call of its checkout flow. It is left out until it is clear that the call
holds nothing.
</details>

<details>
<summary><b>Are prices in Toman or Rial?</b></summary>

Toman. Flytoday's API sends Rial and the tools divide by 10; fields are named `*_toman`. Visa and some activity
prices are in a foreign currency, shown in `price_currency`.
</details>

<details>
<summary><b>How do I debug what the agent sees?</b></summary>

```bash
npx @modelcontextprotocol/inspector uvx flytoday-mcp
```
</details>

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FLYTODAY_MCP_PROXY` | unset | Optional HTTP proxy, e.g. `http://user:pass@host:port`; a comma-separated list is used one at a time (next after a block or connect error). System proxy settings are ignored |

<a id="فارسی"></a>

## فارسی: سرور MCP فلای&zwnj;تودی

<div dir="rtl">

**flytoday-mcp** یک سرور MCP غیررسمی و فقط&zwnj;خواندنی برای فلای&zwnj;تودی (flytoday.ir)، آژانس مسافرتی آنلاین ایران، است. این سرور به دستیار هوش مصنوعی شما (Claude، Cursor، Copilot و ...) اجازه می&zwnj;دهد در فلای&zwnj;تودی ارزان&zwnj;ترین
پرواز داخلی و خارجی، قطار یا اتوبوس را برای یک روز یا یک ماه پیدا کند، قوانین استرداد و بار مجاز را ببیند
و هتل&zwnj;ها و ویلاها را با قیمت دقیق مقایسه کند؛ همچنین قیمت CIP، سیم&zwnj;کارت eSIM و ویزا را می&zwnj;گوید.

**چه کارهایی می&zwnj;کند**

- **پرواز** داخلی و خارجی، یک&zwnj;طرفه و رفت و برگشت، با فیلتر ایرلاین، پرواز مستقیم و ساعت.
- **ارزان&zwnj;ترین روز** برای پرواز، قطار و اتوبوس (تقویم قیمت).
- **قوانین بلیط**: استرداد و تغییر، بار مجاز و نکات ورود به کشور مقصد.
- **هتل** داخلی و خارجی با قیمت دقیق، اتاق&zwnj;ها، شرایط لغو و نظر مهمانان.
- **ویلا و اقامتگاه** در ایران با قیمت هر شب، قیمت کل و تقویم روزهای پر.
- **قطار و اتوبوس**: کلاس، صندلی خالی، ایستگاه&zwnj;ها، امکانات، اتوبوس VIP و تخت&zwnj;شو.
- **خدمات دیگر**: سالن CIP فرودگاه امام و مشهد، بسته اینترنت eSIM، قیمت و مدارک ویزا، تور و بلیت جاذبه&zwnj;ها.

**نکته&zwnj;ها**

- فقط خواندنی است: وارد حساب نمی&zwnj;شود و چیزی رزرو یا پرداخت نمی&zwnj;کند؛ خرید را خودتان در flytoday.ir انجام می&zwnj;دهید.
- همه قیمت&zwnj;ها به تومان است (قیمت ویزا و بعضی فعالیت&zwnj;ها به ارز خارجی). تاریخ&zwnj;ها میلادی هستند.
- فلای&zwnj;تودی جستجو را برای هر IP محدود می&zwnj;کند (حدود ۱۵ جستجوی پرواز، قطار و اتوبوس)؛ از تقویم قیمت استفاده کنید و جستجو را تکرار نکنید.
- کلید API لازم نیست و روی سیستم خود شما اجرا می&zwnj;شود. اگر شبکه شما به flytoday.ir نمی&zwnj;رسد، متغیر `FLYTODAY_MCP_PROXY` را روی یک پراکسی HTTP بگذارید.
- برای پیدا کردن آن در گوگل یا رجیستری MCP، «سرور MCP فلای&zwnj;تودی»، «بلیط هواپیما»، «بلیط قطار»، «بلیط اتوبوس»، «رزرو هتل» یا «flytoday mcp» را جستجو کنید.

**نصب** (اول [uv](https://docs.astral.sh/uv/getting-started/installation/) را نصب کنید). در Claude Code:

</div>

```bash
claude mcp add flytoday -- uvx flytoday-mcp
```

<div dir="rtl">

در Claude Desktop، Cursor و بقیه برنامه&zwnj;ها همان تنظیم بخش [Quick start](#quick-start) را بگذارید.

**نمونه پرسش&zwnj;ها**

- «ارزان&zwnj;ترین راه رفتن از تهران به مشهد حدود ۲۸ مهر چیست؟»
- «ارزان&zwnj;ترین اتوبوس VIP تهران به اصفهان دوشنبه بعد؟»
- «هتل&zwnj;های مشهد برای دو شب با امکان لغو رایگان، از ارزان به گران.»

</div>

## Development

```bash
git clone https://github.com/sepehr071/flytoday-mcp && cd flytoday-mcp
uv sync
uv run python -m pytest  # offline, against recorded responses
uv run python -m pytest -m live  # real API, one search per tool (set FLYTODAY_MCP_PROXY if needed)
uv run ruff check .
```

Tools live in `src/flytoday_mcp/flights.py`, `hotels.py`, `trains.py`, `buses.py` and `extras.py`; each is a typed
async function with a docstring that tells the agent when to use it. Issues and PRs are welcome.

Releases: bump the version in `pyproject.toml` and `server.json`, then push a `v*` tag. GitHub Actions tests,
publishes to PyPI and the [MCP Registry](https://registry.modelcontextprotocol.io), and creates the GitHub Release.

## Related servers

- [iran-mcp](https://github.com/sepehr071/iran-mcp): catalog of all Iranian-service MCP servers
- [MrBilit MCP server](https://github.com/sepehr071/mrbilit-mcp) for flights, trains, buses, taxis and hotels
- [Jabama MCP server](https://github.com/sepehr071/jabama-mcp) for villas, tours and events
- [Otaghak MCP server](https://github.com/sepehr071/otaghak-mcp) for villa, cottage and apartment stays

If this saves you clicks, a star helps others find it.

## Disclaimer

Unofficial and not affiliated with or endorsed by Flytoday. It is read-only and uses the public endpoints of the
flytoday.ir website, which can change without notice. Please keep request rates reasonable.

## Author

<a href="https://www.linkedin.com/in/sepehr-radmard"><img src="https://raw.githubusercontent.com/sepehr071/iran-mcp/main/assets/author-banner.png" alt="Sepehr Radmard: open-source MCP servers for the Iranian web. LinkedIn: linkedin.com/in/sepehr-radmard" width="100%"></a>

Built and maintained by **Sepehr Radmard** (سپهر رادمرد). Connect on [LinkedIn](https://www.linkedin.com/in/sepehr-radmard) or follow [@sepehr071 on GitHub](https://github.com/sepehr071) for more MCP servers for Iranian websites, all listed in [iran-mcp](https://github.com/sepehr071/iran-mcp). If this saves you time, a star on the repo helps other people find it.

## License

[MIT](https://github.com/sepehr071/flytoday-mcp/blob/main/LICENSE)
