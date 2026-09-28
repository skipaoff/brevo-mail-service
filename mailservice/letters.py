# -*- coding: utf-8 -*-
"""Встроенные письма. Каждое — (тема, текст, html).

Текстовая версия обязательна: по ней письмо попадает в журнал, и её же видят
почтовики без HTML. Бренд (название, логотип, цвета, ссылки в подвале) берётся
из настроек, фразы — из texts.py с поправками из TEXTS_FILE. Сервис встаёт в
любой продукт без правки кода.

Всё, что приходит снаружи, экранируется: сервис получает данные от чужого
бэкенда и не должен верить, что в названии тарифа нет тегов.
"""
from __future__ import annotations

import re
from html import escape

from .config import Settings, settings
from .texts import min_word, phrase

_LINK = "color:#8b8f94;text-decoration:none"


def _tidy(subject: str) -> str:
    """Без названия бренда тема не должна кончаться висящим тире."""
    return re.sub(r"\s+—\s*$", "", subject).strip()


def _lang(lang: str, cfg: Settings) -> str:
    return (lang or cfg.lang or "ru").strip().lower()


def _shell(title: str, body_html: str, why: str, to: str, lang: str,
           cfg: Settings) -> str:
    """Общая рамка письма. Стили только внутри тегов: почтовые клиенты
    вырезают внешние таблицы стилей, и письмо приезжает голым.

    why — почему человек получил это письмо. Стоит в подвале рядом с его
    адресом и отвечает недоверчивому получателю на вопрос «я это заказывал?».
    Без неё письмо с кодом неотличимо от попытки его выманить.
    """
    brand = escape(cfg.brand_name)
    if cfg.logo_url:
        head = (f'<img src="{escape(cfg.logo_url)}" alt="{brand}" height="28" '
                f'style="display:block;height:28px;border:0">')
    else:
        head = (f'<div style="font-size:14px;font-weight:700;letter-spacing:.16em;'
                f'color:#fff">{brand.upper()}</div>')

    links = []
    if cfg.site_url:
        shown = cfg.site_url.split("://", 1)[-1]
        links.append(f'<a href="{escape(cfg.site_url)}" style="{_LINK}">{escape(shown)}</a>')
    if cfg.privacy_url:
        links.append(f'<a href="{escape(cfg.privacy_url)}" style="{_LINK}">'
                     f'{escape(phrase("privacy", lang, cfg.texts_file))}</a>')
    links_html = ('<div style="border-top:1px solid #dcdcdc;padding-top:10px">'
                  + "&nbsp;·&nbsp;".join(links) + "</div>") if links else ""
    tagline = (f'<div style="margin-bottom:10px">{escape(cfg.brand_tagline)}</div>'
               if cfg.brand_tagline else "")
    sent_to = escape(phrase("footer_sent", lang, cfg.texts_file, to=to, why=why))
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#ececec;padding:28px 16px">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:18px;overflow:hidden">
    <div style="background:{escape(cfg.header_bg)};padding:20px 28px">{head}</div>
    <div style="padding:26px 28px 28px">
      <h1 style="font-size:21px;margin:0 0 14px;letter-spacing:-.02em;color:#111214">{escape(title)}</h1>
      {body_html}
    </div>
  </div>
  <div style="max-width:520px;margin:18px auto 0;color:#8b8f94;font-size:12px;line-height:1.6;text-align:center">
    <div style="font-weight:700;letter-spacing:.16em;margin-bottom:6px">{brand.upper()}</div>
    {tagline}
    <div style="margin-bottom:10px">{sent_to}</div>
    {links_html}
  </div>
</div>"""


def _p(text: str, muted: bool = False, top: int = 0) -> str:
    color, size = ("#6b7076", 13) if muted else ("#111214", 14)
    body = escape(text).replace("\n", "<br>")
    return (f'<p style="font-size:{size}px;line-height:1.55;color:{color};'
            f'margin:{top}px 0 0">{body}</p>')


def _button(url: str, text: str, cfg: Settings) -> str:
    return (f'<a href="{escape(url)}" style="display:inline-block;'
            f'background:{escape(cfg.accent_color)};color:{escape(cfg.accent_text)};'
            f'text-decoration:none;padding:12px 22px;border-radius:999px;'
            f'font-weight:600;font-size:14px;margin:18px 0">{escape(text)}</a>')


def _code_box(code: str) -> str:
    """Код крупно и с разрядкой: его переписывают глазами с телефона."""
    return (f'<div style="background:#f4f4f8;border:1px solid #dedee8;border-radius:12px;'
            f'padding:20px 16px;text-align:center;margin:20px 0;'
            f'font-family:SFMono-Regular,Menlo,Consolas,monospace;font-size:32px;'
            f'font-weight:700;letter-spacing:10px;color:#111214">{escape(code)}</div>')


def code_letter(code: str, minutes: int, to: str, lang: str = "",
                cfg: Settings | None = None) -> tuple[str, str, str]:
    """Код подтверждения почты.

    Код стоит в теме намеренно: человек видит его в уведомлении на телефоне
    и не открывает письмо ради шести цифр.
    """
    cfg = cfg or settings()
    lang = _lang(lang, cfg)
    values = dict(brand=cfg.brand_name, code=code, minutes=minutes,
                  min_word=min_word(minutes, lang))

    def t(key: str) -> str:
        return phrase(key, lang, cfg.texts_file, **values)

    text = f"{t('code_lead')}\n\n{t('code_label')}: {code}\n\n{t('code_rule')}"
    html = _shell(t("code_title"),
                  _p(t("code_lead")) + _code_box(code) + _p(t("code_rule"), muted=True),
                  t("code_why"), to, lang, cfg)
    return _tidy(t("code_subject")), text, html


def reset_letter(link: str, minutes: int, to: str, lang: str = "",
                 cfg: Settings | None = None) -> tuple[str, str, str]:
    """Сброс пароля. Ссылку с одноразовым ключом собирает бэкенд продукта."""
    cfg = cfg or settings()
    lang = _lang(lang, cfg)
    values = dict(brand=cfg.brand_name, minutes=minutes, min_word=min_word(minutes, lang))

    def t(key: str) -> str:
        return phrase(key, lang, cfg.texts_file, **values)

    text = f"{t('reset_lead')}\n\n{t('link_label')}: {link}\n\n{t('reset_rule')}"
    html = _shell(t("reset_title"),
                  _p(t("reset_lead")) + _button(link, t("reset_button"), cfg)
                  + _p(t("reset_rule"), muted=True),
                  t("reset_why"), to, lang, cfg)
    return _tidy(t("reset_subject")), text, html


def notice_letter(title: str, paragraphs: list[str], why: str, to: str,
                  subject: str = "", button_text: str = "", button_url: str = "",
                  lang: str = "", cfg: Settings | None = None) -> tuple[str, str, str]:
    """Любое уведомление в той же рамке: оплата прошла, подписка кончается,
    заказ готов. Одна мысль и не больше одной кнопки. Тексты даёт бэкенд."""
    cfg = cfg or settings()
    lang = _lang(lang, cfg)
    subject = subject or (f"{title} — {cfg.brand_name}" if cfg.brand_name else title)
    button_text = button_text or phrase("open", lang, cfg.texts_file)
    parts = [p for p in (paragraphs or []) if (p or "").strip()]
    text = "\n\n".join(parts)
    if button_url:
        text += f"\n\n{button_text}: {button_url}"
    body = "".join(_p(p, top=0 if i == 0 else 14) for i, p in enumerate(parts))
    if button_url:
        body += _button(button_url, button_text, cfg)
    return subject, text.strip(), _shell(title, body, why, to, lang, cfg)
