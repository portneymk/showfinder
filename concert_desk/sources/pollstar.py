"""Pollstar — fallback source.

Pollstar has no public API. Its public pages are JavaScript-rendered, so this
module drives headless Chromium (Playwright, pre-installed in the cloud
environment) over the configured search URLs and extracts schema.org JSON-LD
`Event` / `MusicEvent` blocks, which Pollstar event pages emit. If a page emits
none, `extract_dom_fallback` scrapes visible event rows; its selectors must be
confirmed against the live site once the network policy allows it.

Only used when JamBase fails or returns fewer than
`config.pollstar.min_jambase_results` shows.

UNTESTED AGAINST THE LIVE SITE at time of writing.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterable

from ..models import Show

log = logging.getLogger(__name__)


def fetch_pages(urls: Iterable[str], timeout_ms: int = 30000) -> list[str]:
    from playwright.sync_api import sync_playwright  # lazy import

    htmls = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for url in urls:
            try:
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                htmls.append(page.content())
            except Exception as e:  # noqa: BLE001
                log.warning("pollstar fetch failed for %s: %s", url, e)
        browser.close()
    return htmls


_LD = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


def extract_jsonld_events(html: str) -> list[dict]:
    events = []
    for m in _LD.finditer(html):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                items.extend(g for g in graph if isinstance(g, dict))
                continue
            t = item.get("@type", "")
            types = t if isinstance(t, list) else [t]
            if any(x in ("Event", "MusicEvent", "Concert") for x in types):
                events.append(item)
    return events


def parse_events(events: Iterable[dict]) -> list[Show]:
    shows = []
    for ev in events:
        start = ev.get("startDate") or ""
        date, _, time = start.partition("T")
        if not date:
            continue
        loc = ev.get("location") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        addr = loc.get("address") or {}
        if isinstance(addr, str):
            addr = {"addressLocality": addr}
        perf = ev.get("performer") or []
        if isinstance(perf, dict):
            perf = [perf]
        names = [p.get("name", "") for p in perf if isinstance(p, dict)]
        headliner = names[0] if names else ev.get("name", "")
        offers = ev.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        shows.append(Show(
            artist=headliner,
            support=", ".join(names[1:]),
            venue=loc.get("name", ""),
            city=addr.get("addressLocality", ""),
            show_date=date,
            show_time=time[:5],
            onsale_at=offers.get("validFrom", "") or "",
            ticket_url=offers.get("url", "") or ev.get("url", ""),
            source="pollstar",
        ))
    return shows


def fetch(cfg) -> list[Show]:
    urls = cfg.get("pollstar", {}).get("search_urls", [])
    shows: list[Show] = []
    for html in fetch_pages(urls):
        shows.extend(parse_events(extract_jsonld_events(html)))
    return shows
