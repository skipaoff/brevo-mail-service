# -*- coding: utf-8 -*-
"""Одна дверь наружу для всей почты: письмо ушло — и строка в журнале о том,
чем это кончилось.

Зачем. Человек говорит «мне не пришло», а за этим три разные беды: не ушло,
ушло и не дошло, дошло и не прочитали. Журнал отвечает на первую и отделяет
её от остальных; вторую и третью показывает Brevo по provider_id.

Правила:
1. Письмо важнее записи о нём: упала база — письмо всё равно уходит.
2. Секреты маскирует вызывающий, называя их явно. Угадывать ключи в тексте
   регулярным выражением — сегодня поймает ссылку, завтра пропустит код.
"""
from __future__ import annotations

import json
import logging
from typing import NamedTuple

from sqlalchemy import select

from . import transport
from .config import Settings, settings
from .store import engine, mail_events, new_id, now

log = logging.getLogger("mailservice.journal")

MASK = "••••••"

SENT = "sent"
FAILED = "failed"
SKIPPED = "skipped"

# Код транспорта → статус журнала. Ровно одно место перевода: исход знает
# тот, кто отправлял, второй раз его не вычисляют.
_STATUS = {transport.OK: SENT, transport.NOT_CONFIGURED: SKIPPED}


class Posted(NamedTuple):
    ok: bool
    why: str
    status: str
    event_id: str
    message_id: str


def mask(value: str, secrets: tuple[str, ...] | list[str]) -> str:
    out = value or ""
    for secret in secrets or ():
        secret = (secret or "").strip()
        if secret:
            out = out.replace(secret, MASK)
    return out


def _write(cfg: Settings, row: dict) -> None:
    try:
        with engine(cfg.database_url).begin() as conn:
            conn.execute(mail_events.insert().values(**row))
    except Exception:  # noqa: BLE001 — журнал не имеет права уронить отправку
        log.exception("письмо обработано, а запись в журнал не легла: вид %s", row.get("kind"))


def _post(cfg: Settings, sent: transport.Sent, kind: str, to: str, subject: str,
          text: str, ref: str, secrets) -> Posted:
    status = _STATUS.get(sent.code, FAILED)
    event_id = new_id()
    _write(cfg, {
        "id": event_id,
        "ref": (ref or None),
        "kind": kind,
        "to_email": (to or "").strip().lower()[:255],
        "subject": mask(subject, secrets),
        "body": mask(text, secrets),
        "status": status,
        "detail": "" if sent.ok else sent.why,
        "provider_id": sent.message_id[:255],
        "at": now(),
    })
    return Posted(sent.ok, sent.why, status, event_id, sent.message_id)


def send(kind: str, to: str, subject: str, text: str, html: str = "",
         ref: str = "", secrets: tuple[str, ...] | list[str] = (),
         cfg: Settings | None = None) -> Posted:
    """Отправить письмо и записать, чем кончилось."""
    cfg = cfg or settings()
    kind = (kind or "custom")[:40]
    sent = transport.send(to, subject, text, html, cfg, tag=kind)
    return _post(cfg, sent, kind, to, subject, text, ref, secrets)


def send_template(kind: str, to: str, template_id: int, params: dict,
                  ref: str = "", secrets: tuple[str, ...] | list[str] = (),
                  cfg: Settings | None = None) -> Posted:
    """Письмо по шаблону Brevo. Текста у нас нет, его собирает Brevo, поэтому
    в журнал ложатся номер шаблона и параметры (секреты замаскированы)."""
    cfg = cfg or settings()
    kind = (kind or "template")[:40]
    sent = transport.send_template(to, template_id, params, cfg, tag=kind)
    body = json.dumps(params or {}, ensure_ascii=False, default=str)
    return _post(cfg, sent, kind, to, f"шаблон Brevo #{template_id}", body, ref, secrets)


def events(to: str = "", ref: str = "", kind: str = "", limit: int = 50,
           cfg: Settings | None = None) -> list[dict]:
    """Журнал сверху вниз: «покажи всё про этого человека»."""
    cfg = cfg or settings()
    q = select(mail_events).order_by(mail_events.c.at.desc()).limit(max(1, min(limit, 500)))
    if to:
        q = q.where(mail_events.c.to_email == to.strip().lower())
    if ref:
        q = q.where(mail_events.c.ref == ref)
    if kind:
        q = q.where(mail_events.c.kind == kind)
    with engine(cfg.database_url).connect() as conn:
        rows = conn.execute(q).mappings().all()
    return [{**dict(r), "at": r["at"].isoformat() + "Z"} for r in rows]
