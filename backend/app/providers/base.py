"""Provider interfaces shared by all data sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..leadutil import LeadData


class ProviderError(Exception):
    """Raised when a data source cannot be queried (network, rate limit, bad response)."""


@dataclass
class SearchCriteria:
    country: str = "SE"
    niche: str = ""
    city: str = ""
    limit: int = 50


class Provider(Protocol):
    name: str

    def search(self, criteria: SearchCriteria) -> list[LeadData]: ...
