"""Taste scoring: pluggable providers that turn a Show into (p_want, reason).

Providers:
  - JevProvider  : TypeSafe AI's Jev (System One API). One Noul ("would the owner
                   want to know?") and one Choice ("why?") per show, batched, with
                   config/taste.md sent as state. Key: TYPESAFE_API_KEY, or a
                   network secret on api.typesafe.ai in the cloud environment.
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
    """TypeSafe AI System One client (Jev). POST https://api.typesafe.ai/v1/systemone

    Request : {"model": "jev-latest", "state": {...}, "questions": {name: {...}}}
    Noul    : {"type": "noul", "instructions": str | dict, "criteria": {"true":..,"false":..}}
    Choice  : {"type": "choice", "instructions": str | dict, "criteria": {label: desc}}
    Response: {"model": "jev-1.13.0", "answers": {name: {"type": "noul", "noul": p} |
                                                  {"type": "choice", "choice": label,
                                                   "probabilities": {...}, "confidence": c}},
               "usage": {...}}

    Auth: Bearer TYPESAFE_API_KEY. If the key is absent we send no header and rely
    on the cloud environment's network secret for api.typesafe.ai to attach it.
    Several shows go in one request (structured instructions, one Noul + one Choice
    per show) so the taste profile is sent once per batch.
    """
    name = "jev"
    WANT = ("Would the owner want to be told that this show exists, given `taste_profile`? "
            "Judge the headliner first, then support acts, then the venue.")
    WANT_CRITERIA = {
        "true": "An artist on the show matches the profile's yes lists or clearly belongs to a scene the profile says yes to; "
                "or the profile asks for unknown acts at this venue to be surfaced.",
        "false": "The show matches a no list, a no scene, an avoided venue, or the profile gives no signal for it.",
    }
    WHY_CRITERIA = {
        "artist": "The headliner is named in the profile or is an obvious fit for a yes scene",
        "support": "Only a support act matches the profile",
        "venue": "No artist match; the venue is one the profile likes",
        "scene_no": "Belongs to a scene or format the profile rejects (tribute act, DJ night, etc.)",
        "no_signal": "Nothing in the profile speaks to this show",
    }

    def __init__(self, profile_text: str, endpoint: str | None = None, api_key: str | None = None,
                 model: str = "jev-latest", batch_size: int = 8, timeout: float = 30.0, transport=None):
        base = (endpoint or os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai")).rstrip("/")
        self.url = base + "/v1/systemone"
        self.api_key = api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY", "")
        self.model, self.batch_size, self.timeout = model, batch_size, timeout
        self.profile_text = profile_text
        self.transport = transport  # callable(payload) -> response dict, for tests

    @staticmethod
    def encode_show(show: Show) -> dict:
        return {
            "headliner": show.artist,
            "support": [x.strip() for x in show.support.split(",") if x.strip()],
            "venue": show.venue,
            "city": show.city,
            "date": show.show_date,
        }

    def payload(self, shows: list[Show]) -> dict:
        questions = {}
        for s in shows:
            q = {"show": self.encode_show(s)}
            questions[f"want_{s.show_id}"] = {"type": "noul", "instructions": {**q, "question": self.WANT},
                                               "criteria": self.WANT_CRITERIA}
            questions[f"why_{s.show_id}"] = {"type": "choice", "instructions": {**q, "question": "Why?"},
                                              "criteria": self.WHY_CRITERIA}
        return {"model": self.model, "state": {"taste_profile": self.profile_text}, "questions": questions}

    def _post(self, payload: dict) -> dict:
        if self.transport:
            return self.transport(payload)
        import requests, time, random
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        delay = 0.5
        for attempt in range(6):
            r = requests.post(self.url, json=payload, headers=headers, timeout=self.timeout)
            if r.ok:
                return r.json()
            if (r.status_code not in (408, 429, 529) and r.status_code < 500) or attempt == 5:
                raise RuntimeError(f"jev {r.status_code}: {r.text[:200]}")
            ra = r.headers.get("retry-after")
            time.sleep(min(float(ra) if ra else delay * (1 - 0.25 * random.random()), 60))
            delay = min(delay * 2, 5)
        raise AssertionError("unreachable")

    def score_batch(self, shows: list[Show]) -> dict[str, Verdict]:
        out = {}
        for i in range(0, len(shows), self.batch_size):
            chunk = shows[i:i + self.batch_size]
            answers = self._post(self.payload(chunk)).get("answers", {})
            for s in chunk:
                want = answers.get(f"want_{s.show_id}", {})
                why = answers.get(f"why_{s.show_id}", {})
                if "noul" not in want:
                    continue
                p = max(0.0, min(1.0, float(want["noul"])))
                label = why.get("choice", "")
                conf = why.get("confidence")
                reason = {"artist": "artist matches your profile", "support": "support act matches your profile",
                          "venue": "unknown act at a venue you like", "scene_no": "a scene or format you said no to",
                          "no_signal": "nothing in your profile speaks to it"}.get(label, label or "")
                if conf is not None:
                    reason += f" (why-confidence {float(conf):.2f})"
                out[s.show_id] = Verdict(p, reason, self.name)
        return out

    def score(self, show: Show) -> Verdict:
        v = self.score_batch([show]).get(show.show_id)
        if v is None:
            raise RuntimeError("jev returned no answer")
        return v


def score_rows(rows: list[Show], provider: Provider, *, yes_at=0.6, no_below=0.25, only_unscored=True) -> dict:
    """Return {show_id: {"taste":..., "taste_reason":...}} suitable for `apply-taste`."""
    todo = [s for s in rows if not (only_unscored and s.taste)]
    verdicts: dict[str, Verdict] = {}
    if hasattr(provider, "score_batch"):
        try:
            verdicts = provider.score_batch(todo)
        except Exception as e:  # noqa: BLE001
            log.warning("%s batch failed: %s", provider.name, e)
    else:
        for s in todo:
            try:
                verdicts[s.show_id] = provider.score(s)
            except Exception as e:  # noqa: BLE001
                log.warning("%s failed on %s: %s", provider.name, s.show_id, e)
    return {sid: {"taste": v.label(yes_at, no_below), "taste_reason": f"{v.reason} ({v.provider} p={v.p_want:.2f})"}
            for sid, v in verdicts.items()}
