import random

from app import orgnr
from app.countries import get_country
from app.leadutil import LeadData, dedupe_batch, domain_of, merge_into, name_key, upsert_lead
from app.models import Lead
from app.niches import find_niche, random_niche, sanitize_keyword


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_orgnr_validation():
    assert orgnr.luhn_ok("5560160680")
    assert not orgnr.luhn_ok("5560160681")
    assert orgnr.normalize("556016-0680") == "5560160680"
    assert orgnr.normalize("165560160680") == "5560160680"
    assert orgnr.is_personal_number("5560160680") is False  # legal entity
    assert orgnr.is_personal_number("8112289874") is True  # personnummer-style (sole trader)
    assert orgnr.is_personal_number("1234567890") is None  # invalid


def test_orgnr_find_in_text():
    assert orgnr.find_in_text("Vi är Ericsson AB. Org.nr: 556016-0680, Stockholm") == "5560160680"
    assert orgnr.find_in_text("Org nr 111111-1111") == ""  # fails Luhn
    assert orgnr.find_in_text("Telefon 031-123456") == ""


def test_find_niche_aliases_and_fallback():
    assert find_niche("Law firms").key == "law_firms"
    assert find_niche("advokatbyrå").key == "law_firms"
    assert find_niche("redovisning") is not None
    assert find_niche("underwater basket weaving") is None


def test_random_niche_is_from_list():
    seen = {random_niche(random.Random(i)).key for i in range(40)}
    assert "law_firms" in seen or len(seen) > 3
    assert all(find_niche(k) for k in seen)


def test_sanitize_keyword_removes_query_breaking_chars():
    assert sanitize_keyword('law"; out;') == "law out"


def test_country_config():
    assert get_country("se").language == "sv"
    assert "bolagsverket" in get_country("SE").enrichers
    other = get_country("nl")  # any ISO code works
    assert other.code == "NL" and other.providers == ("osm",)


def test_domain_and_name_key():
    assert domain_of("https://www.Example.se/kontakt") == "example.se"
    assert name_key("Åberg Advokatbyrå AB", "Göteborg") == name_key("aberg advokatbyra", "goteborg")


def test_upsert_merges_duplicates(db):
    a, created = upsert_lead(db, LeadData(name="Berg Juristbyrå", city="Malmö", website="berg.example"))
    assert created
    b, created = upsert_lead(db, LeadData(name="Other name", website="https://www.berg.example/", email="Info@Berg.example"))
    assert not created and a.id == b.id
    assert b.email == "info@berg.example" and b.email_status == "found"
    c, created = upsert_lead(db, LeadData(name="Berg Juristbyrå AB", city="Malmö"))
    assert not created and c.id == a.id
    assert db.query(Lead).count() == 1


def test_merge_does_not_overwrite_without_flag(db):
    lead, _ = upsert_lead(db, LeadData(name="X", website="x.example", phone="1"))
    merge_into(lead, LeadData(name="X", phone="2"))
    assert lead.phone == "1"
    merge_into(lead, LeadData(name="X", phone="2"), overwrite=True)
    assert lead.phone == "2"


def test_sole_trader_flag_from_personal_org_number(db):
    lead, _ = upsert_lead(db, LeadData(name="Anna Svensson Juristbyrå", org_number="811228-9874"))
    assert lead.is_sole_trader is True
    lead2, _ = upsert_lead(db, LeadData(name="Ericsson", org_number="556016-0680"))
    assert lead2.is_sole_trader is False


def test_dedupe_batch_merges_same_company():
    items = [
        LeadData(name="Nilsson & Co AB", city="Göteborg", website="https://nilsson.example", phone=""),
        LeadData(name="Nilsson & Co", city="Göteborg", phone="123"),
        LeadData(name="Berg", website="https://berg.example"),
    ]
    out = dedupe_batch(items)
    assert len(out) == 2
    assert out[0].phone == "123"
