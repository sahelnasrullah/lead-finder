import random

import pytest

from app import outreach
from app.leadutil import LeadData, upsert_lead
from app.models import Campaign, DoNotContact, Lead, Message
from app.outreach import OutreachError

SUBJECT = "Hej {{company}}"
BODY = "Hej,\n\nJag heter {{sender_name}} från {{company_name}}. Gäller {{company}} i {{city}}?\n"


def add_lead(db, name, email, website="", city="Göteborg", **kw):
    lead, _ = upsert_lead(db, LeadData(name=name, email=email, city=city, website=website, **kw))
    db.commit()
    return lead


def test_templates_available_and_valid_after_filling():
    keys = {(t["key"], t["language"]) for t in outreach.list_templates()}
    assert keys == {("interview_request", "sv"), ("interview_request", "en"), ("pilot_offer", "sv"), ("pilot_offer", "en")}
    for t in outreach.list_templates():
        if t["key"] == "interview_request":
            outreach.validate_template(t["subject"], t["body"])  # sendable as-is
        else:
            with pytest.raises(OutreachError, match="Replace the placeholder"):
                outreach.validate_template(t["subject"], t["body"])  # must be filled in first


def test_interview_template_is_honest_about_being_a_student():
    sv = next(t for t in outreach.list_templates() if t["key"] == "interview_request" and t["language"] == "sv")
    en = next(t for t in outreach.list_templates() if t["key"] == "interview_request" and t["language"] == "en")
    assert "student" in sv["body"] and "student" in en["body"]
    assert "{{company_name}}" in sv["body"]


def test_unknown_placeholder_rejected():
    with pytest.raises(OutreachError, match="Unknown placeholder"):
        outreach.validate_template("Hi {{nope}}", "Body")
    with pytest.raises(OutreachError):
        outreach.validate_template("", "Body")


def test_render_includes_identity_footer_and_optout(settings, db):
    lead = add_lead(db, "Berg Juristbyrå", "info@berg.example")
    subject, body = outreach.render_message(settings, SUBJECT, BODY, "sv", lead)
    assert subject == "Hej Berg Juristbyrå"
    assert "Gäller Berg Juristbyrå i Göteborg?" in body
    assert "Zarin Tech AB, org.nr 559999-9999" in body and "ali.nasrullah@zarintech.se" in body
    assert "avregistrera" in body
    _, en = outreach.render_message(settings, SUBJECT, BODY, "en", lead)
    assert "unsubscribe" in en and "org. no. 559999-9999" in en


def test_plan_skips_per_rules(settings, db):
    ok = add_lead(db, "A AB", "info@a.example", "https://a.example")
    dup_domain = add_lead(db, "A2 AB", "kontakt@a.example", "https://a2.example", city="Malmö")
    dup_email = add_lead(db, "A3 AB", "info@a.example", "https://other.example", city="Lund")
    no_email = add_lead(db, "B AB", "")
    dnc = add_lead(db, "C AB", "info@c.example")
    dnc_domain = add_lead(db, "D AB", "info@d.example")
    sole = add_lead(db, "E Juristbyrå", "e@e.example", is_sole_trader=True)
    g1 = add_lead(db, "G1", "x@gmail.com")
    g2 = add_lead(db, "G2", "y@gmail.com", city="Umeå")  # free-mail domains are not "the same company"
    db.add_all([DoNotContact(email="info@c.example"), DoNotContact(domain="d.example")])
    db.commit()

    ids = [l.id for l in (ok, dup_domain, dup_email, no_email, dnc, dnc_domain, sole, g1, g2)]
    planned = {p.company: p for p in outreach.plan_messages(db, settings, SUBJECT, BODY, "sv", ids)}
    assert planned["A AB"].status == "pending"
    assert planned["A2 AB"].skip_reason == "duplicate_domain"
    assert planned["A3 AB"].skip_reason == "duplicate_email"
    assert planned["B AB"].skip_reason == "no_email"
    assert planned["C AB"].skip_reason == "do_not_contact"
    assert planned["D AB"].skip_reason == "do_not_contact"
    assert planned["E Juristbyrå"].skip_reason == "sole_trader"
    assert planned["G1"].status == "pending" and planned["G2"].status == "pending"

    allowed = outreach.plan_messages(db, settings, SUBJECT, BODY, "sv", [sole.id], include_sole_traders=True)
    assert allowed[0].status == "pending"


def test_duplicate_email_reason(settings, db):
    a = add_lead(db, "Alpha", "info@same.example", "https://alpha.example")
    b = add_lead(db, "Beta", "info@same.example", "https://beta.example", city="Lund")
    planned = outreach.plan_messages(db, settings, SUBJECT, BODY, "sv", [a.id, b.id])
    assert [p.skip_reason for p in planned] == ["", "duplicate_email"]


def make_campaign(db, settings, leads, **kw):
    return outreach.create_campaign(db, settings, "Test", SUBJECT, BODY, "sv", [l.id for l in leads], **kw)


def test_send_sends_individually_and_second_run_sends_nothing(settings, session_factory, db, sender):
    leads = [add_lead(db, f"Firm {i}", f"info@firm{i}.example", f"https://firm{i}.example", city=f"C{i}") for i in range(3)]
    campaign = make_campaign(db, settings, leads)
    sleeps = []
    counts = outreach.run_send(session_factory, campaign.id, settings, sender=sender, sleep=sleeps.append)
    assert counts == {"sent": 3, "failed": 0, "skipped": 0}
    assert len(sender.sent) == 3
    for msg in sender.sent:
        assert len(msg.get_all("To")) == 1 and not msg["Cc"] and not msg["Bcc"]
        assert msg["From"] == "Ali Nasrullah <ali.nasrullah@zarintech.se>"
        assert "unsubscribe" in msg["List-Unsubscribe"]

    again = outreach.run_send(session_factory, campaign.id, settings, sender=sender, sleep=sleeps.append)
    assert again == {"sent": 0, "failed": 0, "skipped": 0} and len(sender.sent) == 3

    db.expire_all()
    assert db.get(Campaign, campaign.id).status == "done"
    assert {l.status for l in db.query(Lead)} == {"contacted"}


def test_send_is_throttled(settings, session_factory, db, sender):
    settings.send_delay_min = settings.send_delay_max = 30
    leads = [add_lead(db, f"F{i}", f"info@f{i}.example", f"https://f{i}.example", city=f"C{i}") for i in range(3)]
    campaign = make_campaign(db, settings, leads)
    sleeps = []
    outreach.run_send(session_factory, campaign.id, settings, sender=sender, sleep=sleeps.append, rng=random.Random(1))
    assert sum(sleeps) == 60  # a 30s wait between emails, none after the last one


def test_daily_cap_pauses_and_resumes(settings, session_factory, db, sender):
    settings.daily_cap = 2
    leads = [add_lead(db, f"F{i}", f"info@f{i}.example", f"https://f{i}.example", city=f"C{i}") for i in range(3)]
    campaign = make_campaign(db, settings, leads)
    counts = outreach.run_send(session_factory, campaign.id, settings, sender=sender, sleep=lambda s: None)
    assert counts["sent"] == 2
    db.expire_all()
    c = db.get(Campaign, campaign.id)
    assert c.status == "paused" and "Daily cap" in c.note
    settings.daily_cap = 10  # "tomorrow"
    counts = outreach.run_send(session_factory, campaign.id, settings, sender=sender, sleep=lambda s: None)
    assert counts["sent"] == 1 and len(sender.sent) == 3


def test_failed_sends_are_logged_and_retryable(settings, session_factory, db):
    from tests.conftest import FakeSender

    flaky = FakeSender(fail_for={"info@f1.example"})
    leads = [add_lead(db, f"F{i}", f"info@f{i}.example", f"https://f{i}.example", city=f"C{i}") for i in range(2)]
    campaign = make_campaign(db, settings, leads)
    counts = outreach.run_send(session_factory, campaign.id, settings, sender=flaky, sleep=lambda s: None)
    assert counts == {"sent": 1, "failed": 1, "skipped": 0}
    db.expire_all()
    failed = db.query(Message).filter_by(status="failed").one()
    assert "ConnectionError" in failed.error and "secret" not in failed.error
    flaky.fail_for.clear()
    assert outreach.retry_failed(db, campaign.id) == 1
    counts = outreach.run_send(session_factory, campaign.id, settings, sender=flaky, sleep=lambda s: None)
    assert counts["sent"] == 1 and len(flaky.sent) == 2


def test_dnc_added_after_creation_is_honoured_at_send_time(settings, session_factory, db, sender):
    a = add_lead(db, "A", "info@a.example", "https://a.example")
    campaign = make_campaign(db, settings, [a])
    db.add(DoNotContact(email="info@a.example"))
    db.commit()
    counts = outreach.run_send(session_factory, campaign.id, settings, sender=sender, sleep=lambda s: None)
    assert counts == {"sent": 0, "failed": 0, "skipped": 1} and not sender.sent


def test_previously_contacted_skipped_in_new_campaign(settings, session_factory, db, sender):
    a = add_lead(db, "A", "info@a.example", "https://a.example")
    first = make_campaign(db, settings, [a])
    outreach.run_send(session_factory, first.id, settings, sender=sender, sleep=lambda s: None)
    second = make_campaign(db, settings, [a])
    skipped = db.query(Message).filter_by(campaign_id=second.id).one()
    assert skipped.status == "skipped" and skipped.skip_reason == "already_contacted"
    third = make_campaign(db, settings, [a], skip_previously_contacted=False)
    assert db.query(Message).filter_by(campaign_id=third.id).one().status == "pending"


def test_dry_run_changes_nothing(settings, session_factory, db, sender):
    a = add_lead(db, "A", "info@a.example", "https://a.example")
    campaign = make_campaign(db, settings, [a])
    report = outreach.dry_run(db, campaign.id)
    assert report["count"] == 1 and report["would_send"][0]["to"] == "info@a.example"
    assert db.query(Message).filter_by(status="pending").count() == 1 and not sender.sent


def test_cannot_send_without_settings(settings):
    settings.smtp_password = ""
    settings.company_org_number = ""
    with pytest.raises(OutreachError, match="SMTP_PASSWORD.*COMPANY_ORG_NUMBER"):
        outreach.check_ready_to_send(settings)


def test_concurrent_send_of_same_campaign_is_refused(settings, session_factory, db):
    a = add_lead(db, "A", "info@a.example", "https://a.example")
    campaign = make_campaign(db, settings, [a])
    attempts = []

    def reentrant_sender(msg):
        try:
            outreach.run_send(session_factory, campaign.id, settings, sender=lambda m: None, sleep=lambda s: None)
        except OutreachError as exc:
            attempts.append(str(exc))

    outreach.run_send(session_factory, campaign.id, settings, sender=reentrant_sender, sleep=lambda s: None)
    assert attempts == ["This campaign is already sending."]
