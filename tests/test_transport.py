import httpx

from mailservice import config, transport


def cfg():
    config.settings.cache_clear()
    return config.settings()


def test_not_configured_is_skip_not_error():
    sent = transport.send("a@b.co", "Тема", "Текст", cfg=cfg())
    assert not sent.ok and sent.code == transport.NOT_CONFIGURED


def test_bad_address():
    sent = transport.send("не почта", "Тема", "Текст", cfg=cfg())
    assert sent.code == transport.BAD_ADDRESS


def test_api_preferred_when_both(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-1")
    monkeypatch.setenv("MAIL_HOST", "smtp-relay.brevo.com")
    monkeypatch.setenv("MAIL_USER", "u")
    monkeypatch.setenv("MAIL_PASSWORD", "xsmtpsib-1")
    assert cfg().transport == config.BREVO_API
    monkeypatch.setenv("MAIL_TRANSPORT", "smtp")
    assert cfg().transport == config.SMTP


def test_api_sends_and_returns_message_id(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", " xkeysib-abc \n")
    seen = {}

    def fake_post(url, json, timeout, headers):
        seen.update(url=url, json=json, headers=headers)
        return httpx.Response(201, json={"messageId": "<42@smtp-relay.mailin.fr>"})

    monkeypatch.setattr(httpx, "post", fake_post)
    sent = transport.send("Person@Example.com", "Тема", "Текст", "<b>html</b>", cfg=cfg())
    assert sent.ok and sent.message_id == "<42@smtp-relay.mailin.fr>"
    assert seen["headers"]["api-key"] == "xkeysib-abc"  # пробелы срезаны
    assert seen["json"]["sender"] == {"name": "Тест", "email": "noreply@example.com"}
    assert seen["json"]["replyTo"] == {"email": "support@example.com"}
    assert seen["json"]["htmlContent"] == "<b>html</b>"


def test_api_401_names_wrong_key_kind(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "xsmtpsib-oops")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(
        401, json={"code": "unauthorized", "message": "Key not found"}))
    sent = transport.send("a@b.co", "Тема", "Текст", cfg=cfg())
    assert not sent.ok and sent.code == transport.REFUSED
    assert "SMTP-ключ" in sent.why


def test_api_network_error_does_not_raise(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-1")

    def boom(*a, **k):
        raise httpx.ConnectError("нет сети")

    monkeypatch.setattr(httpx, "post", boom)
    sent = transport.send("a@b.co", "Тема", "Текст", cfg=cfg())
    assert not sent.ok and sent.code == transport.REFUSED


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context):
        pass

    def login(self, user, password):
        if password == "bad":
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"auth failed")

    def send_message(self, message):
        FakeSMTP.sent.append(message)


def smtp_env(monkeypatch, password="xsmtpsib-1"):
    monkeypatch.setenv("MAIL_HOST", "smtp-relay.brevo.com")
    monkeypatch.setenv("MAIL_USER", "login@brevo")
    monkeypatch.setenv("MAIL_PASSWORD", password)
    monkeypatch.setattr(transport.smtplib, "SMTP", FakeSMTP)


def test_smtp_builds_full_message(monkeypatch):
    smtp_env(monkeypatch)
    FakeSMTP.sent.clear()
    sent = transport.send("a@b.co", "Тема", "Текст", "<p>html</p>", cfg=cfg())
    assert sent.ok and sent.message_id.endswith("@example.com>")
    msg = FakeSMTP.sent[0]
    assert msg["Reply-To"] == "support@example.com"
    assert msg["Message-ID"] == sent.message_id
    assert msg["X-Mailin-Tag"] is None  # без вида письма метки нет
    assert msg.get_body(("html",)).get_content().strip() == "<p>html</p>"


def test_smtp_auth_error_hints_at_key(monkeypatch):
    smtp_env(monkeypatch, password="bad")
    sent = transport.send("a@b.co", "Тема", "Текст", cfg=cfg())
    assert sent.code == transport.REFUSED and "535" in sent.why


def test_smtp_sends_tag(monkeypatch):
    smtp_env(monkeypatch)
    FakeSMTP.sent.clear()
    transport.send("a@b.co", "Тема", "Текст", cfg=cfg(), tag="password_reset")
    assert FakeSMTP.sent[0]["X-Mailin-Tag"] == "password_reset"
