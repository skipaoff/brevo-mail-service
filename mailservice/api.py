# -*- coding: utf-8 -*-
"""HTTP-вход сервиса. Бэкенд продукта ходит сюда с заголовком
`Authorization: Bearer <SERVICE_TOKEN>`; наружу в интернет сервис не
выставляется, он живёт во внутренней сети рядом с бэкендом.

Запуск: uvicorn mailservice.api:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import codes, journal, letters
from .config import settings
from .store import engine

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Проверка настроек и создание таблиц до первого запроса."""
    cfg = settings()
    problems = cfg.problems()
    if problems:
        raise RuntimeError("сервис не настроен:\n- " + "\n- ".join(problems))
    engine(cfg.database_url)
    logging.getLogger("mailservice").info("почта: %s", cfg.transport)
    yield


app = FastAPI(title="Brevo mail service", version="1.1.0", lifespan=lifespan)


def auth(authorization: str = Header(default="")) -> None:
    token = settings().service_token
    given = authorization.removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(given, token):
        raise HTTPException(status_code=401, detail="нужен заголовок Authorization: Bearer <SERVICE_TOKEN>")


# ------------------------------------------------------------------ модели

class SendResult(BaseModel):
    ok: bool
    status: str = Field(description="sent | failed | skipped")
    why: str
    event_id: str
    message_id: str = Field(description="идентификатор письма у Brevo")


class RawLetter(BaseModel):
    to: str
    subject: str
    text: str
    html: str = ""
    kind: str = Field(default="custom", description="вид письма для журнала")
    ref: str = Field(default="", description="id пользователя в вашей системе")
    secrets: list[str] = Field(default_factory=list,
                               description="что замаскировать в журнале: коды, ссылки с ключом")


class ResetLetter(BaseModel):
    to: str
    link: str = Field(description="ссылка с одноразовым ключом, собирает ваш бэкенд")
    minutes: int = 45
    ref: str = ""
    lang: str = Field(default="", description="ru | uk | en; пусто — MAIL_LANG")


class NoticeLetter(BaseModel):
    to: str
    kind: str = "notice"
    title: str
    paragraphs: list[str]
    why: str = Field(description="почему человек получил письмо, стоит в подвале")
    subject: str = ""
    button_text: str = ""
    button_url: str = ""
    ref: str = ""
    secrets: list[str] = Field(default_factory=list)
    lang: str = Field(default="", description="ru | uk | en; пусто — MAIL_LANG")


class CodeRequest(BaseModel):
    email: str
    purpose: str = "verify"
    ref: str = ""
    ip: str = Field(default="", description="IP человека, для ограничения частоты")
    lang: str = Field(default="", description="ru | uk | en; пусто — MAIL_LANG")


class TemplateLetter(BaseModel):
    to: str
    template_id: int = Field(description="номер шаблона в Brevo → Templates")
    params: dict = Field(default_factory=dict, description="подстановки {{ params.x }} в шаблоне")
    kind: str = "template"
    ref: str = ""
    secrets: list[str] = Field(default_factory=list)


class CodeIssued(BaseModel):
    status: str = Field(description="sent | failed | skipped | rate_limited")
    why: str
    expires_in_minutes: int
    event_id: str = ""


class CodeCheck(BaseModel):
    email: str
    code: str
    purpose: str = "verify"


class CodeResult(BaseModel):
    result: str = Field(description="ok | wrong | expired | burned | none")


def _result(posted: journal.Posted) -> SendResult:
    return SendResult(ok=posted.ok, status=posted.status, why=posted.why,
                      event_id=posted.event_id, message_id=posted.message_id)


# ------------------------------------------------------------------ ручки

@app.get("/health")
def health():
    return {"ok": True, "transport": settings().transport}


@app.post("/v1/send", response_model=SendResult, dependencies=[Depends(auth)])
def send_raw(body: RawLetter):
    """Готовое письмо от вашего бэкенда: тема, текст, html по желанию."""
    return _result(journal.send(body.kind, body.to, body.subject, body.text, body.html,
                                ref=body.ref, secrets=body.secrets))


@app.post("/v1/send/reset", response_model=SendResult, dependencies=[Depends(auth)])
def send_reset(body: ResetLetter):
    """Письмо со ссылкой на смену пароля. Ссылка в журнал не попадёт."""
    subject, text, html = letters.reset_letter(body.link, body.minutes, body.to, body.lang)
    return _result(journal.send("password_reset", body.to, subject, text, html,
                                ref=body.ref, secrets=(body.link,)))


@app.post("/v1/send/notice", response_model=SendResult, dependencies=[Depends(auth)])
def send_notice(body: NoticeLetter):
    """Уведомление в фирменной рамке: оплата, окончание подписки и т.п."""
    subject, text, html = letters.notice_letter(
        body.title, body.paragraphs, body.why, body.to, subject=body.subject,
        button_text=body.button_text, button_url=body.button_url, lang=body.lang)
    return _result(journal.send(body.kind, body.to, subject, text, html,
                                ref=body.ref, secrets=body.secrets))


@app.post("/v1/send/template", response_model=SendResult, dependencies=[Depends(auth)])
def send_template(body: TemplateLetter):
    """Письмо по шаблону, свёрстанному в Brevo. Работает только через API Brevo."""
    return _result(journal.send_template(body.kind, body.to, body.template_id, body.params,
                                         ref=body.ref, secrets=body.secrets))


@app.post("/v1/codes/send", response_model=CodeIssued, dependencies=[Depends(auth)])
def codes_send(body: CodeRequest):
    """Выдать код и отправить письмом. На частые запросы — 429."""
    issued = codes.issue(body.email, body.purpose, ref=body.ref, ip=body.ip, lang=body.lang)
    payload = CodeIssued(**issued._asdict())
    if issued.status == codes.RATE_LIMITED:
        return JSONResponse(status_code=429, content=payload.model_dump())
    return payload


@app.post("/v1/codes/check", response_model=CodeResult, dependencies=[Depends(auth)])
def codes_check(body: CodeCheck):
    """Проверить код. Всегда 200, исход — в поле result."""
    return CodeResult(result=codes.check(body.email, body.code, body.purpose))


@app.get("/v1/events", dependencies=[Depends(auth)])
def events(to: str = "", ref: str = "", kind: str = "",
           limit: int = Query(default=50, le=500)):
    """Журнал писем, новые сверху. Для ответа на «мне не пришло»."""
    return {"events": journal.events(to=to, ref=ref, kind=kind, limit=limit)}
