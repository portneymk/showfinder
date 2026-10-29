"""Show model and normalization helpers shared by every source."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Optional

SHEET_COLUMNS = [
    "show_id", "artist", "support", "venue", "city", "show_date", "show_time",
    "onsale_at", "presale_at", "presale_name", "ticket_url", "seller",
    "price_range", "source", "status", "action", "companions", "max_price",
    "seat_pref", "code", "code_source", "code_status", "notes", "first_seen",
    "last_updated", "calendar_event_id", "taste", "taste_reason",
]

_STRIP_WORDS = {"the", "a", "an", "and", "&", "at", "presents", "live"}


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, drop filler words."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    words = [w for w in text.split() if w not in _STRIP_WORDS]
    return " ".join(words)


def make_show_id(artist: str, venue: str, show_date: str) -> str:
    key = f"{normalize(artist)}|{normalize(venue)}|{show_date}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


@dataclass
class Show:
    artist: str
    venue: str
    show_date: str                  # YYYY-MM-DD local
    show_time: str = ""             # HH:MM local, may be empty
    support: str = ""
    city: str = ""
    onsale_at: str = ""             # ISO 8601 if known
    presale_at: str = ""
    presale_name: str = ""
    ticket_url: str = ""
    seller: str = ""
    price_range: str = ""
    source: str = ""
    # decision fields, owned by the person, never overwritten by ingest
    status: str = "NEW"
    action: str = ""
    companions: str = ""
    max_price: str = ""
    seat_pref: str = ""
    code: str = ""
    code_source: str = ""
    code_status: str = ""
    notes: str = ""
    first_seen: str = ""
    last_updated: str = ""
    calendar_event_id: str = ""
    taste: str = ""                 # yes / no / maybe, set by the taste filter
    taste_reason: str = ""
    show_id: str = field(default="")

    def __post_init__(self):
        if not self.show_id:
            self.show_id = make_show_id(self.artist, self.venue, self.show_date)

    def to_row(self) -> list:
        d = asdict(self)
        return [d.get(c, "") or "" for c in SHEET_COLUMNS]

    @classmethod
    def from_row(cls, row: list) -> "Show":
        padded = list(row) + [""] * (len(SHEET_COLUMNS) - len(row))
        kwargs = dict(zip(SHEET_COLUMNS, padded))
        return cls(**kwargs)

    @property
    def soonest_action_at(self) -> str:
        """Earliest of presale / on-sale, used for digest ordering."""
        candidates = [t for t in (self.presale_at, self.onsale_at) if t]
        return min(candidates) if candidates else "9999"

    def facts(self) -> dict:
        """Fields that ingest may refresh."""
        return {
            k: getattr(self, k) for k in (
                "support", "city", "show_time", "onsale_at", "presale_at",
                "presale_name", "ticket_url", "seller", "price_range", "source",
            )
        }
