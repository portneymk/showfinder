"""Ticketmaster Discovery API — presale window enrichment only.

Read-only, developer key, no Ticketmaster account. Used to attach
`sales.public.startDateTime` and `sales.presales[]` to shows already in the
table. Shows found only here are also returned so big-venue dates are not
missed, but JamBase is the primary source.

UNTESTED AGAINST LIVE RESPONSES at time of writing.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Iterable

import requests

from ..models import Show

log = logging.getLogger(__name__)
BASE = "https://app.ticketmaster.com/discovery/v2/events.json"


def fetch(cfg, months_ahead: int | None = None, session=None) -> list[dict]:
    key = cfg.ticketmaster_key
    if not key:
        log.warning("TICKETMASTER_API_KEY not set; skipping presale enrichment")
        return []
    geo = cfg["geo"]
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    end = now + dt.timedelta(days=30 * (months_ahead or cfg.get("months_ahead", 6)))
    params = {
        "apikey": key,
        "latlong": f"{geo['lat']},{geo['lon']}",
        "radius": geo["radius_miles"],
        "unit": "miles",
        "classificationName": cfg.get("ticketmaster", {}).get("classification", "music"),
        "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "size": 200,
        "page": 0,
        "sort": "date,asc",
    }
    s = session or requests.Session()
    out: list[dict] = []
    while True:
        r = s.get(BASE, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        events = (data.get("_embedded") or {}).get("events") or []
        out.extend(events)
        page = data.get("page") or {}
        total = int(page.get("totalPages") or 1)
        log.info("ticketmaster page %s/%s: %d events", params["page"] + 1, total, len(events))
        # Discovery caps deep paging at page*size <= 1000
        if params["page"] + 1 >= total or not events or (params["page"] + 1) * 200 >= 1000:
            break
        params["page"] += 1
    return out


def parse_events(events: Iterable[dict]) -> list[Show]:
    shows = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        dates = (ev.get("dates") or {}).get("start") or {}
        date = dates.get("localDate", "")
        if not date:
            continue
        emb = ev.get("_embedded") or {}
        venue = (emb.get("venues") or [{}])[0]
        attractions = [a.get("name", "") for a in emb.get("attractions") or []]
        headliner = attractions[0] if attractions else ev.get("name", "")
        sales = ev.get("sales") or {}
        public = sales.get("public") or {}
        presales = sales.get("presales") or []
        presales = sorted((p for p in presales if p.get("startDateTime")), key=lambda p: p["startDateTime"])
        first_presale = presales[0] if presales else {}
        prices = ev.get("priceRanges") or []
        price = ""
        if prices:
            lo = min(p.get("min", 0) for p in prices)
            hi = max(p.get("max", 0) for p in prices)
            price = f"{lo}-{hi} {prices[0].get('currency', 'USD')}"
        shows.append(Show(
            artist=headliner,
            support=", ".join(a for a in attractions[1:] if a),
            venue=venue.get("name", ""),
            city=(venue.get("city") or {}).get("name", ""),
            show_date=date,
            show_time=(dates.get("localTime") or "")[:5],
            onsale_at=public.get("startDateTime", "") or "",
            presale_at=first_presale.get("startDateTime", "") or "",
            presale_name="; ".join(f"{p.get('name','')}@{p.get('startDateTime','')}" for p in presales),
            ticket_url=ev.get("url", ""),
            seller="Ticketmaster",
            price_range=price,
            source="ticketmaster",
        ))
    return shows
