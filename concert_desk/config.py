from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    raw: dict

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        with open(path) as f:
            return cls(json.load(f))

    def __getitem__(self, k):
        return self.raw[k]

    def get(self, k, default=None):
        return self.raw.get(k, default)

    @property
    def jambase_key(self) -> str:
        return os.environ.get("JAMBASE_API_KEY", "")

    @property
    def ticketmaster_key(self) -> str:
        return os.environ.get("TICKETMASTER_API_KEY", "")
