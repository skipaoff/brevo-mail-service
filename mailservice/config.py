# -*- coding: utf-8 -*-
"""Настройки сервиса. Всё из окружения, в коде секретов нет.

Почта включается одним из двух способов, код при этом не меняется:

    SMTP Brevo   MAIL_HOST=smtp-relay.brevo.com, MAIL_USER, MAIL_PASSWORD (SMTP-ключ)
    API Brevo    BREVO_API_KEY (API-ключ, начинается с xkeysib-)

Если есть оба, по умолчанию берётся API: он возвращает идентификатор письма,
журнал сходится с журналом Brevo, и работают шаблоны, свёрстанные в самом
Brevo. Выбрать явно — MAIL_TRANSPORT=smtp|brevo_api.

Пока почта не настроена, письма не уходят, а целиком пишутся в лог. Так
можно разрабатывать и проверять весь поток, никому ничего не отправляя.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from email.utils import parseaddr
from functools import lru_cache

SMTP = "smtp"
BREVO_API = "brevo_api"
NONE = "not_configured"


def _env(name: str, default: str = "") -> str:
    # strip обязателен: лишний пробел или перевод строки в ключе даёт у
    # Brevo ошибку 535, которая выглядит как «неверный пароль».
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    # транспорт
    transport_choice: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    brevo_api_key: str
    mail_from: str
    reply_to: str

    # сервис
    database_url: str
    service_token: str
    code_secret: str

    # как выглядят письма
    brand_name: str
    brand_tagline: str
    site_url: str
    privacy_url: str
    logo_url: str
    header_bg: str
    accent_color: str
    accent_text: str
    lang: str
    texts_file: str
    code_template_id: int

    # коды подтверждения
    code_minutes: int
    code_digits: int
    code_max_attempts: int
    codes_per_email_hour: int
    codes_per_ip_hour: int

    @property
    def transport(self) -> str:
        """Чем реально отправляем. NONE — почта не настроена."""
        smtp_ready = bool(self.smtp_host and self.smtp_user and self.smtp_password)
        api_ready = bool(self.brevo_api_key)
        if self.transport_choice == SMTP:
            return SMTP if smtp_ready else NONE
        if self.transport_choice == BREVO_API:
            return BREVO_API if api_ready else NONE
        if api_ready:
            return BREVO_API
        return SMTP if smtp_ready else NONE

    def problems(self) -> list[str]:
        """Без чего сервис не имеет права стартовать."""
        out = []
        if not self.service_token:
            out.append("SERVICE_TOKEN пуст: любой, кто достучится до сервиса, "
                       "сможет слать письма от вашего домена")
        if not self.code_secret:
            out.append("CODE_SECRET пуст: коды подтверждения нечем подписать")
        if not self.mail_from:
            out.append("MAIL_FROM пуст: не от кого отправлять")
        if self.code_template_id and self.transport == SMTP:
            out.append("CODE_TEMPLATE_ID работает только через API Brevo, "
                       "а выбран SMTP: задайте BREVO_API_KEY")
        return out


@lru_cache
def settings() -> Settings:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    return Settings(
        transport_choice=_env("MAIL_TRANSPORT").lower(),
        smtp_host=_env("MAIL_HOST"),
        smtp_port=_int("MAIL_PORT", 587),
        smtp_user=_env("MAIL_USER"),
        smtp_password=_env("MAIL_PASSWORD"),
        brevo_api_key=_env("BREVO_API_KEY"),
        mail_from=_env("MAIL_FROM"),
        reply_to=_env("MAIL_REPLY_TO"),
        database_url=_env("DATABASE_URL", "sqlite:///./data/mail.db"),
        service_token=_env("SERVICE_TOKEN"),
        code_secret=_env("CODE_SECRET"),
        # Без BRAND_NAME название берётся из MAIL_FROM: «Acme <noreply@acme.io>» → Acme.
        brand_name=_env("BRAND_NAME") or parseaddr(_env("MAIL_FROM"))[0],
        brand_tagline=_env("BRAND_TAGLINE"),
        site_url=_env("SITE_URL").rstrip("/"),
        privacy_url=_env("PRIVACY_URL"),
        logo_url=_env("LOGO_URL"),
        header_bg=_env("HEADER_BG", "#111827"),
        accent_color=_env("ACCENT_COLOR", "#2563eb"),
        accent_text=_env("ACCENT_TEXT_COLOR", "#ffffff"),
        lang=_env("MAIL_LANG", "ru").lower(),
        texts_file=_env("TEXTS_FILE"),
        code_template_id=_int("CODE_TEMPLATE_ID", 0),
        code_minutes=_int("CODE_MINUTES", 15),
        code_digits=_int("CODE_DIGITS", 6),
        code_max_attempts=_int("CODE_MAX_ATTEMPTS", 5),
        codes_per_email_hour=_int("CODES_PER_EMAIL_HOUR", 3),
        codes_per_ip_hour=_int("CODES_PER_IP_HOUR", 20),
    )
