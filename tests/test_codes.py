import re

from mailservice import codes, config, journal, store
from mailservice.store import email_codes, engine
from sqlalchemy import update


def last_code(outbox):
    return re.search(r"Код: (\d+)", outbox[-1]["text"]).group(1)


def test_issue_and_confirm(outbox):
    issued = codes.issue("Person@Example.com", ip="1.1.1.1")
    assert issued.status == journal.SENT
    code = last_code(outbox)
    assert code in outbox[-1]["subject"]  # код виден в уведомлении на телефоне
    assert codes.check("person@example.com", "000000" if code != "000000" else "111111") == codes.WRONG
    assert codes.check(" PERSON@example.com ", code) == codes.OK
    assert codes.check("person@example.com", code) == codes.NONE  # одноразовый


def test_code_is_masked_in_journal(outbox):
    codes.issue("a@b.co")
    code = last_code(outbox)
    row = journal.events(to="a@b.co")[0]
    assert code not in row["body"] and code not in row["subject"]
    assert journal.MASK in row["body"]
    assert row["provider_id"] == "<id1@brevo>"


def test_new_code_kills_old(outbox):
    codes.issue("a@b.co")
    old = last_code(outbox)
    codes.issue("a@b.co")
    new = last_code(outbox)
    if old != new:
        assert codes.check("a@b.co", old) == codes.WRONG
    assert codes.check("a@b.co", new) == codes.OK


def test_attempts_burn(outbox):
    codes.issue("a@b.co")
    code = last_code(outbox)
    wrong = "0" * 6 if code != "0" * 6 else "1" * 6
    for _ in range(5):
        assert codes.check("a@b.co", wrong) == codes.WRONG
    assert codes.check("a@b.co", code) == codes.BURNED


def test_expired(outbox):
    codes.issue("a@b.co")
    code = last_code(outbox)
    cfg = config.settings()
    with engine(cfg.database_url).begin() as conn:
        conn.execute(update(email_codes).values(expires_at=store.ago(1)))
    assert codes.check("a@b.co", code) == codes.EXPIRED


def test_rate_limit_per_email(outbox):
    for _ in range(3):
        assert codes.issue("a@b.co").status == journal.SENT
    limited = codes.issue("a@b.co")
    assert limited.status == codes.RATE_LIMITED
    assert len(outbox) == 3  # четвёртое письмо не ушло


def test_rate_limit_per_ip(outbox, monkeypatch):
    monkeypatch.setenv("CODES_PER_IP_HOUR", "2")
    config.settings.cache_clear()
    assert codes.issue("a@b.co", ip="9.9.9.9").status == journal.SENT
    assert codes.issue("b@b.co", ip="9.9.9.9").status == journal.SENT
    assert codes.issue("c@b.co", ip="9.9.9.9").status == codes.RATE_LIMITED


def test_purposes_are_separate(outbox):
    codes.issue("a@b.co", purpose="verify")
    code = last_code(outbox)
    assert codes.check("a@b.co", code, purpose="login") == codes.NONE
    assert codes.check("a@b.co", code, purpose="verify") == codes.OK


def test_code_not_stored_in_clear(outbox):
    codes.issue("a@b.co")
    code = last_code(outbox)
    cfg = config.settings()
    with engine(cfg.database_url).connect() as conn:
        row = conn.execute(email_codes.select()).mappings().first()
    assert code not in row["code_hash"]


def test_mail_down_still_answers(monkeypatch):
    # Почта не настроена: код выдан, письмо в журнале как skipped, без исключений.
    issued = codes.issue("a@b.co")
    assert issued.status == journal.SKIPPED
    assert journal.events(to="a@b.co")[0]["status"] == journal.SKIPPED
