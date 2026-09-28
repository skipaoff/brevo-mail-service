import json

import httpx

from mailservice import codes, config, journal, letters, transport
from mailservice.texts import min_word, phrase


def cfg():
    config.settings.cache_clear()
    return config.settings()


def test_plural_minutes():
    assert [min_word(n, "ru") for n in (1, 3, 5, 11, 21, 22, 45)] == [
        "минуту", "минуты", "минут", "минут", "минуту", "минуты", "минут"]
    assert min_word(1, "uk") == "хвилину" and min_word(15, "uk") == "хвилин"
    assert min_word(1, "en") == "minute" and min_word(15, "en") == "minutes"


def test_three_languages_built_in():
    for lang, word in (("ru", "Код"), ("uk", "Код"), ("en", "Code")):
        subject, text, html = letters.code_letter("123456", 15, "a@b.co", lang, cfg())
        assert "123456" in subject and f"{word}: 123456" in text and "123456" in html
    _, text, _ = letters.reset_letter("https://x/r", 45, "a@b.co", "en", cfg())
    assert "valid for 45 minutes" in text


def test_default_language_from_env(monkeypatch):
    monkeypatch.setenv("MAIL_LANG", "uk")
    subject, _, _ = letters.code_letter("123456", 15, "a@b.co", cfg=cfg())
    assert subject.startswith("Код підтвердження")


def test_texts_file_overrides_and_adds_language(tmp_path, monkeypatch):
    path = tmp_path / "texts.json"
    path.write_text(json.dumps({
        "ru": {"code_lead": "Ваш код для входа в {brand}:"},
        "de": {"code_title": "E-Mail bestätigen", "code_label": "Code"},
    }), encoding="utf-8")
    monkeypatch.setenv("TEXTS_FILE", str(path))
    c = cfg()
    _, text, _ = letters.code_letter("123456", 15, "a@b.co", "ru", c)
    assert text.startswith("Ваш код для входа в Тест:")
    _, _, html = letters.code_letter("123456", 15, "a@b.co", "de", c)
    assert "E-Mail bestätigen" in html and "Enter this code" in html  # недостающее — из en


def test_broken_texts_file_does_not_break_letters(tmp_path, monkeypatch):
    path = tmp_path / "texts.json"
    path.write_text("{не json", encoding="utf-8")
    monkeypatch.setenv("TEXTS_FILE", str(path))
    subject, _, _ = letters.code_letter("123456", 15, "a@b.co", "ru", cfg())
    assert subject.startswith("Код подтверждения")


def test_unknown_placeholder_is_kept(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"ru": {"code_lead": "Код для {nope} в {brand}"}}), encoding="utf-8")
    assert phrase("code_lead", "ru", str(path), brand="Acme") == "Код для {nope} в Acme"


def test_brand_falls_back_to_mail_from_and_no_dangling_dash(monkeypatch):
    monkeypatch.setenv("BRAND_NAME", "")
    monkeypatch.setenv("MAIL_FROM", "Acme <noreply@acme.io>")
    assert cfg().brand_name == "Acme"
    monkeypatch.setenv("MAIL_FROM", "noreply@acme.io")
    subject, _, _ = letters.code_letter("123456", 15, "a@b.co", "ru", cfg())
    assert subject == "Код подтверждения 123456"


def test_logo_and_colors(monkeypatch):
    monkeypatch.setenv("LOGO_URL", "https://acme.io/logo.png")
    monkeypatch.setenv("ACCENT_COLOR", "#ff0000")
    _, _, html = letters.reset_letter("https://x/r", 45, "a@b.co", "ru", cfg())
    assert '<img src="https://acme.io/logo.png"' in html and "background:#ff0000" in html


def api_env(monkeypatch, seen):
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-1")

    def fake_post(url, json, timeout, headers):
        seen.append(json)
        return httpx.Response(201, json={"messageId": "<t@brevo>"})

    monkeypatch.setattr(httpx, "post", fake_post)


def test_kind_goes_to_brevo_as_tag(monkeypatch):
    seen = []
    api_env(monkeypatch, seen)
    journal.send("welcome", "a@b.co", "Тема", "Текст", cfg=cfg())
    assert seen[0]["tags"] == ["welcome"]


def test_brevo_template(monkeypatch):
    seen = []
    api_env(monkeypatch, seen)
    posted = journal.send_template("order_ready", "a@b.co", 7, {"order": 15, "link": "https://x/t=S"},
                                   ref="u1", secrets=["https://x/t=S"], cfg=cfg())
    assert posted.ok and seen[0]["templateId"] == 7 and seen[0]["params"]["order"] == 15
    row = journal.events(ref="u1")[0]
    assert row["subject"] == "шаблон Brevo #7" and "t=S" not in row["body"]


def test_template_over_smtp_is_refused(monkeypatch):
    monkeypatch.setenv("MAIL_HOST", "smtp-relay.brevo.com")
    monkeypatch.setenv("MAIL_USER", "u")
    monkeypatch.setenv("MAIL_PASSWORD", "xsmtpsib-1")
    sent = transport.send_template("a@b.co", 7, {}, cfg=cfg())
    assert sent.code == transport.REFUSED and "API" in sent.why


def test_code_via_brevo_template(monkeypatch):
    seen = []
    api_env(monkeypatch, seen)
    monkeypatch.setenv("CODE_TEMPLATE_ID", "12")
    issued = codes.issue("a@b.co", cfg=cfg())
    assert issued.status == journal.SENT
    code = seen[0]["params"]["code"]
    assert seen[0]["templateId"] == 12 and seen[0]["tags"] == ["code_verify"]
    assert code not in journal.events(to="a@b.co")[0]["body"]
    assert codes.check("a@b.co", code) == codes.OK


def test_code_template_needs_api(monkeypatch):
    monkeypatch.setenv("CODE_TEMPLATE_ID", "12")
    monkeypatch.setenv("MAIL_HOST", "smtp-relay.brevo.com")
    monkeypatch.setenv("MAIL_USER", "u")
    monkeypatch.setenv("MAIL_PASSWORD", "xsmtpsib-1")
    assert any("CODE_TEMPLATE_ID" in p for p in cfg().problems())
