"""JamBase Data API v3 — primary source.

Docs: https://apidocs.jambase.com  Key: https://data.jambase.com (free
Developer tier for non-commercial use). Events come back in schema.org
MusicEvent shape; the parser below only relies on fields that shape defines
(name, startDate, location, performer, offers) and tolerates anything missing.

UNTESTED AGAINST LIVE RESPONSES at time of writing (network policy denied the
host). `parse_events` is covered by tests/fixtures/jambase_events.json.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Iterable

import requests

from ..models import Show

log = logging.getLogger(__name__)
BASE = "https://www.jambase.com/jb-api/v1/events"


def fetch(cfg, months_ahead: int | None = None, session=None) -> list[dict]:
    key = cfg.jambase_key
    if not key:
        raise RuntimeError("JAMBASE_API_KEY not set")
    geo = cfg["geo"]
    today = dt.date.today()
    end = today + dt.timedelta(days=30 * (months_ahead or cfg.get("months_ahead", 6)))
    params = {
        "apikey": key,
        "geoLatitude": geo["lat"],
        "geoLongitude": geo["lon"],
        "geoRadiusAmount": geo["radius_miles"],
        "geoRadiusUnits": "mi",
        "eventDateFrom": today.isoformat(),
        "eventDateTo": end.isoformat(),
        "perPage": 100,
        "page": 1,
    }
    s = session or requests.Session()
    out: list[dict] = []
    while True:
        r = s.get(BASE, params=params, timeout=30, headers={"Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        events = data.get("events") or []
        out.extend(events)
        pag = data.get("pagination") or {}
        total_pages = int(pag.get("totalPages") or 1)
        log.info("jambase page %s/%s: %d events", params["page"], total_pages, len(events))
        if params["page"] >= total_pages or not events:
            break
        params["page"] += 1
    return out


def _first(xs, default=None):
    return xs[0] if isinstance(xs, list) and xs else default


def _performers(ev: dict) -> tuple[str, str]:
    perf = ev.get("performer") or []
    if isinstance(perf, dict):
        perf = [perf]
    names = [p.get("name", "") for p in perf if isinstance(p, dict)]
    head = [p.get("name", "") for p in perf if isinstance(p, dict) and p.get("x-isHeadliner")]
    headliner = head[0] if head else (names[0] if names else ev.get("name", ""))
    support = ", ".join(n for n in names if n and n != headliner)
    return headliner, support


def _offer(ev: dict) -> dict:
    offers = ev.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    offers = [o for o in offers if isinstance(o, dict)]
    if not offers:
        return {}
    # prefer the primary seller over resale
    primaries = [o for o in offers if "resale" not in (o.get("name") or "").lower()]
    return primaries[0] if primaries else offers[0]


def _price(offer: dict) -> str:
    ps = offer.get("priceSpecification") or {}
    lo, hi = ps.get("minPrice"), ps.get("maxPrice")
    cur = ps.get("priceCurrency", "USD")
    if lo is None and hi is None:
        p = offer.get("price")
        return f"{p} {cur}" if p is not None else ""
    if lo is not None and hi is not None and lo != hi:
        return f"{lo}-{hi} {cur}"
    return f"{lo if lo is not None else hi} {cur}"


def parse_events(events: Iterable[dict]) -> list[Show]:
    shows = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        start = ev.get("startDate") or ""
        date, _, time = start.partition("T")
        if not date:
            continue
        loc = ev.get("location") or {}
        addr = loc.get("address") or {}
        headliner, support = _performers(ev)
        offer = _offer(ev)
        seller = (offer.get("seller") or {}).get("name", "") if isinstance(offer.get("seller"), dict) else ""
        shows.append(Show(
            artist=headliner,
            support=support,
            venue=loc.get("name", ""),
            city=addr.get("addressLocality", ""),
            show_date=date,
            show_time=time[:5],
            onsale_at=offer.get("validFrom", "") or "",
            ticket_url=offer.get("url", "") or ev.get("url", ""),
            seller=seller,
            price_range=_price(offer),
            source="jambase",
        ))
    return shows
