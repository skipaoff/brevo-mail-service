# -*- coding: utf-8 -*-
"""Транспорт: довезти одно письмо до Brevo и честно сказать, чем кончилось.

Ничего не знает ни о базе, ни о текстах писем, ни о кодах. Два способа доставки —
SMTP Brevo и API Brevo — отвечают одинаково, и остальной сервис не знает,
каким из них ушло письмо.

Никогда не бросает исключение: сломанная почта не должна ронять регистрацию.
Человеку лучше увидеть «письмо не ушло, попробуйте ещё раз», чем ошибку сервера.
"""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from typing import NamedTuple

import httpx

from .config import BREVO_API, SMTP, Settings, settings

log = logging.getLogger("mailservice.transport")

BREVO_URL = "https://api.brevo.com/v3/smtp/email"

# Чем кончилась отправка. Код — для решения, слова — для лога и человека.
# «Не настроена» и «отказ» — две разные беды с разным лечением, путать нельзя.
OK = "ok"
NOT_CONFIGURED = "not_configured"
BAD_ADDRESS = "bad_address"
REFUSED = "refused"


class Sent(NamedTuple):
    """Ответ транспорта: получилось, почему словами, почему кодом и какой
    идентификатор письму дал Brevo (по нему письмо находится в логах Brevo)."""

    ok: bool
    why: str
    code: str
    message_id: str = ""


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def looks_like_email(value: str) -> bool:
    """Грубая проверка формы адреса. Настоящая проверка — дошло письмо или нет."""
    return bool(_EMAIL_RE.match((value or "").strip()))


def send(to: str, subject: str, text: str, html: str = "",
         cfg: Settings | None = None, tag: str = "") -> Sent:
    """Одно письмо. tag — вид письма; в логах Brevo по нему фильтруются письма."""
    cfg = cfg or settings()
    to = (to or "").strip()
    if not looks_like_email(to):
        return Sent(False, f"адрес не похож на почтовый: {to!r}", BAD_ADDRESS)

    kind = cfg.transport
    if kind == SMTP:
        return _smtp(cfg, to, subject, text, html, tag)
    if kind == BREVO_API:
        payload = {"subject": subject, "textContent": text}
        if html:
            payload["htmlContent"] = html
        return _api(cfg, to, payload, tag)
    return _not_configured(to, subject, text)


def send_template(to: str, template_id: int, params: dict,
                  cfg: Settings | None = None, tag: str = "") -> Sent:
    """Письмо по шаблону, свёрстанному в самом Brevo. Только через API:
    у SMTP шаблонов Brevo нет."""
    cfg = cfg or settings()
    to = (to or "").strip()
    if not looks_like_email(to):
        return Sent(False, f"адрес не похож на почтовый: {to!r}", BAD_ADDRESS)

    kind = cfg.transport
    if kind == BREVO_API:
        return _api(cfg, to, {"templateId": int(template_id), "params": params or {}}, tag)
    if kind == SMTP:
        return Sent(False, "шаблоны Brevo работают только через API: задайте "
                    "BREVO_API_KEY", REFUSED)
    return _not_configured(to, f"шаблон Brevo #{template_id}",
                           f"параметры: {params}")


def _not_configured(to: str, subject: str, text: str) -> Sent:
    # Почта не настроена: письмо целиком в лог, чтобы разработка работала
    # без настоящего ящика.
    log.warning("почта не настроена, письмо не отправлено\n"
                "[mail] кому: %s\n[mail] тема: %s\n%s", to, subject, text)
    return Sent(False, "почта не настроена (нет BREVO_API_KEY или MAIL_HOST/"
                "MAIL_USER/MAIL_PASSWORD)", NOT_CONFIGURED)


def _sender(cfg: Settings) -> tuple[str, str]:
    name, addr = parseaddr(cfg.mail_from)
    return (name or cfg.brand_name), (addr or cfg.smtp_user)


def _smtp(cfg: Settings, to: str, subject: str, text: str, html: str,
          tag: str) -> Sent:
    name, addr = _sender(cfg)
    message = EmailMessage()
    message["From"] = formataddr((name, addr))
    message["To"] = to
    message["Subject"] = subject
    if cfg.reply_to:
        message["Reply-To"] = cfg.reply_to
    # Свой Message-ID, чтобы журнал и лог Brevo сходились не только по времени.
    message_id = make_msgid(domain=addr.rpartition("@")[2] or None)
    message["Message-ID"] = message_id
    if tag:
        message["X-Mailin-Tag"] = tag  # так Brevo принимает метку по SMTP
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    try:
        context = ssl.create_default_context()
        if cfg.smtp_port == 465:
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context,
                                  timeout=20) as smtp:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20) as smtp:
                smtp.starttls(context=context)
                smtp.login(cfg.smtp_user, cfg.smtp_password)
                smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        hint = ("в MAIL_PASSWORD лежит API-ключ, а SMTP нужен SMTP-ключ (xsmtpsib-…)"
                if cfg.smtp_password.startswith("xkeysib-") else
                "проверьте SMTP-ключ и логин; у свежего аккаунта Brevo это же "
                "бывает до активации")
        return Sent(False, f"Brevo не пустил по SMTP (535): {hint}", REFUSED)
    except Exception as exc:  # noqa: BLE001 — причина уходит в ответ, отправитель не падает
        return Sent(False, f"письмо не ушло: {type(exc).__name__}: {exc}", REFUSED)
    return Sent(True, "отправлено", OK, message_id)


def _api(cfg: Settings, to: str, content: dict, tag: str) -> Sent:
    name, addr = _sender(cfg)
    payload: dict = {
        "sender": {"name": name, "email": addr},
        "to": [{"email": to}],
        **content,
    }
    if cfg.reply_to:
        _, reply_addr = parseaddr(cfg.reply_to)
        payload["replyTo"] = {"email": reply_addr or cfg.reply_to}
    if tag:
        payload["tags"] = [tag]

    try:
        resp = httpx.post(BREVO_URL, json=payload, timeout=20, headers={
            "api-key": cfg.brevo_api_key, "accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        return Sent(False, f"Brevo недоступен: {type(exc).__name__}: {exc}", REFUSED)

    if resp.status_code in (200, 201, 202):
        try:
            message_id = str(resp.json().get("messageId") or "")
        except ValueError:
            message_id = ""
        return Sent(True, "отправлено", OK, message_id)

    try:
        reason = resp.json().get("message") or resp.text
    except ValueError:
        reason = resp.text
    if resp.status_code == 401:
        hint = ("в BREVO_API_KEY лежит SMTP-ключ, а API нужен API-ключ (xkeysib-…)"
                if cfg.brevo_api_key.startswith("xsmtpsib-") else
                "ключ не принят; если у ключа включён список IP — добавьте IP сервера")
        return Sent(False, f"Brevo отказал (401): {hint}. {reason}", REFUSED)
    return Sent(False, f"Brevo отказал ({resp.status_code}): {reason}", REFUSED)
