"""Parse compact JamBase MCP connector cards saved by the nightly session.

The connector (mcp.jambase.com) returns `jambase.cards/v1` cards: id, title,
startDate, venue, headliners, and CTA links. It does NOT carry on-sale dates
or prices; those come only from the REST API (sources/jambase.py).
Files: out/jambase_cards/page_NN.json, each a JSON array of compact cards
with keys id,title,startDate,venue,city,region,performers,jambaseUrl,
ticketUrl,ticketLabel.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from ..models import Show

_SELLER = re.compile(r"Tickets \((.+?)\)")


def load_dir(path: str | Path) -> list[dict]:
    cards: list[dict] = []
    for f in sorted(Path(path).glob("page_*.json")):
        cards.extend(json.loads(f.read_text()))
    return cards


def parse_cards(cards: Iterable[dict]) -> list[Show]:
    shows, seen = [], set()
    for c in cards:
        cid = c.get("id", "")
        if cid in seen:
            continue
        seen.add(cid)
        start = c.get("startDate") or ""
        date, _, time = start.partition("T")
        if not date:
            continue
        perf = c.get("performers") or []
        headliner = perf[0] if perf else c.get("title", "")
        m = _SELLER.search(c.get("ticketLabel") or "")
        url = c.get("ticketUrl") or c.get("jambaseUrl") or ""
        shows.append(Show(
            artist=headliner,
            support=", ".join(perf[1:]),
            venue=c.get("venue", ""),
            city=c.get("city", ""),
            show_date=date,
            show_time=time[:5],
            ticket_url=url,
            seller=m.group(1) if m else "",
            notes="",
            source="jambase",
        ))
    return shows
