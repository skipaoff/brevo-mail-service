# -*- coding: utf-8 -*-
"""Фразы встроенных писем на трёх языках.

Язык письма: поле `lang` в запросе, иначе MAIL_LANG, иначе ru. Любую фразу
можно заменить без правки кода: файл TEXTS_FILE в формате JSON
`{"ru": {"code_lead": "..."}, "de": {...}}`. Новый язык добавляется там же;
чего в нём не хватает, берётся из английского.

В фразах доступны подстановки {brand}, {code}, {minutes}, {min_word}, {to}, {why}.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("mailservice.texts")

TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        "footer_sent": "Письмо отправлено на {to}, потому что {why}",
        "privacy": "Конфиденциальность",
        "open": "Открыть",
        "link_label": "Ссылка",
        "code_subject": "Код подтверждения {code} — {brand}",
        "code_title": "Подтвердите почту",
        "code_lead": "Введите этот код на странице подтверждения.",
        "code_label": "Код",
        "code_rule": ("Код действует {minutes} {min_word} и работает один раз. Никому его "
                      "не сообщайте: мы никогда не спрашиваем код в переписке."),
        "code_why": ("этот адрес указали при регистрации или входе. Если это были не вы — "
                     "просто не вводите код."),
        "reset_subject": "Смена пароля — {brand}",
        "reset_title": "Смена пароля",
        "reset_lead": ("Кто-то запросил смену пароля для этого адреса. "
                       "Если это вы — задайте новый:"),
        "reset_button": "Задать новый пароль",
        "reset_rule": ("Ссылка работает {minutes} {min_word} и только один раз.\n"
                       "Если это были не вы, ничего делать не нужно: пароль останется прежним."),
        "reset_why": "кто-то запросил смену пароля для этого адреса.",
    },
    "uk": {
        "footer_sent": "Лист надіслано на {to}, тому що {why}",
        "privacy": "Конфіденційність",
        "open": "Відкрити",
        "link_label": "Посилання",
        "code_subject": "Код підтвердження {code} — {brand}",
        "code_title": "Підтвердіть пошту",
        "code_lead": "Введіть цей код на сторінці підтвердження.",
        "code_label": "Код",
        "code_rule": ("Код діє {minutes} {min_word} і працює один раз. Нікому його "
                      "не повідомляйте: ми ніколи не питаємо код у листуванні."),
        "code_why": ("цю адресу вказали під час реєстрації або входу. Якщо це були не ви — "
                     "просто не вводьте код."),
        "reset_subject": "Зміна пароля — {brand}",
        "reset_title": "Зміна пароля",
        "reset_lead": ("Хтось запросив зміну пароля для цієї адреси. "
                       "Якщо це ви — задайте новий:"),
        "reset_button": "Задати новий пароль",
        "reset_rule": ("Посилання працює {minutes} {min_word} і лише один раз.\n"
                       "Якщо це були не ви, нічого робити не потрібно: пароль залишиться попереднім."),
        "reset_why": "хтось запросив зміну пароля для цієї адреси.",
    },
    "en": {
        "footer_sent": "This email was sent to {to} because {why}",
        "privacy": "Privacy",
        "open": "Open",
        "link_label": "Link",
        "code_subject": "Your verification code {code} — {brand}",
        "code_title": "Confirm your email",
        "code_lead": "Enter this code on the verification page.",
        "code_label": "Code",
        "code_rule": ("The code is valid for {minutes} {min_word} and works once. Don't share "
                      "it with anyone: we never ask for codes in messages."),
        "code_why": ("this address was entered during sign-up or sign-in. If it wasn't you, "
                     "just ignore this email."),
        "reset_subject": "Password reset — {brand}",
        "reset_title": "Reset your password",
        "reset_lead": ("Someone requested a password reset for this address. "
                       "If it was you, set a new one:"),
        "reset_button": "Set a new password",
        "reset_rule": ("The link is valid for {minutes} {min_word} and works only once.\n"
                       "If it wasn't you, no action is needed: your password stays the same."),
        "reset_why": "someone requested a password reset for this address.",
    },
}

_MINUTES = {
    "ru": ("минуту", "минуты", "минут"),
    "uk": ("хвилину", "хвилини", "хвилин"),
    "en": ("minute", "minutes", "minutes"),
}


def min_word(n: int, lang: str) -> str:
    """«1 минуту, 3 минуты, 15 минут» — без этого «21 минут» режет глаз."""
    one, few, many = _MINUTES.get(lang, _MINUTES["en"])
    if lang not in ("ru", "uk"):
        return one if n == 1 else many
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


@lru_cache
def _overrides(path: str) -> dict[str, dict[str, str]]:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Сломанный файл фраз не должен ронять отправку кодов: пишем в лог
        # и шлём встроенными фразами.
        log.error("TEXTS_FILE %s не прочитан: %s", path, exc)
        return {}
    return {str(lang): dict(v) for lang, v in data.items() if isinstance(v, dict)}


class _Keep(dict):
    """Неизвестная подстановка остаётся как есть, а не роняет письмо."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def phrase(key: str, lang: str, texts_file: str = "", **values) -> str:
    own = _overrides(texts_file)
    template = (own.get(lang, {}).get(key)
                or TEXTS.get(lang, {}).get(key)
                or own.get("en", {}).get(key)
                or TEXTS["en"][key])
    return template.format_map(_Keep(values))
