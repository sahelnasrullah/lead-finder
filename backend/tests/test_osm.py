import json
from pathlib import Path

import httpx
import pytest

from app.providers.base import ProviderError, SearchCriteria
from app.providers.osm import OsmProvider, build_query

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "overpass_lawyers.json").read_text())


def make_provider(settings, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OsmProvider(settings, client=client, sleep=lambda s: None, endpoints=["https://overpass.test/api"])


def test_build_query_for_mapped_niche_country_wide():
    q = build_query(SearchCriteria("SE", "law firms", "", 20))
    assert 'area["ISO3166-1"="SE"]' in q and 'nwr["office"="lawyer"](area.a);' in q
    assert q.rstrip().endswith("out tags center 60;")


def test_build_query_city_uses_bbox():
    q = build_query(SearchCriteria("SE", "law firms", "Göteborg", 20), bbox=(57.5, 11.8, 57.9, 12.1))
    assert 'nwr["office"="lawyer"](57.5,11.8,57.9,12.1);' in q and "area.a" not in q


def test_build_query_generic_keyword_is_escaped():
    q = build_query(SearchCriteria("SE", 'sushi"; drop', "", 10))
    assert '"; drop' not in q and "sushi" in q and "drop" in q


def test_build_query_rejects_bad_country_and_empty_keyword():
    with pytest.raises(ProviderError):
        build_query(SearchCriteria("SWE", "law firms"))
    with pytest.raises(ProviderError):
        build_query(SearchCriteria("SE", "!!!"))


def test_search_parses_fixture(settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=FIXTURE)

    leads = make_provider(settings, handler).search(SearchCriteria("SE", "law firms", "", 10))
    names = [l.name for l in leads]
    assert "Berg Juristbyrå" in names and "Nilsson & Co Advokatbyrå AB" in names
    assert len(leads) == 3  # nameless element dropped (dedupe happens in the pipeline)
    nilsson = next(l for l in leads if l.website.startswith("www") or "nilsson" in l.website)
    assert nilsson.country == "SE" and nilsson.source == "OpenStreetMap" and nilsson.industry == "Law firms"
    berg = next(l for l in leads if l.name == "Berg Juristbyrå")
    assert berg.email == "info@berg.example" and berg.website == "https://berg.example/"
    assert len(calls) == 1


def test_results_are_cached(settings):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=FIXTURE)

    provider = make_provider(settings, handler)
    provider.search(SearchCriteria("SE", "law firms"))
    provider.search(SearchCriteria("SE", "law firms"))
    assert len(calls) == 1


def test_retries_then_succeeds_on_rate_limit(settings):
    responses = iter([httpx.Response(429), httpx.Response(200, json=FIXTURE)])
    provider = make_provider(settings, lambda r: next(responses))
    assert provider.search(SearchCriteria("SE", "law firms"))


def test_failure_raises_provider_error(settings):
    provider = make_provider(settings, lambda r: httpx.Response(504))
    with pytest.raises(ProviderError):
        provider.search(SearchCriteria("SE", "law firms"))


def test_city_geocoding_error(settings):
    def handler(request):
        return httpx.Response(200, json=[])  # Nominatim finds nothing

    with pytest.raises(ProviderError, match="not found"):
        make_provider(settings, handler).search(SearchCriteria("SE", "law firms", "Nowhereville"))


def test_city_search_uses_nominatim_bbox(settings):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if "nominatim" in str(request.url):
            return httpx.Response(200, json=[{"boundingbox": ["57.5", "57.9", "11.8", "12.1"]}])
        return httpx.Response(200, json=FIXTURE)

    make_provider(settings, handler).search(SearchCriteria("SE", "law firms", "Göteborg"))
    assert any("nominatim" in u for u in seen)
