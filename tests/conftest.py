import pytest

from mailservice import config, transport

BASE_ENV = {
    "SERVICE_TOKEN": "test-token",
    "CODE_SECRET": "test-secret",
    "MAIL_FROM": "Тест <noreply@example.com>",
    "MAIL_REPLY_TO": "support@example.com",
    "BRAND_NAME": "Тест",
    "SITE_URL": "https://example.com",
    "MAIL_TRANSPORT": "", "BREVO_API_KEY": "", "MAIL_HOST": "", "MAIL_USER": "",
    "MAIL_PASSWORD": "",
}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/mail.db")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()


@pytest.fixture
def outbox(monkeypatch):
    """Подменяет транспорт: письма копятся в списке и считаются ушедшими."""
    letters = []

    def fake_send(to, subject, text, html="", cfg=None, tag=""):
        letters.append({"to": to, "subject": subject, "text": text, "html": html, "tag": tag})
        return transport.Sent(True, "отправлено", transport.OK, f"<id{len(letters)}@brevo>")

    monkeypatch.setattr(transport, "send", fake_send)
    return letters
