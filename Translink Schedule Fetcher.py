"""
Translink Newry ---> Belfast availability checker 

- Prompts for a date and time
- Loads the Translink journey planner with query parameters
- Scrapes departure times, arrival times, prices and mode
- Never logs in, never books, never checks out
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from playwright.async_api import async_playwright


# 
# URL construction
# 

BASE = "https://www.translink.co.uk/journeyplannerv2"

PARAMS = {
    "departureOrArrivalDate": None,  # filled in per run
    "destinationId": "10000055",
    "destinationName": "Belfast, Grand Central Station",
    "destinationType": "Train stop",
    "findBusDepartures": "true",
    "findTrainDepartures": "true",
    "fromAirport": "false",
    "hasStaffedStations": "false",
    "hasStepFreeAccess": "false",
    "isEnterpriseOnly": "false",
    "isQuickBuy": "false",
    "isSearchByDepartureDate": "true",
    "originId": "suburbID:31400130:706:Newry:708457:173448:ITMR",
    "originName": "Newry",
    "originType": "suburb",
    "toAirport": "false",
}


def build_url(dt_iso: str) -> str:
    params = {**PARAMS, "departureOrArrivalDate": dt_iso}
    return f"{BASE}?{urlencode(params)}"


# 
# User input parsing
# 

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday"]


def parse_date(raw: str) -> datetime:
    """Parse a date string. Accepts YYYY-MM-DD, 'today', 'tomorrow',
    a weekday name, or 'next <weekday>'."""
    raw = raw.strip().lower()
    now = datetime.now(timezone.utc)

    if raw == "today":
        return now
    if raw == "tomorrow":
        return now + timedelta(days=1)

    next_week = raw.startswith("next ")
    name = raw[5:] if next_week else raw

    if name in WEEKDAYS:
        target = WEEKDAYS.index(name)
        days_ahead = (target - now.weekday()) % 7
        if days_ahead == 0 or next_week:
            days_ahead += 7
        return now + timedelta(days=days_ahead)

    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(
            f"Couldn't parse date {raw!r}. "
            "Use YYYY-MM-DD, 'today', 'tomorrow', or a weekday name."
        )


def parse_time(raw: str) -> tuple[int, int]:
    """Parse 'HH:MM' (24-hour) into (hour, minute)."""
    raw = raw.strip()
    try:
        t = datetime.strptime(raw, "%H:%M")
        return t.hour, t.minute
    except ValueError:
        raise ValueError(
            f"Couldn't parse time {raw!r}. Use 24-hour HH:MM, e.g. 09:00."
        )


def local_to_utc_iso(date: datetime, hour: int, minute: int) -> str:
    """Combine a date with a local UK time, convert to UTC, return ISO 8601 with Z."""
    local = date.replace(
        hour=hour, minute=minute, second=0, microsecond=0,
        tzinfo=ZoneInfo("Europe/London"),
    )
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prompt_for_datetime() -> str:
    """Ask the user for a date and time; return ISO 8601 UTC string with Z."""
    while True:
        try:
            date_raw = input(
                "Date (YYYY-MM-DD, today, tomorrow, or a weekday): "
            )
            time_raw = input(
                "Departure time after (HH:MM, UK local time): "
            )
            date = parse_date(date_raw)
            hour, minute = parse_time(time_raw)
            return local_to_utc_iso(date, hour, minute)
        except ValueError as e:
            print(f"  {e}\n  Try again.\n")


# 
# Scraping
#  
    """Collapse all whitespace (incl. \\xa0, \\n, \\t) to single spaces."""
    return re.sub(r"\s+", " ", text).strip()


async def scrape_journeys(page) -> list[dict]:
    """Return a list of dicts with depart, arrive, price, mode."""
    # Wait for a journey button to appear
    await page.wait_for_selector(
        "button[aria-label^='View journey']", timeout=25000
    )

    journeys: list[dict] = []
    items = await page.query_selector_all("li.tw-group")

    for li in items:
        raw = await li.inner_text()
        text = normalise(raw)
        if not text:
            continue

        time_match = re.search(r"(\d{2}:\d{2}) - (\d{2}:\d{2})", text)
        price_match = re.search(r"£ ?(\d+(?:\.\d{2})?)", text)
        mode = "rail" if "Rail" in text else "bus"

        if time_match:
            journeys.append({
                "depart": time_match.group(1),
                "arrive": time_match.group(2),
                "price": f"£{price_match.group(1)}" if price_match else "n/a",
                "mode": mode,
            })

    return journeys


# 
# Main
#

async def main():
    print("Translink Newry to Belfast availability checker\n")
    iso = prompt_for_datetime()
    url = build_url(iso)

    print(f"\nSearching for departures at/after {iso} (UTC)...")
    print(f"URL: {url}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded")
            journeys = await scrape_journeys(page)
        except Exception as e:
            print(f"Failed to scrape: {e}")
            await browser.close()
            return

        if not journeys:
            print("No journeys found.")
        else:
            print(f"Found {len(journeys)} journeys:\n")
            for j in journeys:
                print(
                    f"  {j['depart']} → {j['arrive']}  "
                    f"({j['mode']})  {j['price']}"
                )

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())