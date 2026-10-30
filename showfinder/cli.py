from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Config
from .models import Show, SHEET_COLUMNS
from . import merge, digest, decisions

log = logging.getLogger("showfinder")


def _load_rows(path: Path) -> list[Show]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [Show(**{k: v for k, v in d.items() if k in SHEET_COLUMNS}) for d in data]


def _dump_rows(rows: list[Show], path: Path) -> None:
    path.write_text(json.dumps([dict(zip(SHEET_COLUMNS, r.to_row())) for r in rows], indent=1))


def cmd_ingest(a) -> int:
    cfg = Config.load(a.config)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    fresh: list[Show] = []
    status = {}
    from .sources import jambase, ticketmaster, pollstar
    try:
        jb = jambase.parse_events(jambase.fetch(cfg))
        fresh.extend(jb); status["jambase"] = len(jb)
    except Exception as e:  # noqa: BLE001
        log.error("jambase failed: %s", e); status["jambase"] = f"ERROR {e}"
        jb = []
    pcfg = cfg.get("pollstar", {})
    if pcfg.get("enabled") and len(jb) < pcfg.get("min_jambase_results", 25):
        try:
            ps = pollstar.fetch(cfg); fresh.extend(ps); status["pollstar"] = len(ps)
        except Exception as e:  # noqa: BLE001
            log.error("pollstar failed: %s", e); status["pollstar"] = f"ERROR {e}"
    if cfg.get("ticketmaster", {}).get("enabled", True):
        try:
            tm = ticketmaster.parse_events(ticketmaster.fetch(cfg)); fresh.extend(tm); status["ticketmaster"] = len(tm)
        except Exception as e:  # noqa: BLE001
            log.error("ticketmaster failed: %s", e); status["ticketmaster"] = f"ERROR {e}"
    return _finish_merge(cfg, fresh, out, status)


def _finish_merge(cfg, fresh, out: Path, status: dict) -> int:
    fresh = merge.dedupe(fresh)
    fresh = merge.apply_filters(fresh, cfg.get("never_venues", []), cfg.get("never_artists", []))
    existing = _load_rows(out / "rows.json")
    rows, diff = merge.merge_into_existing(existing, fresh)
    _dump_rows(rows, out / "rows.json")
    diff["source_status"] = status
    watch = {v.lower() for v in cfg.get("venue_watchlist", [])}
    seen = {r.venue for r in rows if r.venue.lower() in watch}
    diff["watchlist_coverage"] = {"hit": sorted(seen), "missing": sorted(v for v in cfg.get("venue_watchlist", []) if v not in seen)}
    (out / "diff.json").write_text(json.dumps(diff, indent=1))
    print(json.dumps({"rows": len(rows), "new": len(diff["new"]), "changed": len(diff["changed"]), "sources": status,
                      "watchlist_hit": len(seen), "watchlist_total": len(watch)}, indent=1))
    return 0


def cmd_import_cards(a) -> int:
    """Merge compact JamBase MCP cards (saved by the session) into rows.json."""
    from .sources import jambase_cards
    cfg = Config.load(a.config)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    cards = jambase_cards.load_dir(a.dir)
    fresh = jambase_cards.parse_cards(cards)
    return _finish_merge(cfg, fresh, out, {"jambase_mcp_cards": len(fresh)})


def cmd_apply_taste(a) -> int:
    """Apply {show_id: {"taste": "yes|no|maybe", "taste_reason": "..."}} to rows.json."""
    rows = _load_rows(Path(a.rows))
    verdicts = json.loads(Path(a.taste).read_text())
    n = 0
    for r in rows:
        v = verdicts.get(r.show_id)
        if v:
            r.taste = v.get("taste", r.taste); r.taste_reason = v.get("taste_reason", r.taste_reason); n += 1
    _dump_rows(rows, Path(a.rows))
    print(json.dumps({"applied": n, "unmatched": len(set(verdicts) - {r.show_id for r in rows})}))
    return 0


def cmd_score(a) -> int:
    """Score unscored rows with a provider and write out/taste.json for apply-taste."""
    from . import taste
    cfg = Config.load(a.config)
    rows = _load_rows(Path(a.rows))
    tcfg = cfg.get("taste", {})
    if a.provider == "jev":
        prov = taste.JevProvider(Path(a.profile).read_text(), model=tcfg.get("jev", {}).get("model", "jev-latest"),
                                 batch_size=int(tcfg.get("jev", {}).get("batch_size", 8)))
    else:
        prov = taste.RulesProvider(tcfg.get("yes_artists", []), tcfg.get("no_artists", []),
                                   tcfg.get("yes_venues", []), tcfg.get("no_venues", []))
    verdicts = taste.score_rows(rows, prov, yes_at=tcfg.get("yes_at", 0.6), no_below=tcfg.get("no_below", 0.25),
                                only_unscored=not a.rescore)
    Path(a.out).write_text(json.dumps(verdicts, indent=1))
    from collections import Counter
    print(json.dumps({"scored": len(verdicts), "by_label": dict(Counter(v["taste"] for v in verdicts.values())), "provider": prov.name}))
    return 0


def cmd_digest(a) -> int:
    cfg = Config.load(a.config)
    rows = _load_rows(Path(a.rows))
    text, numbering = digest.build(rows, tz=cfg.get("timezone", "America/Los_Angeles"),
                                   regret_guard_days=int(cfg.get("regret_guard_days", 14)),
                                   include_filtered=a.all)
    Path(a.numbering).write_text(json.dumps(numbering, indent=1))
    print(text)
    return 0


def cmd_decide(a) -> int:
    numbering = {int(k): v for k, v in json.loads(Path(a.numbering).read_text()).items()}
    text = Path(a.reply).read_text() if a.reply != "-" else sys.stdin.read()
    res = decisions.parse(text, numbering)
    print(json.dumps({"decisions": [{"show_id": d.show_id, **d.as_updates()} for d in res.decisions],
                      "never_names": res.never_names, "errors": res.errors}, indent=1))
    return 1 if res.errors and not res.decisions else 0


def cmd_rows_to_sheet(a) -> int:
    """Emit rows.json as a 2D array (header + rows) for the Sheets connector."""
    rows = _load_rows(Path(a.rows))
    ids = set(json.loads(a.only_ids)) if a.only_ids else None
    table = [SHEET_COLUMNS] if a.header else []
    table += [r.to_row() for r in rows if ids is None or r.show_id in ids]
    print(json.dumps(table))
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="showfinder")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ingest"); s.add_argument("--config", default="config/portland.json"); s.add_argument("--out", default="out"); s.set_defaults(fn=cmd_ingest)
    s = sub.add_parser("digest"); s.add_argument("--config", default="config/portland.json"); s.add_argument("--rows", default="out/rows.json"); s.add_argument("--numbering", default="out/numbering.json"); s.add_argument("--all", action="store_true", help="include taste-filtered shows"); s.set_defaults(fn=cmd_digest)
    s = sub.add_parser("import-cards"); s.add_argument("--config", default="config/portland.json"); s.add_argument("--dir", default="out/jambase_cards"); s.add_argument("--out", default="out"); s.set_defaults(fn=cmd_import_cards)
    s = sub.add_parser("score"); s.add_argument("--config", default="config/portland.json"); s.add_argument("--rows", default="out/rows.json"); s.add_argument("--provider", choices=["rules", "jev"], default="rules"); s.add_argument("--profile", default="config/taste.md"); s.add_argument("--out", default="out/taste.json"); s.add_argument("--rescore", action="store_true"); s.set_defaults(fn=cmd_score)
    s = sub.add_parser("apply-taste"); s.add_argument("--rows", default="out/rows.json"); s.add_argument("--taste", default="out/taste.json"); s.set_defaults(fn=cmd_apply_taste)
    s = sub.add_parser("decide"); s.add_argument("--numbering", default="out/numbering.json"); s.add_argument("--reply", default="-"); s.set_defaults(fn=cmd_decide)
    s = sub.add_parser("rows-to-sheet"); s.add_argument("--rows", default="out/rows.json"); s.add_argument("--only-ids", default=""); s.add_argument("--header", action="store_true"); s.set_defaults(fn=cmd_rows_to_sheet)
    a = p.parse_args(argv)
    return a.fn(a)
