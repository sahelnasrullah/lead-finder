"""OpenStreetMap provider (Overpass API). Free, works in any country."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable

import httpx

from ..config import Settings
from ..leadutil import LeadData
from ..niches import find_niche, sanitize_keyword
from .base import ProviderError, SearchCriteria

log = logging.getLogger(__name__)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CACHE_TTL_SECONDS = 24 * 3600
_ISO = re.compile(r"^[A-Z]{2}$")


def _q(value: str) -> str:
    """Escape a value for use inside an Overpass double-quoted string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_query(criteria: SearchCriteria, bbox: tuple[float, float, float, float] | None = None) -> str:
    """Build an Overpass QL query for the criteria (niche mapping or a generic keyword fallback).

    bbox is (south, west, north, east) for a city search; without it the whole country is searched.
    """
    country = criteria.country.upper()
    if not _ISO.match(country):
        raise ProviderError(f"Invalid country code: {criteria.country!r}")
    limit = max(1, min(int(criteria.limit), 500))
    fetch_limit = min(max(limit * 3, 60), 600)

    niche = find_niche(criteria.niche)
    scope = "(area.a)" if bbox is None else "({},{},{},{})".format(*bbox)
    if niche:
        selectors = [f"nwr{f}{scope};" for f in niche.osm_filters]
    else:
        kw = sanitize_keyword(criteria.niche)
        if not kw:
            raise ProviderError("Niche/keyword is empty after cleaning.")
        rx = _q(re.escape(kw))
        selectors = [
            f'nwr["name"~"{rx}",i]{scope};',
            f'nwr[~"^(office|shop|amenity|craft|healthcare)$"~"^{rx}$",i]{scope};',
        ]

    lines = ["[out:json][timeout:90];"]
    if bbox is None:
        lines.append(f'area["ISO3166-1"="{country}"]["admin_level"="2"]->.a;')
    lines.append("(")
    lines.extend(f"  {s}" for s in selectors)
    lines.append(");")
    lines.append(f"out tags center {fetch_limit};")
    return "\n".join(lines)


def _first(tags: dict, *keys: str) -> str:
    for k in keys:
        v = tags.get(k)
        if v:
            return str(v).strip()
    return ""


def parse_elements(elements: list[dict], criteria: SearchCriteria, niche_label: str) -> list[LeadData]:
    leads: list[LeadData] = []
    for el in elements:
        tags = el.get("tags") or {}
        name = _first(tags, "name", "operator", "brand")
        if not name:
            continue
        street = _first(tags, "addr:street")
        number = _first(tags, "addr:housenumber")
        address = " ".join(p for p in (street, number) if p)
        postcode = _first(tags, "addr:postcode")
        city = _first(tags, "addr:city") or criteria.city
        if postcode and address:
            address = f"{address}, {postcode}"
        leads.append(
            LeadData(
                name=name,
                address=address,
                city=city,
                country=criteria.country.upper(),
                industry=niche_label,
                website=_first(tags, "website", "contact:website", "url"),
                phone=_first(tags, "phone", "contact:phone", "mobile", "contact:mobile"),
                email=_first(tags, "email", "contact:email").split(";")[0].strip(),
                source="OpenStreetMap",
            )
        )
    return leads


class OsmProvider:
    name = "OpenStreetMap"

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        endpoints: list[str] | None = None,
    ):
        self.settings = settings
        self.client = client or httpx.Client(
            timeout=120, headers={"User-Agent": settings.user_agent}
        )
        self.sleep = sleep
        self.endpoints = endpoints or OVERPASS_ENDPOINTS

    # -- cache -------------------------------------------------------------
    def _cache_path(self, query: str) -> Path:
        digest = hashlib.sha256(query.encode()).hexdigest()[:32]
        return self.settings.cache_dir / f"overpass_{digest}.json"

    def _cache_get(self, query: str) -> dict | None:
        path = self._cache_path(query)
        try:
            if path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS:
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return None

    def _cache_put(self, query: str, data: dict) -> None:
        try:
            self.settings.cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(query).write_text(json.dumps(data), encoding="utf-8")
        except OSError:  # cache is best-effort
            log.warning("Could not write Overpass cache")

    # -- query -------------------------------------------------------------
    def _run(self, query: str) -> dict:
        cached = self._cache_get(query)
        if cached is not None:
            return cached
        last_error = "unknown error"
        for endpoint in self.endpoints:
            for attempt in range(3):
                try:
                    resp = self.client.post(endpoint, data={"data": query})
                except httpx.HTTPError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    self.sleep(2 ** attempt)
                    continue
                if resp.status_code in (429, 502, 503, 504):
                    last_error = f"HTTP {resp.status_code} from {endpoint}"
                    self.sleep(2 ** attempt * 2)
                    continue
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    break
                try:
                    data = resp.json()
                except ValueError:
                    last_error = "Overpass returned invalid JSON"
                    break
                self._cache_put(query, data)
                return data
        raise ProviderError(f"OpenStreetMap (Overpass) query failed: {last_error}")

    def _geocode(self, city: str, country: str) -> tuple[float, float, float, float]:
        """City -> (south, west, north, east) via Nominatim (cached)."""
        key = f"nominatim|{country}|{city.lower()}"
        cached = self._cache_get(key)
        if cached is None:
            try:
                resp = self.client.get(
                    NOMINATIM_URL,
                    params={"q": city, "countrycodes": country.lower(), "format": "json", "limit": 1},
                )
                resp.raise_for_status()
                cached = {"results": resp.json()}
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError(f"Could not look up the city '{city}': {exc}") from exc
            self._cache_put(key, cached)
        results = cached.get("results") or []
        if not results:
            raise ProviderError(f"City '{city}' was not found in country {country}.")
        south, north, west, east = (float(x) for x in results[0]["boundingbox"])
        return south, west, north, east

    def search(self, criteria: SearchCriteria) -> list[LeadData]:
        city = sanitize_keyword(criteria.city)
        bbox = self._geocode(city, criteria.country.upper()) if city else None
        query = build_query(criteria, bbox)
        data = self._run(query)
        niche = find_niche(criteria.niche)
        label = niche.label if niche else criteria.niche
        leads = parse_elements(data.get("elements", []), criteria, label)
        # Prefer entries that are easier to contact, then cut to the requested limit.
        leads.sort(key=lambda l: (not l.website, not l.email, not l.phone))
        return leads
