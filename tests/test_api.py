import re

import pytest
from fastapi.testclient import TestClient

from mailservice import config
from mailservice.api import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_open(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["transport"] == "not_configured"


def test_auth_required(client):
    assert client.post("/v1/send", json={}).status_code == 401
    r = client.post("/v1/send", headers={"Authorization": "Bearer wrong"},
                    json={"to": "a@b.co", "subject": "s", "text": "t"})
    assert r.status_code == 401


def test_refuses_to_start_without_secrets(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "")
    config.settings.cache_clear()
    with pytest.raises(RuntimeError, match="SERVICE_TOKEN"):
        with TestClient(app):
            pass


def test_send_raw_masks_secrets(client, outbox):
    r = client.post("/v1/send", headers=AUTH, json={
        "to": "a@b.co", "subject": "Вход", "text": "ссылка https://x/y?t=SECRET",
        "kind": "magic_link", "ref": "user-1", "secrets": ["https://x/y?t=SECRET"]})
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["status"] == "sent"
    assert body["message_id"] == "<id1@brevo>"
    events = client.get("/v1/events", headers=AUTH, params={"ref": "user-1"}).json()["events"]
    assert "SECRET" not in events[0]["body"] and events[0]["kind"] == "magic_link"


def test_reset_letter(client, outbox):
    link = "https://example.com/reset?t=abc123"
    r = client.post("/v1/send/reset", headers=AUTH, json={"to": "a@b.co", "link": link})
    assert r.json()["status"] == "sent"
    assert link in outbox[0]["text"] and link in outbox[0]["html"]
    ev = client.get("/v1/events", headers=AUTH, params={"to": "A@B.co"}).json()["events"][0]
    assert "abc123" not in ev["body"]


def test_notice_escapes_html(client, outbox):
    r = client.post("/v1/send/notice", headers=AUTH, json={
        "to": "a@b.co", "kind": "plan_paid", "title": "Тариф <script>",
        "paragraphs": ["Оплата прошла.", "Подписка до 4 октября."],
        "why": "вы оплатили подписку.", "button_text": "Открыть кабинет",
        "button_url": "https://example.com/app"})
    assert r.json()["ok"]
    assert "<script>" not in outbox[0]["html"] and "&lt;script&gt;" in outbox[0]["html"]
    assert "Открыть кабинет: https://example.com/app" in outbox[0]["text"]


def test_code_flow_over_http(client, outbox):
    r = client.post("/v1/codes/send", headers=AUTH, json={"email": "a@b.co", "ip": "1.2.3.4"})
    assert r.status_code == 200 and r.json()["status"] == "sent"
    assert "code" not in r.json()  # бэкенд кода не видит
    code = re.search(r"Код: (\d+)", outbox[0]["text"]).group(1)
    ok = client.post("/v1/codes/check", headers=AUTH, json={"email": "a@b.co", "code": code})
    assert ok.json() == {"result": "ok"}


def test_code_rate_limit_is_429(client, outbox):
    for _ in range(3):
        client.post("/v1/codes/send", headers=AUTH, json={"email": "a@b.co"})
    r = client.post("/v1/codes/send", headers=AUTH, json={"email": "a@b.co"})
    assert r.status_code == 429 and r.json()["status"] == "rate_limited"


def test_template_endpoint_without_mail_is_skipped(client):
    r = client.post("/v1/send/template", headers=AUTH, json={
        "to": "a@b.co", "template_id": 3, "params": {"name": "Аня"}, "kind": "welcome"})
    assert r.status_code == 200 and r.json()["status"] == "skipped"


def test_code_language_per_request(client, outbox):
    client.post("/v1/codes/send", headers=AUTH, json={"email": "a@b.co", "lang": "en"})
    assert outbox[0]["subject"].startswith("Your verification code")
    assert outbox[0]["tag"] == "code_verify"
