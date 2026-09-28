# -*- coding: utf-8 -*-
"""Своя база сервиса: журнал писем и коды подтверждения.

Сервис не лезет в базу продукта и не знает его таблиц. Пользователь продукта
для него — строка `ref` (id в вашей системе), без внешних ключей: письмо о
смене пароля может уйти на адрес, за которым аккаунта нет вовсе.

По умолчанию SQLite в файле, для прода — Postgres через DATABASE_URL.
Таблицы создаются при старте. Всё время — UTC без часового пояса, одинаково
в обеих базах.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy import (Column, DateTime, Engine, Index, Integer, MetaData, String,
                        Table, Text, create_engine)

metadata = MetaData()

# Журнал писем: кому, какое письмо, когда и чем кончилось. Только
# дописывается — строку о письме не правят и не удаляют.
# HTML не храним: тяжёлый, нечитаемый и для ответа «мне не пришло» не нужен.
# Секреты (коды, ссылки с ключом) в body и subject заменены на ••••••.
mail_events = Table(
    "mail_events", metadata,
    Column("id", String(32), primary_key=True),
    Column("ref", String(64), nullable=True),
    Column("kind", String(40), nullable=False),
    Column("to_email", String(255), nullable=False),
    Column("subject", Text, nullable=False),
    Column("body", Text, nullable=False),
    # sent — ушло, failed — транспорт отказал, skipped — почта не настроена.
    # Третье не поломка, а местный запуск, и путать их в отчёте нельзя.
    Column("status", String(20), nullable=False),
    Column("detail", Text, nullable=False, default=""),
    # Идентификатор письма у Brevo: по нему наше «отправили» сверяется с их
    # «доставлено / открыто / отскочило».
    Column("provider_id", String(255), nullable=False, default=""),
    Column("at", DateTime, nullable=False),
    Index("ix_mail_events_ref_at", "ref", "at"),
    Index("ix_mail_events_to_at", "to_email", "at"),
)

# Коды подтверждения — состояние, а не история: у кода есть жизненный цикл.
# Сам код не хранится, только его подпись (как пароль).
email_codes = Table(
    "email_codes", metadata,
    Column("id", String(32), primary_key=True),
    Column("email", String(255), nullable=False),
    Column("purpose", String(40), nullable=False),
    Column("ref", String(64), nullable=True),
    Column("ip", String(64), nullable=False, default=""),
    Column("code_hash", String(64), nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    # used_at — код погашен (подтвердили или выдали новый),
    # confirmed_at — именно подтвердили. Разница и есть точка отвала
    # «запросил код и не дошёл».
    Column("used_at", DateTime, nullable=True),
    Column("confirmed_at", DateTime, nullable=True),
    Index("ix_email_codes_email", "email", "purpose", "created_at"),
    Index("ix_email_codes_ip", "ip", "created_at"),
)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ago(minutes: int) -> datetime:
    return now() - timedelta(minutes=minutes)


def new_id() -> str:
    return uuid.uuid4().hex


@lru_cache
def engine(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(url, future=True, pool_pre_ping=True)
    metadata.create_all(eng)
    return eng
