"""Deduplicate shows across sources and refresh facts on existing rows."""
from __future__ import annotations

import datetime as dt
from typing import Iterable

from .models import Show, normalize

SOURCE_PRIORITY = {"jambase": 0, "ticketmaster": 1, "pollstar": 2}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def dedupe(shows: Iterable[Show]) -> list[Show]:
    """Collapse shows with the same id; fill blanks from lower-priority sources."""
    by_id: dict[str, Show] = {}
    for s in sorted(shows, key=lambda s: SOURCE_PRIORITY.get(s.source, 9)):
        cur = by_id.get(s.show_id)
        if cur is None:
            by_id[s.show_id] = s
            continue
        for k, v in s.facts().items():
            if v and not getattr(cur, k):
                setattr(cur, k, v)
        # presale info is the one thing Ticketmaster knows better than JamBase
        if s.source == "ticketmaster":
            if s.presale_at:
                cur.presale_at, cur.presale_name = s.presale_at, s.presale_name
            if s.onsale_at:
                cur.onsale_at = s.onsale_at
        if s.source not in cur.source.split("+"):
            cur.source = f"{cur.source}+{s.source}"
    return list(by_id.values())


def apply_filters(shows: Iterable[Show], never_venues: list[str], never_artists: list[str]) -> list[Show]:
    nv = {normalize(v) for v in never_venues if v}
    na = {normalize(a) for a in never_artists if a}
    return [s for s in shows if normalize(s.venue) not in nv and normalize(s.artist) not in na]


def _inherit_artist_decision(f: Show, existing: list[Show], window_days: int) -> None:
    """A NEVER on an artist anywhere, or a SKIP on the same artist within `window_days`,
    closes a newly seen show for that artist in every metro."""
    key = normalize(f.artist)
    try:
        fd = dt.date.fromisoformat(f.show_date)
    except ValueError:
        return
    for e in existing:
        if normalize(e.artist) != key or e.status not in ("SKIP", "NEVER"):
            continue
        if e.status == "NEVER":
            f.status, f.notes = "NEVER", f"closed: artist marked never ({e.metro or 'home'} {e.show_date})"
            return
        try:
            ed = dt.date.fromisoformat(e.show_date)
        except ValueError:
            continue
        if abs((fd - ed).days) <= window_days:
            f.status, f.notes = "SKIP", f"closed via {e.metro or 'home'} {e.show_date} decision"
            return


def merge_into_existing(existing: list[Show], fresh: list[Show], *, skip_window_days: int = 180) -> tuple[list[Show], dict]:
    """Return (all rows, diff). Decision fields on existing rows are preserved."""
    now = _now()
    by_id = {s.show_id: s for s in existing}
    new_rows, changed_rows = [], []
    for f in fresh:
        cur = by_id.get(f.show_id)
        if cur is None:
            _inherit_artist_decision(f, existing, skip_window_days)
            f.first_seen = f.last_updated = now
            by_id[f.show_id] = f
            new_rows.append(f)
            continue
        changes = {}
        for k, v in f.facts().items():
            if v and v != getattr(cur, k):
                changes[k] = (getattr(cur, k), v)
                setattr(cur, k, v)
        if f.metro and not cur.metro:
            cur.metro = f.metro
        if changes:
            cur.last_updated = now
            changed_rows.append((cur, changes))
    rows = sorted(by_id.values(), key=lambda s: (s.soonest_action_at, s.show_date))
    diff = {
        "new": [s.show_id for s in new_rows],
        "auto_closed": [s.show_id for s in new_rows if s.status in ("SKIP", "NEVER")],
        "changed": {s.show_id: {k: list(v) for k, v in ch.items()} for s, ch in changed_rows},
    }
    return rows, diff
