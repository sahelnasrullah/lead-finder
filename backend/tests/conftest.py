from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Base, make_engine, make_session_factory
from app.main import create_app, inline_spawn


class FakeSender:
    """Collects emails instead of sending them."""

    def __init__(self, fail_for: set[str] | None = None):
        self.sent: list = []
        self.fail_for = fail_for or set()

    def __call__(self, msg):
        if msg["To"] in self.fail_for:
            raise ConnectionError("boom")
        self.sent.append(msg)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url="sqlite://",
        cache_dir=tmp_path / "cache",
        smtp_host="smtp.example.test",
        smtp_user="ali.nasrullah@zarintech.se",
        smtp_password="secret",
        smtp_from="ali.nasrullah@zarintech.se",
        sender_name="Ali Nasrullah",
        company_name="Zarin Tech AB",
        company_org_number="559999-9999",
        send_delay_min=0,
        send_delay_max=0,
        daily_cap=30,
        enrich_delay_seconds=0,
    )


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def client(settings, session_factory, sender):
    app = create_app(
        settings=settings,
        session_factory=session_factory,
        spawn=inline_spawn,
        sender=sender,
        sleep=lambda s: None,
    )
    with TestClient(app) as c:
        yield c
