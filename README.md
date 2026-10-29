# Concert Desk

Nightly concert discovery, triage and presale tracking for Portland, run as a
Claude Code scheduled task in the cloud. The laptop can be closed; nothing runs
locally.

## How it works

1. **Ingest** (`python -m showfinder ingest`): pulls the next N months of
   shows for the configured metro. JamBase Data API v3 is the primary source
   (it aggregates Ticketmaster, Etix, TicketWeb, AXS, DICE and others). Pollstar
   is the fallback when JamBase fails or returns thin results. Ticketmaster's
   read-only Discovery API is used only to enrich presale windows; no
   Ticketmaster account is involved.
2. **Merge**: shows are deduplicated across sources on
   `(normalized headliner, normalized venue, local date)`. Existing rows keep
   their decisions; only facts (on-sale, price, URL) are refreshed.
3. **Sheet**: the Google Sheet "Concert Desk" is the source of truth. The
   script emits `out/rows.json` (full table) and `out/diff.json` (new rows,
   changed rows); the Claude session writes those to the sheet through the
   Sheets connector.
4. **Decisions**: the owner answers the nightly digest in chat with shorthand
   (`x 1 3`, `go 2 buy`, `go 4 code floor max 80`, `never Moda Center`) or
   flips the Status/Action dropdowns in the sheet. `python -m showfinder
   decide` parses the shorthand against the numbered digest.
5. **Digest** (`python -m showfinder digest`): four sections, numbered.
   New shows by on-sale date; happening this week for GO shows; codes found;
   waiting on you.

Presale **codes** are never inferred. They come from Gmail (the Claude session
searches for presale emails matching GO shows with action `find_code`) and are
recorded with their source. A card with no code says so.

## Status vocabulary

| Status | Meaning |
|---|---|
| NEW | not yet triaged |
| GO | want to go; `Action` says what to do about tickets |
| MAYBE | snoozed until two weeks before on-sale |
| SKIP | not this one |
| NEVER | hide this artist (or venue, via Config) permanently |
| BOUGHT | ticket in hand; order details in Notes |

Actions on a GO row: `buy_now`, `alert`, `find_code`, `watch_resale`.

## Running

```bash
pip install -r requirements.txt
export JAMBASE_API_KEY=...        # https://data.jambase.com (free Developer tier)
export TICKETMASTER_API_KEY=...   # https://developer.ticketmaster.com (free)
python -m showfinder ingest --config config/portland.json --out out/
python -m showfinder digest --rows out/rows.json --decisions out/decisions.json
```

Network hosts the cloud environment must allow: `www.jambase.com`,
`data.jambase.com`, `app.ticketmaster.com`, `www.pollstar.com`.

## Layout

- `showfinder/sources/jambase.py` — primary source
- `showfinder/sources/pollstar.py` — fallback (Playwright, JSON-LD extraction)
- `showfinder/sources/ticketmaster.py` — presale window enrichment only
- `showfinder/merge.py` — dedupe and row refresh
- `showfinder/decisions.py` — shorthand parser
- `showfinder/digest.py` — nightly digest text
- `config/portland.json` — metro, radius, venue watchlist
- `tests/` — offline tests (parser, merge, fixture-based source parsing)

## Verification status

Written while the cloud environment's network policy denied every ticketing
host, so the JamBase, Ticketmaster and Pollstar fetchers are untested against
live responses. Parsers are tested against fixtures built from the documented
response shapes. First live run is the acceptance test: does the Portland pull
include Mississippi Studios, Holocene, Wonder Ballroom and Revolution Hall?
