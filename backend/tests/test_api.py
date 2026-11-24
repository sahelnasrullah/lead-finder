import io

from openpyxl import load_workbook

from app.leadutil import LeadData, upsert_lead


def add(db, name, email="", website="", city="Göteborg"):
    lead, _ = upsert_lead(db, LeadData(name=name, email=email, website=website, city=city))
    db.commit()
    return lead.id


def test_meta(client):
    data = client.get("/api/meta").json()
    assert {c["code"] for c in data["countries"]} >= {"SE", "NO"}
    assert any(n["key"] == "law_firms" for n in data["niches"])
    assert data["smtp_configured"] is True and data["missing_for_sending"] == []
    assert "company" in data["placeholders"]


def test_random_niche(client):
    data = client.get("/api/niches/random").json()
    assert data["key"] and data["label"]


def test_lead_crud_and_filters(client, db):
    a = add(db, "Alpha AB", "info@alpha.example", "alpha.example")
    add(db, "Beta AB")
    assert len(client.get("/api/leads").json()) == 2
    assert [l["name"] for l in client.get("/api/leads", params={"has_email": "true"}).json()] == ["Alpha AB"]
    assert len(client.get("/api/leads", params={"q": "beta"}).json()) == 1

    r = client.patch(f"/api/leads/{a}", json={"notes": "Called", "status": "interested", "linkedin_contacted": True})
    assert r.status_code == 200 and r.json()["status"] == "interested" and r.json()["linkedin_contacted"] is True
    assert client.patch(f"/api/leads/{a}", json={"email": "bad"}).status_code == 422
    assert client.patch(f"/api/leads/{a}", json={"status": "bogus"}).status_code == 422
    assert client.patch("/api/leads/999", json={"notes": "x"}).status_code == 404
    assert client.delete(f"/api/leads/{a}").status_code == 204
    assert len(client.get("/api/leads").json()) == 1


def test_export_and_import_via_api(client, db):
    add(db, "Alpha AB", "info@alpha.example", "alpha.example")
    r = client.get("/api/leads/export")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb["Leads"]["A2"].value == "Alpha AB"

    imported = client.post("/api/leads/import", files={"file": ("leads.xlsx", r.content)})
    assert imported.json()["updated"] == 1 and imported.json()["created"] == 0
    bad = client.post("/api/leads/import", files={"file": ("x.xlsx", b"junk")})
    assert bad.status_code == 400


def test_search_endpoint_runs_job_with_injected_provider(settings, session_factory, sender):
    import json
    from pathlib import Path

    import httpx
    from fastapi.testclient import TestClient

    from app.main import create_app, inline_spawn
    from app.providers.osm import OsmProvider

    fixture = json.loads((Path(__file__).parent / "fixtures" / "overpass_lawyers.json").read_text())
    osm = OsmProvider(
        settings,
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=fixture))),
        endpoints=["https://x.test"],
    )

    class NoWeb:  # no network in tests
        def get(self, url):
            return None

    app = create_app(settings=settings, session_factory=session_factory, spawn=inline_spawn, osm=osm,
                     fetcher=NoWeb(), sender=sender, sleep=lambda s: None)
    with TestClient(app) as c:
        r = c.post("/api/searches", json={"country": "se", "niche": "law firms", "limit": 10})
        assert r.status_code == 202
        sid = r.json()["id"]
        done = c.get(f"/api/searches/{sid}").json()
        assert done["status"] == "done" and done["found_count"] == 2
        assert len(c.get("/api/leads", params={"search_id": sid}).json()) == 2
        assert c.post("/api/searches", json={"niche": "x"}).status_code == 422  # too short
        assert c.post("/api/searches", json={"niche": "law firms", "country": "SWE"}).status_code == 422


def test_campaign_flow_preview_create_dry_run_send(client, db, sender):
    ids = [add(db, f"Firm {i}", f"info@firm{i}.example", f"firm{i}.example", city=f"C{i}") for i in range(3)]
    ids.append(add(db, "No Mail AB"))
    templ = next(t for t in client.get("/api/templates").json() if t["key"] == "interview_request" and t["language"] == "sv")
    payload = {"name": "Interviews", "subject": templ["subject"], "body": templ["body"], "language": "sv", "lead_ids": ids}

    preview = client.post("/api/campaigns/preview", json=payload).json()
    assert [p["status"] for p in preview].count("pending") == 3
    assert "Firm 0" in next(p for p in preview if p["company"] == "Firm 0")["subject"]
    assert client.post("/api/campaigns/preview", json={**payload, "subject": "{{bogus}}"}).status_code == 400

    created = client.post("/api/campaigns", json=payload)
    assert created.status_code == 201
    cid = created.json()["id"]
    assert created.json()["counts"] == {"pending": 3, "sent": 0, "failed": 0, "skipped": 1}

    dry = client.post(f"/api/campaigns/{cid}/send", json={"dry_run": True}).json()
    assert dry["dry_run"] and dry["count"] == 3 and not sender.sent

    assert client.post(f"/api/campaigns/{cid}/send", json={}).json() == {"started": True}
    detail = client.get(f"/api/campaigns/{cid}").json()
    assert detail["counts"]["sent"] == 3 and detail["status"] == "done" and len(sender.sent) == 3
    assert detail["company_names"]

    client.post(f"/api/campaigns/{cid}/send", json={})  # second run
    assert len(sender.sent) == 3


def test_campaign_requires_selected_leads(client):
    r = client.post("/api/campaigns", json={"name": "x", "subject": "Hi", "body": "Hello", "language": "sv", "lead_ids": []})
    assert r.status_code == 400


def test_send_blocked_without_org_number(client, db, settings):
    lead = add(db, "A", "info@a.example", "a.example")
    cid = client.post("/api/campaigns", json={"name": "x", "subject": "Hi {{company}}", "body": "Hello", "language": "sv", "lead_ids": [lead]}).json()["id"]
    settings.company_org_number = ""
    r = client.post(f"/api/campaigns/{cid}/send", json={})
    assert r.status_code == 400 and "COMPANY_ORG_NUMBER" in r.json()["detail"]


def test_do_not_contact_endpoints(client):
    r = client.post("/api/do-not-contact", json={"email": "Info@C.example", "reason": "asked"})
    assert r.status_code == 201 and r.json()["email"] == "info@c.example"
    assert client.post("/api/do-not-contact", json={"domain": "https://www.d.example/"}).json()["domain"] == "d.example"
    assert client.post("/api/do-not-contact", json={}).status_code == 422
    assert client.post("/api/do-not-contact", json={"email": "bad"}).status_code == 422
    rows = client.get("/api/do-not-contact").json()
    assert len(rows) == 2
    assert client.delete(f"/api/do-not-contact/{rows[0]['id']}").status_code == 204
    assert client.delete("/api/do-not-contact/9999").status_code == 404


def test_restart_recovery_pauses_stuck_campaigns(settings, session_factory, db):
    from app.main import create_app
    from app.models import Campaign

    c = Campaign(name="x", subject="s", body="b", status="sending")
    db.add(c)
    db.commit()
    create_app(settings=settings, session_factory=session_factory)
    db.expire_all()
    assert db.get(Campaign, c.id).status == "paused"
