import json
from pathlib import Path

import httpx

from app.enrichment import PoliteFetcher
from app.models import Lead, Search
from app.pipeline import run_search
from app.providers.bolagsverket import BolagsverketClient, OrgInfo, parse_organisation
from app.providers.osm import OsmProvider

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "overpass_lawyers.json").read_text())

HOME = '<html><body><a href="mailto:info@nilssonco.example">Mail</a>Org.nr 556016-0680</body></html>'


def osm_provider(settings, payload=FIXTURE, status=200):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload)))
    return OsmProvider(settings, client=client, sleep=lambda s: None, endpoints=["https://overpass.test"])


def web_fetcher():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=HOME, headers={"content-type": "text/html"})

    return PoliteFetcher("T", delay=0, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)


def new_search(db, **kw):
    s = Search(country="SE", niche="law firms", city="", result_limit=10, **kw)
    db.add(s)
    db.commit()
    return s


def test_run_search_saves_deduped_enriched_leads(settings, session_factory, db):
    search = new_search(db)
    run_search(session_factory, search.id, settings, osm=osm_provider(settings), fetcher=web_fetcher(),
               bolagsverket=BolagsverketClient(settings))
    db.expire_all()
    search = db.get(Search, search.id)
    assert search.status == "done" and search.found_count == 2  # the two Nilsson entries were merged
    nilsson = db.query(Lead).filter(Lead.name.like("Nilsson%")).one()
    assert nilsson.email == "info@nilssonco.example" and nilsson.email_status == "found"
    assert nilsson.org_number == "556016-0680" and nilsson.is_sole_trader is False
    assert nilsson.phone.startswith("+46 31")
    assert "Bolagsverket skipped" in search.message  # no credentials configured


def test_run_search_respects_limit(settings, session_factory, db):
    search = new_search(db)
    search.result_limit = 1
    db.commit()
    run_search(session_factory, search.id, settings, osm=osm_provider(settings), fetcher=web_fetcher())
    db.expire_all()
    assert db.query(Lead).count() == 1


def test_run_search_failure_is_recorded(settings, session_factory, db):
    search = new_search(db)
    run_search(session_factory, search.id, settings, osm=osm_provider(settings, status=504))
    db.expire_all()
    s = db.get(Search, search.id)
    assert s.status == "failed" and "failed" in s.message.lower()


def test_rerunning_search_does_not_duplicate_leads(settings, session_factory, db):
    for _ in range(2):
        s = new_search(db)
        run_search(session_factory, s.id, settings, osm=osm_provider(settings), fetcher=web_fetcher())
    db.expire_all()
    assert db.query(Lead).count() == 2


# ---- Bolagsverket -------------------------------------------------------------
ORG_RESPONSE = {
    "organisationer": [{
        "organisationsidentitet": {"identitetsbeteckning": "5560160680"},
        "organisationsnamn": {"organisationsnamnLista": [{"namn": "Nilsson & Co Advokatbyrå AB"}]},
        "juridiskForm": {"kod": "49", "klartext": "Aktiebolag"},
        "postadressOrganisation": {"postadress": {"utdelningsadress": "Kungsgatan 1", "postnummer": "41119", "postort": "GÖTEBORG"}},
        "naringsgrenOrganisation": {"sni": [{"kod": "69101", "klartext": "Advokatbyråer"}]},
    }]
}


def test_parse_organisation():
    info = parse_organisation(ORG_RESPONSE, "5560160680")
    assert info.legal_form == "Aktiebolag" and info.sni == ["69101"] and info.city == "GÖTEBORG"
    assert not info.is_sole_trader
    assert OrgInfo("1", legal_form="Enskild näringsidkare", legal_form_code="10").is_sole_trader
    assert parse_organisation({}, "1") is None


def test_bolagsverket_client_token_and_lookup(settings):
    settings.bolagsverket_client_id, settings.bolagsverket_client_secret = "id", "secret"
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer tok"
        assert json.loads(request.content) == {"identitetsbeteckning": "5560160680"}
        return httpx.Response(200, json=ORG_RESPONSE)

    client = BolagsverketClient(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.available
    assert client.lookup("556016-0680").legal_form == "Aktiebolag"
    client.lookup("556016-0680")
    assert calls.count("/oauth2/token") == 1  # token is reused
    assert client.lookup("not a number") is None


def test_pipeline_uses_bolagsverket_when_configured(settings, session_factory, db):
    settings.bolagsverket_client_id, settings.bolagsverket_client_secret = "id", "secret"

    def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        payload = json.loads(json.dumps(ORG_RESPONSE))
        payload["organisationer"][0]["juridiskForm"] = {"kod": "10", "klartext": "Enskild näringsidkare"}
        return httpx.Response(200, json=payload)

    bv = BolagsverketClient(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    s = new_search(db)
    run_search(session_factory, s.id, settings, osm=osm_provider(settings), fetcher=web_fetcher(), bolagsverket=bv)
    db.expire_all()
    nilsson = db.query(Lead).filter(Lead.name.like("Nilsson%")).one()
    assert nilsson.legal_form == "Enskild näringsidkare" and nilsson.is_sole_trader is True
    assert "SNI 69101" in nilsson.notes


def test_other_country_uses_osm_only_and_stores_country(settings, session_factory, db):
    seen = []

    def handler(request):
        seen.append(request.content.decode())
        return httpx.Response(200, json=FIXTURE)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    osm = OsmProvider(settings, client=client, sleep=lambda s: None, endpoints=["https://overpass.test"])
    s = Search(country="NO", niche="law firms", city="", result_limit=10)
    db.add(s)
    db.commit()
    run_search(session_factory, s.id, settings, osm=osm, fetcher=web_fetcher())
    db.expire_all()
    from urllib.parse import unquote_plus

    assert 'area["ISO3166-1"="NO"]' in unquote_plus(seen[0])
    done = db.get(Search, s.id)
    assert done.status == "done" and "Bolagsverket" not in done.message
    assert {l.country for l in db.query(Lead)} == {"NO"}
