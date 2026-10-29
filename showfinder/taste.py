"""Taste scoring: pluggable providers that turn a Show into (p_want, reason).

Providers:
  - JevProvider  : the owner's typesafe probability model served from the DGX.
                   HTTP POST; endpoint, key and schema come from config/env.
                   Request/response mapping lives in `JevProvider.encode/decode`
                   and is a STUB until the API spec is supplied.
  - RulesProvider: deterministic baseline from config/taste.md style lists
                   (yes/no artists, yes/no venues). Used as a sanity floor and
                   as the fallback when Jev is unreachable.
  - The LLM (the Claude session) can also write verdicts directly via
    `showfinder apply-taste`; that path needs no code here.

Verdict thresholds (config "taste": {"yes_at": 0.6, "no_below": 0.25}):
  p >= yes_at   -> yes
  p <  no_below -> no
  otherwise     -> maybe
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Protocol

from .models import Show, normalize

log = logging.getLogger(__name__)


@dataclass
class Verdict:
    p_want: float
    reason: str
    provider: str

    def label(self, yes_at: float = 0.6, no_below: float = 0.25) -> str:
        if self.p_want >= yes_at:
            return "yes"
        if self.p_want < no_below:
            return "no"
        return "maybe"


class Provider(Protocol):
    name: str

    def score(self, show: Show) -> Verdict: ...


class RulesProvider:
    name = "rules"

    def __init__(self, yes_artists=(), no_artists=(), yes_venues=(), no_venues=()):
        self.ya = {normalize(a) for a in yes_artists}
        self.na = {normalize(a) for a in no_artists}
        self.yv = {normalize(v) for v in yes_venues}
        self.nv = {normalize(v) for v in no_venues}

    def score(self, show: Show) -> Verdict:
        acts = [normalize(show.artist)] + [normalize(x) for x in show.support.split(",") if x.strip()]
        if acts[0] in self.na:
            return Verdict(0.02, f"{show.artist} is on the no list", self.name)
        if acts[0] in self.ya:
            return Verdict(0.95, f"{show.artist} is on the yes list", self.name)
        hit = [a for a in acts[1:] if a in self.ya]
        if hit:
            return Verdict(0.55, f"support act {hit[0]} is on the yes list", self.name)
        if normalize(show.venue) in self.nv:
            return Verdict(0.05, f"{show.venue} is on the avoid list", self.name)
        if normalize(show.venue) in self.yv:
            return Verdict(0.35, f"unknown act at {show.venue}, a venue you like", self.name)
        return Verdict(0.15, "no signal in the profile", self.name)


class JevProvider:
    """HTTP client for Jev. STUB: encode/decode must match Jev's actual API."""
    name = "jev"

    def __init__(self, endpoint: str | None = None, api_key: str | None = None, timeout: float = 20.0):
        self.endpoint = endpoint or os.environ.get("JEV_ENDPOINT", "")
        self.api_key = api_key or os.environ.get("JEV_API_KEY", "")
        self.timeout = timeout
        if not self.endpoint:
            raise RuntimeError("JEV_ENDPOINT not set")

    @staticmethod
    def encode(show: Show) -> dict:
        # Features Jev gets per show. Adjust to Jev's typed schema once known.
        return {
            "artist": show.artist,
            "support": [x.strip() for x in show.support.split(",") if x.strip()],
            "venue": show.venue,
            "city": show.city,
            "date": show.show_date,
            "seller": show.seller,
        }

    @staticmethod
    def decode(body: dict) -> Verdict:
        # Expected (placeholder): {"p": 0.0-1.0, "reason": "..."}
        p = float(body.get("p", body.get("probability", 0.0)))
        return Verdict(max(0.0, min(1.0, p)), str(body.get("reason", "")), "jev")

    def score(self, show: Show) -> Verdict:
        import requests
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        r = requests.post(self.endpoint, json=self.encode(show), headers=headers, timeout=self.timeout)
        r.raise_for_status()
        return self.decode(r.json())


def score_rows(rows: list[Show], provider: Provider, *, yes_at=0.6, no_below=0.25, only_unscored=True) -> dict:
    """Return {show_id: {"taste":..., "taste_reason":...}} suitable for `apply-taste`."""
    out = {}
    for s in rows:
        if only_unscored and s.taste:
            continue
        try:
            v = provider.score(s)
        except Exception as e:  # noqa: BLE001
            log.warning("%s failed on %s: %s", provider.name, s.show_id, e)
            continue
        out[s.show_id] = {"taste": v.label(yes_at, no_below), "taste_reason": f"{v.reason} ({v.provider} p={v.p_want:.2f})"}
    return out
