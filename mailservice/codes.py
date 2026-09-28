# -*- coding: utf-8 -*-
"""Коды подтверждения почты: выдать, отправить, проверить.

Правила, каждое оплачено опытом:

- **Код хранится подписью, как пароль.** Украли базу — живые коды по ней не
  восстановить: подпись делается секретом сервиса (CODE_SECRET), которого в
  базе нет, а голый sha256 от шести цифр перебирается мгновенно.
- **Живой код всегда один.** Новый запрос гасит прежний, иначе «прислать ещё
  раз» копит действующие ключи.
- **Частота ограничена** на адрес и на источник запроса. Без этого ручка —
  бесплатная пушка для спама на любые адреса от вашего имени, и репутация
  домена сгорает за день.
- **Проверка отвечает словом**, а не да/нет: человеку надо знать, ошибся он,
  опоздал или исчерпал попытки.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import NamedTuple

from sqlalchemy import and_, func, select, update

from . import journal, letters
from .config import Settings, settings
from .store import ago, email_codes, engine, new_id, now

# Исходы проверки
OK = "ok"              # подтверждено
WRONG = "wrong"        # не тот код, попытка засчитана
EXPIRED = "expired"    # опоздал, нужен новый
BURNED = "burned"      # попытки кончились, нужен новый
NONE = "none"          # живого кода нет, нужен новый

RATE_LIMITED = "rate_limited"


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _sign(cfg: Settings, purpose: str, email: str, code: str) -> str:
    # Адрес и назначение внутри подписи: одинаковый код у двух людей даёт
    # разные подписи, и введённый код не подтвердит чужой адрес.
    msg = f"{purpose}:{email}:{code}".encode()
    return hmac.new(cfg.code_secret.encode(), msg, hashlib.sha256).hexdigest()


class Issued(NamedTuple):
    status: str          # sent | skipped | failed | rate_limited
    why: str
    expires_in_minutes: int
    event_id: str = ""


def _too_many(cfg: Settings, email: str, purpose: str, ip: str) -> str:
    since = ago(60)
    with engine(cfg.database_url).connect() as conn:
        per_email = conn.execute(
            select(func.count()).select_from(email_codes).where(and_(
                email_codes.c.email == email, email_codes.c.purpose == purpose,
                email_codes.c.created_at > since))).scalar_one()
        if per_email >= cfg.codes_per_email_hour:
            return (f"на этот адрес уже отправлено {per_email} кодов за час, "
                    "попробуйте позже")
        if ip:
            per_ip = conn.execute(
                select(func.count()).select_from(email_codes).where(and_(
                    email_codes.c.ip == ip, email_codes.c.created_at > since))).scalar_one()
            if per_ip >= cfg.codes_per_ip_hour:
                return "слишком много запросов кода с этого адреса сети, попробуйте позже"
    return ""


def issue(email: str, purpose: str = "verify", ref: str = "", ip: str = "",
          lang: str = "", cfg: Settings | None = None) -> Issued:
    """Выдать новый код и отправить его письмом. Сам код наружу не отдаётся:
    бэкенд продукта его не видит, видит только человек в почте.

    Письмо — встроенное, либо шаблон Brevo, если задан CODE_TEMPLATE_ID
    (в шаблон приходят params.code, params.minutes, params.brand)."""
    cfg = cfg or settings()
    email, purpose, ip = _norm(email), (purpose or "verify")[:40], (ip or "")[:64]

    limited = _too_many(cfg, email, purpose, ip)
    if limited:
        return Issued(RATE_LIMITED, limited, cfg.code_minutes)

    code = f"{secrets.randbelow(10 ** cfg.code_digits):0{cfg.code_digits}d}"
    with engine(cfg.database_url).begin() as conn:
        conn.execute(update(email_codes).where(and_(
            email_codes.c.email == email, email_codes.c.purpose == purpose,
            email_codes.c.used_at.is_(None))).values(used_at=now()))
        conn.execute(email_codes.insert().values(
            id=new_id(), email=email, purpose=purpose, ref=(ref or None), ip=ip,
            code_hash=_sign(cfg, purpose, email, code), attempts=0,
            created_at=now(), expires_at=ago(-cfg.code_minutes)))

    kind = f"code_{purpose}"
    if cfg.code_template_id:
        posted = journal.send_template(
            kind, email, cfg.code_template_id,
            {"code": code, "minutes": cfg.code_minutes, "brand": cfg.brand_name},
            ref=ref, secrets=(code,), cfg=cfg)
    else:
        subject, text, html = letters.code_letter(code, cfg.code_minutes, email, lang, cfg)
        posted = journal.send(kind, email, subject, text, html,
                              ref=ref, secrets=(code,), cfg=cfg)
    return Issued(posted.status, posted.why, cfg.code_minutes, posted.event_id)


def check(email: str, code: str, purpose: str = "verify",
          cfg: Settings | None = None) -> str:
    cfg = cfg or settings()
    email, purpose = _norm(email), (purpose or "verify")[:40]
    with engine(cfg.database_url).begin() as conn:
        row = conn.execute(
            select(email_codes).where(and_(
                email_codes.c.email == email, email_codes.c.purpose == purpose,
                email_codes.c.used_at.is_(None)))
            .order_by(email_codes.c.created_at.desc()).limit(1)).mappings().first()
        if row is None:
            return NONE
        if int(row["attempts"] or 0) >= cfg.code_max_attempts:
            return BURNED
        if row["expires_at"] <= now():
            return EXPIRED
        typed = "".join(ch for ch in str(code or "") if ch.isdigit())
        if not hmac.compare_digest(row["code_hash"], _sign(cfg, purpose, email, typed)):
            conn.execute(update(email_codes).where(email_codes.c.id == row["id"])
                         .values(attempts=email_codes.c.attempts + 1))
            return WRONG
        stamp = now()
        conn.execute(update(email_codes).where(email_codes.c.id == row["id"])
                     .values(used_at=stamp, confirmed_at=stamp))
    return OK
