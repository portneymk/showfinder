import datetime as dt
import json
from pathlib import Path

from showfinder.sources import jambase, ticketmaster, pollstar
from showfinder import merge, decisions, digest
from showfinder.models import Show, make_show_id

FX = Path(__file__).parent / "fixtures"


def test_jambase_parse():
    shows = jambase.parse_events(json.loads((FX / "jambase_events.json").read_text())["events"])
    assert len(shows) == 2
    w = shows[0]
    assert w.artist == "Wednesday" and w.support == "Hotline TNT"
    assert w.venue == "Mississippi Studios" and w.show_date == "2027-02-14" and w.show_time == "20:00"
    assert w.seller == "Etix" and w.onsale_at.startswith("2026-11-07") and w.price_range == "25-28 USD"
    assert shows[1].price_range == "55 USD" and shows[1].seller == "Portland'5"


def test_ticketmaster_parse_and_merge_presale():
    tm = ticketmaster.parse_events(json.loads((FX / "ticketmaster_events.json").read_text())["_embedded"]["events"])
    jb = jambase.parse_events(json.loads((FX / "jambase_events.json").read_text())["events"])
    assert tm[0].presale_at == "2026-11-12T18:00:00Z"
    merged = merge.dedupe(jb + tm)
    assert len(merged) == 2
    bt = next(s for s in merged if s.artist == "Big Thief")
    assert bt.source == "jambase+ticketmaster"
    assert bt.presale_at == "2026-11-12T18:00:00Z" and bt.onsale_at == "2026-11-14T18:00:00Z"
    assert bt.ticket_url == "https://www.portland5.com/x"  # primary source url kept


def test_show_id_stable_under_noise():
    a = make_show_id("The Wednesday", "Mississippi Studios", "2027-02-14")
    b = make_show_id("Wednesday", "Mississippi Studios (Portland)", "2027-02-14")
    assert a == make_show_id("wednesday", "mississippi studios", "2027-02-14")
    assert a != b  # venue noise changes the key; dedupe relies on sources agreeing on venue names


def test_merge_preserves_decisions():
    old = Show(artist="Wednesday", venue="Mississippi Studios", show_date="2027-02-14", status="GO", action="find_code", onsale_at="")
    new = Show(artist="Wednesday", venue="Mississippi Studios", show_date="2027-02-14", onsale_at="2026-11-07T10:00:00-08:00", source="jambase")
    rows, diff = merge.merge_into_existing([old], [new])
    assert rows[0].status == "GO" and rows[0].action == "find_code"
    assert rows[0].onsale_at.startswith("2026-11-07")
    assert diff["new"] == [] and old.show_id in diff["changed"]


def test_filters():
    s = [Show(artist="X", venue="Moda Center", show_date="2027-01-01"), Show(artist="Y", venue="Holocene", show_date="2027-01-01")]
    assert [x.venue for x in merge.apply_filters(s, ["moda center"], [])] == ["Holocene"]


def test_decisions_parser():
    numbering = {1: "a", 2: "b", 3: "c", 4: "d", 5: "e", 6: "f"}
    res = decisions.parse("""
x 1, 3
go 2 buy
GO 4 code floor max $80 +1
maybe 5
never Moda Center
bought 6 order 12-34567 sec 104
never 1
""", numbering)
    assert not res.errors
    d = {x.show_id: x for x in res.decisions}
    assert d["a"].status == "NEVER"  # last write wins per show in caller; parser emits both
    assert [x.status for x in res.decisions if x.show_id == "a"] == ["SKIP", "NEVER"]
    assert d["c"].status == "SKIP"
    assert d["b"].status == "GO" and d["b"].action == "buy_now"
    assert d["d"].action == "find_code" and d["d"].seat_pref == "floor" and d["d"].max_price == "80" and d["d"].companions == "+1"
    assert d["e"].status == "MAYBE"
    assert d["f"].status == "BOUGHT" and "order 12-34567" in d["f"].note
    assert res.never_names == ["Moda Center"]


def test_decisions_default_action_and_errors():
    res = decisions.parse("go 1\nfly me to the moon\nx 9", {1: "a"})
    assert res.decisions[0].action == "alert"
    assert len(res.errors) == 2


def test_digest_numbering():
    rows = [
        Show(artist="Wednesday", venue="Mississippi Studios", show_date="2027-02-14", onsale_at="2026-11-07T10:00:00-08:00", price_range="25 USD"),
        Show(artist="Big Thief", venue="Schnitzer", show_date="2027-03-02", presale_at="2026-11-02T10:00:00-08:00", onsale_at="2026-11-04T10:00:00-08:00", status="GO", action="find_code"),
        Show(artist="Old Band", venue="Holocene", show_date="2026-11-08", onsale_at="2026-09-01T10:00:00-07:00", status="GO", action="alert"),
    ]
    text, numbering = digest.build(rows, tz="America/Los_Angeles", today=dt.date(2026, 10, 29))
    assert numbering[1] == rows[0].show_id            # new show first
    assert "presale Mon Nov 2" in text and "no code yet" in text
    assert "no ticket logged, show in 10 days" in text
    assert len(numbering) == 3


def test_pollstar_jsonld_extraction():
    html = '<html><script type="application/ld+json">{"@type":"MusicEvent","name":"X","startDate":"2027-01-05T20:00","location":{"name":"Doug Fir Lounge","address":{"addressLocality":"Portland"}},"performer":[{"name":"X"}],"offers":{"url":"https://t/1"}}</script></html>'
    evs = pollstar.extract_jsonld_events(html)
    shows = pollstar.parse_events(evs)
    assert shows[0].venue == "Doug Fir Lounge" and shows[0].source == "pollstar"
