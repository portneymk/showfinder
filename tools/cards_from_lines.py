"""Convert compact pipe-delimited lines (one card per line) into page_NN.json for import-cards.

Line format (9 fields, '|' separated; empty allowed):
  id|title|startDate|venue|city|performers(; separated, headliner first)|slug|ticketUrl|ticketLabel
`slug` becomes https://www.jambase.com/show/<slug>. City blank => Portland.
Usage: python tools/cards_from_lines.py out/jambase_cards/page_04.json < lines.txt
"""
import json, sys
out = sys.argv[1]
cards = []
for raw in sys.stdin:
    raw = raw.rstrip("\n")
    if not raw.strip() or raw.startswith("#"):
        continue
    f = raw.split("|")
    f += [""] * (9 - len(f))
    cid, title, start, venue, city, perf, slug, turl, tlabel = f[:9]
    cards.append({"id": cid.strip() if cid.strip().startswith("jambase:") else f"jambase:{cid.strip()}",
                  "title": title.strip(), "startDate": start.strip(), "venue": venue.strip(),
                  "city": city.strip() or "Portland", "region": "OR",
                  "performers": [p.strip() for p in perf.split(";") if p.strip()] or [title.strip()],
                  "jambaseUrl": f"https://www.jambase.com/show/{slug.strip()}" if slug.strip() else "",
                  "ticketUrl": turl.strip(), "ticketLabel": tlabel.strip()})
json.dump(cards, open(out, "w"), ensure_ascii=False, indent=0)
print(out, len(cards))
