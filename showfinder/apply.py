"""Apply parsed decisions to rows, propagating artist-level closes across every metro.

Rules:
  SKIP on a show  -> that show SKIP, and every other NEW/MAYBE row for the same artist
                     (any metro) within `window_days` of it becomes SKIP with a note.
  NEVER on a show -> that show NEVER, every row for the same artist (any metro, any date,
                     unless BOUGHT) becomes NEVER, and the artist joins never_artists.
  GO / MAYBE / BOUGHT / NOTE -> that show only (you pick one date and city to go to).
Returns the list of rows that changed so the caller can write just those to the sheet.
"""
from __future__ import annotations

import datetime as dt

from .decisions import Decision
from .models import Show, normalize


def apply(rows: list[Show], decisions: list[Decision], never_names: list[str] | None = None,
          *, window_days: int = 180) -> tuple[list[Show], list[str]]:
    by_id = {r.show_id: r for r in rows}
    changed: dict[str, Show] = {}
    new_never: list[str] = []

    def touch(r: Show):
        r.last_updated = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
        changed[r.show_id] = r

    for d in decisions:
        r = by_id.get(d.show_id)
        if r is None:
            continue
        upd = d.as_updates()
        note = upd.pop("notes_append", "")
        for k, v in upd.items():
            setattr(r, k, v)
        if note:
            r.notes = (r.notes + " | " if r.notes else "") + note
        touch(r)
        if d.status in ("SKIP", "NEVER"):
            key = normalize(r.artist)
            try:
                rd = dt.date.fromisoformat(r.show_date)
            except ValueError:
                rd = None
            for o in rows:
                if o.show_id == r.show_id or normalize(o.artist) != key or o.status == "BOUGHT":
                    continue
                if d.status == "NEVER":
                    o.status = "NEVER"
                    o.notes = (o.notes + " | " if o.notes else "") + f"closed: artist marked never ({r.metro or 'home'} {r.show_date})"
                    touch(o)
                elif o.status in ("NEW", "MAYBE") and rd is not None:
                    try:
                        od = dt.date.fromisoformat(o.show_date)
                    except ValueError:
                        continue
                    if abs((od - rd).days) <= window_days:
                        o.status = "SKIP"
                        o.notes = (o.notes + " | " if o.notes else "") + f"closed via {r.metro or 'home'} {r.show_date} decision"
                        touch(o)
            if d.status == "NEVER":
                new_never.append(r.artist)

    # bare names: "never Moda Center" / "never Gorillaz"
    for name in never_names or []:
        key = normalize(name)
        for o in rows:
            if (normalize(o.artist) == key or normalize(o.venue) == key) and o.status != "BOUGHT":
                o.status = "NEVER"
                o.notes = (o.notes + " | " if o.notes else "") + f"closed: never {name}"
                touch(o)
        new_never.append(name)
    return list(changed.values()), new_never
