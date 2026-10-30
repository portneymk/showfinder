"""Build the nightly digest text and its item numbering."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from .models import Show


def _local(iso: str, tz: str) -> str:
    if not iso:
        return "on-sale TBA"
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if t.tzinfo is None:
        t = t.replace(tzinfo=ZoneInfo(tz))
    return t.astimezone(ZoneInfo(tz)).strftime("%a %b %-d %-I:%M%p").replace("AM", "am").replace("PM", "pm")


def _date(d: str) -> str:
    try:
        return dt.date.fromisoformat(d).strftime("%a %b %-d")
    except ValueError:
        return d


def build(rows: list[Show], *, tz: str, today: dt.date | None = None,
          regret_guard_days: int = 14, horizon_days: int = 7,
          include_filtered: bool = False) -> tuple[str, dict[int, str]]:
    today = today or dt.datetime.now(ZoneInfo(tz)).date()
    week = today + dt.timedelta(days=horizon_days)
    numbering: dict[int, str] = {}
    n = 0
    out: list[str] = []

    def item(s: Show, line: str) -> None:
        nonlocal n
        existing = next((k for k, v in numbering.items() if v == s.show_id), None)
        if existing is None:
            n += 1
            numbering[n] = s.show_id
            existing = n
        out.append(f"{existing}. {line}")

    all_new = [s for s in rows if s.status == "NEW"]
    if include_filtered:
        new = all_new
    else:
        new = [s for s in all_new if s.taste != "no"]
    filtered = len(all_new) - len(new)
    new.sort(key=lambda s: (s.soonest_action_at, s.show_date))
    out.append(f"**New shows ({len(new)})** — sorted by when tickets act")
    if filtered:
        out.append(f"_{filtered} more filtered out by taste; say `show filtered` to see them_")
    if not new:
        out.append("_nothing new_")
    for s in new:
        when = s.presale_at or s.onsale_at
        tag = "presale" if s.presale_at else "on sale"
        price = f" · {s.price_range}" if s.price_range else ""
        why = f" · _{s.taste_reason}_" if s.taste_reason else ""
        support = f" w/ {s.support}" if s.support else ""
        when_txt = f" · {tag} {_local(when, tz)}" if when else ""
        where = f"{s.venue}" + (f" ({s.metro.title()})" if s.metro and s.metro != "portland" else "")
        item(s, f"**{s.artist}**{support} — {where}, {_date(s.show_date)}{when_txt}{price}{why}")

    out.append("")
    out.append("**This week for your GO shows**")
    go = [s for s in rows if s.status == "GO"]
    acting: dict[str, tuple[str, list[str], Show]] = {}
    for s in go:
        for label, iso in (("presale", s.presale_at), ("on sale", s.onsale_at)):
            if not iso:
                continue
            try:
                d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(tz)).date()
            except ValueError:
                continue
            if today <= d <= week:
                first, parts, _ = acting.get(s.show_id, (iso, [], s))
                parts.append(f"{label} {_local(iso, tz)}")
                acting[s.show_id] = (min(first, iso), parts, s)
    if not acting:
        out.append("_nothing acts this week_")
    for first, parts, s in sorted(acting.values(), key=lambda t: t[0]):
        code = f" · code **{s.code}** ({s.code_source})" if s.code else (" · no code yet" if s.action == "find_code" else "")
        item(s, f"**{s.artist}** {', '.join(parts)} — {s.venue} {_date(s.show_date)}{code} · {s.ticket_url}")

    out.append("")
    out.append("**Codes found**")
    found = [s for s in go if s.code and s.code_status == "found"]
    if not found:
        out.append("_none_")
    for s in found:
        item(s, f"**{s.artist}** — {s.code} via {s.code_source}")

    out.append("")
    out.append("**Waiting on you**")
    waiting = []
    for s in go:
        try:
            show_day = dt.date.fromisoformat(s.show_date)
        except ValueError:
            continue
        past_onsale = bool(s.onsale_at) and s.onsale_at[:10] <= today.isoformat()
        if past_onsale and (show_day - today).days <= regret_guard_days:
            waiting.append((s, f"no ticket logged, show in {(show_day - today).days} days"))
        elif s.code_status == "needs_you":
            waiting.append((s, f"needs your tap: {s.notes}"))
    if not waiting:
        out.append("_nothing_")
    for s, why in waiting:
        item(s, f"**{s.artist}** — {s.venue} {_date(s.show_date)} · {why}")

    out.append("")
    out.append("Reply by number: `x 1 3`, `go 2 buy`, `go 4 alert`, `go 5 code floor max 80`, `maybe 6`, `never 7`, `bought 2 order …`")
    return "\n".join(out), numbering
