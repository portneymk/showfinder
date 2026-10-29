"""Parse the owner's shorthand replies to a numbered digest.

Grammar (one command per line, case-insensitive; commas optional):

    x 1 3 5                      -> SKIP items 1, 3, 5
    skip 2                       -> SKIP
    go 2 buy                     -> GO, action buy_now
    go 4 alert                   -> GO, action alert
    go 6 code floor max 80 +1    -> GO, action find_code, seat_pref floor,
                                    max_price 80, companions "+1"
    go 7 resale max 120          -> GO, action watch_resale
    maybe 8                      -> MAYBE
    never 9                      -> NEVER (artist of item 9)
    never Moda Center            -> add venue/artist name to never list
    bought 2 order 12-34567 sec 104 row F
                                 -> BOUGHT, notes appended
    note 3 bring earplugs        -> append to notes

`numbering` maps digest item numbers to show_ids.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ACTION_WORDS = {
    "buy": "buy_now", "buynow": "buy_now", "buy_now": "buy_now", "now": "buy_now",
    "alert": "alert", "onsale": "alert", "remind": "alert", "tell": "alert",
    "code": "find_code", "presale": "find_code", "find_code": "find_code", "signup": "find_code",
    "resale": "watch_resale", "watch": "watch_resale", "watch_resale": "watch_resale",
}
SEAT_WORDS = {"floor", "ga", "pit", "seats", "seated", "balcony", "reserved", "lawn"}
STATUS_WORDS = {
    "x": "SKIP", "skip": "SKIP", "no": "SKIP", "nah": "SKIP", "pass": "SKIP",
    "go": "GO", "yes": "GO", "y": "GO", "check": "GO",
    "maybe": "MAYBE", "snooze": "MAYBE",
    "never": "NEVER",
    "bought": "BOUGHT", "got": "BOUGHT",
    "note": "NOTE",
}


@dataclass
class Decision:
    show_id: str
    status: str = ""
    action: str = ""
    seat_pref: str = ""
    max_price: str = ""
    companions: str = ""
    note: str = ""

    def as_updates(self) -> dict:
        d = {}
        if self.status and self.status != "NOTE":
            d["status"] = self.status
        for k in ("action", "seat_pref", "max_price", "companions"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        if self.note:
            d["notes_append"] = self.note
        return d


@dataclass
class ParseResult:
    decisions: list[Decision] = field(default_factory=list)
    never_names: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


_NUM = re.compile(r"^\d+$")


def parse(text: str, numbering: dict[int, str]) -> ParseResult:
    res = ParseResult()
    for raw in text.splitlines():
        line = raw.strip().rstrip(".")
        if not line:
            continue
        tokens = [t for t in re.split(r"[,\s]+", line) if t]
        head = tokens[0].lower()
        status = STATUS_WORDS.get(head)
        if status is None:
            res.errors.append(f"don't understand: {raw!r}")
            continue
        nums, rest = [], []
        for t in tokens[1:]:
            (nums if _NUM.match(t) and not rest else rest).append(t)
        if status == "NEVER" and not nums:
            res.never_names.append(" ".join(tokens[1:]))
            continue
        if not nums:
            res.errors.append(f"no item number in: {raw!r}")
            continue
        for n in nums:
            sid = numbering.get(int(n))
            if sid is None:
                res.errors.append(f"item {n} is not in the digest")
                continue
            d = Decision(show_id=sid, status=status)
            _apply_rest(d, rest)
            res.decisions.append(d)
    return res


def _apply_rest(d: Decision, rest: list[str]) -> None:
    i, free = 0, []
    while i < len(rest):
        t = rest[i].lower()
        if d.status == "GO" and t in ACTION_WORDS and not d.action:
            d.action = ACTION_WORDS[t]
        elif t in SEAT_WORDS:
            d.seat_pref = t
        elif t in ("max", "under", "cap") and i + 1 < len(rest):
            d.max_price = rest[i + 1].lstrip("$")
            i += 1
        elif re.match(r"^\+\d+$", t):
            d.companions = t
        elif t == "with" and i + 1 < len(rest):
            d.companions = rest[i + 1]
            i += 1
        else:
            free.append(rest[i])
        i += 1
    if d.status == "GO" and not d.action:
        d.action = "alert"
    if free:
        d.note = " ".join(free)
