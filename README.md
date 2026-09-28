# brevo-mail-service

Готовая транзакционная почта через [Brevo](https://www.brevo.com) для любого проекта:
коды подтверждения почты, сброс пароля, уведомления и журнал всего отправленного.
Сервис ставится рядом с бэкендом, и бэкенд ходит в него по HTTP. Язык бэкенда значения
не имеет: Node, PHP, Go, Python, что угодно.

```
 ваш бэкенд ──HTTP──▶ brevo-mail-service ──▶ Brevo (API или SMTP) ──▶ почта человека
                              │
                              ▼
                    своя база сервиса
                    ├─ mail_events   журнал: кому, что, когда, чем кончилось
                    └─ email_codes   коды подтверждения (только подписи, не сами коды)
```

Под свой продукт сервис настраивается через `.env`, код трогать не нужно: название
и логотип, цвета, язык писем (ru, uk, en), свои формулировки, свои шаблоны из
редактора Brevo.

## Содержание

- [Чего сервис не делает](#чего-сервис-не-делает)
- [Запуск за 10 минут](#запуск-за-10-минут)
- [API](#api)
- [Как подогнать под свой проект](#как-подогнать-под-свой-проект)
- [Настройки](#настройки-env)
- [Безопасность](#безопасность)
- [Настройка Brevo и домена](docs/brevo-setup.md)

## Чего сервис не делает

- **Это не маркетинговые рассылки.** Кампаний, сегментов и списков отписки нет. Письмо
  уходит только в ответ на действие человека: зарегистрировался, забыл пароль, оплатил.
  Для массовых рассылок есть кампании в самом Brevo.
- **Повторов в очереди нет.** Если письмо не ушло, сервис так и говорит, а бэкенд
  показывает человеку кнопку «прислать ещё раз». Это честнее тихого повтора.
- **Пользователей сервис не знает.** Отметку «почта подтверждена» хранит ваш бэкенд. Для
  сервиса пользователь — это строка `ref`, то есть его id в вашей системе.

## Запуск за 10 минут

1. **Домен в Brevo.** Подтвердите домен и получите ключ по инструкции
   [docs/brevo-setup.md](docs/brevo-setup.md). Пока ключа нет, можно идти дальше:
   сервис работает и без него, см. ниже.
2. **Настройки.**
   ```bash
   cp .env.example .env
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # → SERVICE_TOKEN
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # → CODE_SECRET
   ```
   В `.env` обязательны `SERVICE_TOKEN`, `CODE_SECRET`, `MAIL_FROM`, а для реальной
   отправки ещё и `BREVO_API_KEY`. Остальное задаёт вид писем.
3. **Запуск.**
   ```bash
   docker compose up -d --build
   curl http://127.0.0.1:8080/health     # {"ok":true,"transport":"brevo_api"}
   ```
   Без Docker:
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/uvicorn mailservice.api:app --host 0.0.0.0 --port 8080
   ```
4. **Проверка на себе.** Отправьте код на свою почту и убедитесь, что письмо не попало в спам:
   ```bash
   curl -X POST http://127.0.0.1:8080/v1/codes/send \
     -H "Authorization: Bearer $SERVICE_TOKEN" -H "Content-Type: application/json" \
     -d '{"email": "you@gmail.com"}'
   ```
5. **Подключение бэкенда.** Опишите сценарии ниже, и готово.

**Без ключа Brevo сервис тоже работает.** Письма не уходят, а целиком пишутся в лог,
а в журнале у них статус `skipped`. Так весь поток регистрации можно разрабатывать
локально, никому ничего не отправляя. Код подтверждения в этом режиме виден в логе.

Интерактивная документация API открывается на `http://127.0.0.1:8080/docs`.

## API

Каждый запрос, кроме `/health`, передаёт заголовок `Authorization: Bearer <SERVICE_TOKEN>`.

| Метод | Путь | Что делает |
|---|---|---|
| GET | `/health` | жив ли сервис и чем отправляет: `brevo_api`, `smtp`, `not_configured` |
| POST | `/v1/codes/send` | выдать код подтверждения и отправить письмом |
| POST | `/v1/codes/check` | проверить введённый код |
| POST | `/v1/send/reset` | письмо со ссылкой для смены пароля |
| POST | `/v1/send/notice` | уведомление во встроенной рамке (оплата, окончание подписки и т.п.) |
| POST | `/v1/send/template` | письмо по шаблону, свёрстанному в Brevo |
| POST | `/v1/send` | произвольное письмо: тема, текст, html по желанию |
| GET | `/v1/events` | журнал писем: `?to=`, `?ref=`, `?kind=`, `?limit=` |

Ответ любой отправки выглядит так:

```json
{"ok": true, "status": "sent", "why": "отправлено",
 "event_id": "9f1c…", "message_id": "<202609281200.123@smtp-relay.mailin.fr>"}
```

Возможные `status`: `sent` (письмо ушло), `failed` (Brevo отказал, причина в `why`),
`skipped` (почта не настроена, это местный запуск, а не поломка).

Во всех ручках с письмами есть необязательные поля `ref` (id пользователя у вас, чтобы
потом найти его письма в журнале) и `lang` (`ru`, `uk` или `en` для встроенных писем).

### Сценарий 1. Регистрация с подтверждением почты

```
1. Человек зарегистрировался
   бэкенд → POST /v1/codes/send {"email": "...", "ref": "<user_id>", "ip": "<ip человека>"}
      200, status=sent     → показать поле для кода
      200, status=failed   → «письмо не ушло, попробуйте ещё раз»
      429, rate_limited    → текст из why: «слишком часто, попробуйте позже»

2. Человек ввёл код
   бэкенд → POST /v1/codes/check {"email": "...", "code": "123456"}
      result=ok       → бэкенд ставит у себя email_verified_at = now()
      result=wrong    → «неверный код» (попытка засчитана)
      result=expired  → «код истёк, запросите новый»
      result=burned   → «попытки кончились, запросите новый»
      result=none     → «запросите код»
```

Сам код бэкенд не видит: его знает только человек, получивший письмо. Правила для кодов
по умолчанию такие: 6 цифр, живёт 15 минут, 5 попыток, не больше 3 кодов на адрес и
20 на IP в час. Живой код всегда один, новый запрос гасит прежний. Всё это меняется в `.env`.

Поле `purpose` (по умолчанию `verify`) разделяет независимые потоки. Например, код для
входа без пароля можно выдавать с `purpose: "login"`, и он не пересечётся с кодом
подтверждения.

### Сценарий 2. Сброс пароля

Одноразовый ключ выпускает и проверяет ваш бэкенд, а в базе он хранит его хеш. Сервис
только доставляет ссылку, и в журнал она не попадает.

```bash
curl -X POST http://mail:8080/v1/send/reset \
  -H "Authorization: Bearer $SERVICE_TOKEN" -H "Content-Type: application/json" \
  -d '{"to": "user@example.com", "link": "https://app.example.com/reset?t=…", "minutes": 45, "ref": "42"}'
```

### Сценарий 3. Уведомление

```bash
curl -X POST http://mail:8080/v1/send/notice \
  -H "Authorization: Bearer $SERVICE_TOKEN" -H "Content-Type: application/json" \
  -d '{
    "to": "user@example.com", "ref": "42", "kind": "plan_paid",
    "title": "Тариф «Про» подключён",
    "paragraphs": ["Спасибо, оплата прошла. Тариф уже действует.",
                   "Подписка активна до 4 октября. За сутки до этой даты мы напомним письмом."],
    "why": "вы оплатили подписку. Это подтверждение платежа, сохраните его.",
    "button_text": "Открыть кабинет", "button_url": "https://app.example.com"
  }'
```

`why` стоит в подвале письма рядом с адресом получателя: «Письмо отправлено на …, потому
что …». Эта строка отвечает недоверчивому человеку на вопрос «я это заказывал?», поэтому
не пропускайте её.

### Сценарий 4. Шаблон из редактора Brevo

Если письма удобнее верстать в визуальном редакторе Brevo (раздел **Templates**),
передайте номер шаблона и подстановки. Внутри шаблона они доступны как `{{ params.name }}`.

```json
POST /v1/send/template
{"to": "user@example.com", "template_id": 7, "kind": "order_ready", "ref": "42",
 "params": {"name": "Аня", "order": 1543, "link": "https://…"}}
```

Работает только через API Brevo (`BREVO_API_KEY`). В журнал ложатся номер шаблона
и параметры, а всё, что указано в `secrets`, в них замаскировано.

### Сценарий 5. Своё письмо целиком

```json
POST /v1/send
{"to": "user@example.com", "subject": "Вход по ссылке", "text": "Ссылка: https://…?t=SECRET",
 "html": "<p>…</p>", "kind": "magic_link", "ref": "42", "secrets": ["https://…?t=SECRET"]}
```

Всё, что перечислено в `secrets`, в журнале заменяется на `••••••`. Сервис **не угадывает**
секреты в тексте сам: коды и ссылки с ключом передавайте явно.

### Журнал: ответ на «мне не пришло»

```bash
curl "http://mail:8080/v1/events?to=user@example.com" -H "Authorization: Bearer $SERVICE_TOKEN"
```

По журналу видно, ушло ли письмо от нас, и если нет, то почему (`detail`). Если письмо
ушло, его можно найти в логах транзакционных писем Brevo по `provider_id` или по метке:
вид письма (`kind`) уходит в Brevo как тег. Там же видно, было ли письмо доставлено,
открыто или вернулось. Журнал только дописывается, HTML писем в нём не хранится.

### Пример вызова из Node.js

```js
const MAIL = process.env.MAIL_SERVICE_URL;          // http://mail:8080
const auth = { Authorization: `Bearer ${process.env.MAIL_SERVICE_TOKEN}`,
               "Content-Type": "application/json" };

async function sendVerifyCode(user, ip) {
  const r = await fetch(`${MAIL}/v1/codes/send`, { method: "POST", headers: auth,
    body: JSON.stringify({ email: user.email, ref: String(user.id), ip }) });
  return { httpStatus: r.status, ...(await r.json()) };   // 429 = слишком часто
}

async function checkVerifyCode(email, code) {
  const r = await fetch(`${MAIL}/v1/codes/check`, { method: "POST", headers: auth,
    body: JSON.stringify({ email, code }) });
  return (await r.json()).result;                          // ok | wrong | expired | burned | none
}
```

### Пример вызова из PHP

```php
function mail_service(string $path, array $body): array {
    $ch = curl_init(getenv('MAIL_SERVICE_URL') . $path);
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json',
                               'Authorization: Bearer ' . getenv('MAIL_SERVICE_TOKEN')],
        CURLOPT_POSTFIELDS => json_encode($body),
    ]);
    $out = json_decode(curl_exec($ch), true);
    $out['http_status'] = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    return $out;
}

mail_service('/v1/codes/send', ['email' => $email, 'ref' => (string)$userId, 'ip' => $_SERVER['REMOTE_ADDR']]);
$result = mail_service('/v1/codes/check', ['email' => $email, 'code' => $code])['result'];
```

### Если бэкенд на Python

Сервис можно не поднимать отдельно, а поставить как пакет и вызывать напрямую:

```bash
pip install "git+https://github.com/skipaoff/brevo-mail-service.git"
```

```python
from mailservice import codes, journal, letters

codes.issue(email, ref=str(user.id), ip=ip)      # → Issued(status, why, …)
codes.check(email, code)                          # → "ok" | "wrong" | …
subject, text, html = letters.reset_letter(link, 45, email)
journal.send("password_reset", email, subject, text, html, ref=str(user.id), secrets=(link,))
```

Настройки в этом случае берутся из тех же переменных окружения.

## Как подогнать под свой проект

Всё настраивается в `.env`, код трогать не нужно.

**Бренд.** `BRAND_NAME` (если пусто, берётся имя из `MAIL_FROM`), `BRAND_TAGLINE`, `LOGO_URL`
(картинка вместо названия в шапке), `HEADER_BG`, `ACCENT_COLOR` и `ACCENT_TEXT_COLOR`
(кнопка), `SITE_URL` и `PRIVACY_URL` (ссылки в подвале). Как выглядят письма, можно
посмотреть в [docs/preview](docs/preview).

**Язык.** Встроенные письма есть на русском, украинском и английском. Язык по умолчанию
задаётся в `MAIL_LANG`, а в каждом запросе можно передать свой `lang`.

**Свои формулировки.** Любую фразу встроенных писем можно заменить файлом JSON, указанным
в `TEXTS_FILE`. Там же можно добавить новый язык: чего в нём не хватит, возьмётся из
английского.

```json
{
  "ru": {"code_lead": "Вы входите в {brand}. Введите код:"},
  "de": {"code_title": "E-Mail bestätigen", "code_lead": "Geben Sie diesen Code ein.",
         "code_label": "Code", "code_subject": "Ihr Code {code} — {brand}"}
}
```

Ключи и подстановки (`{brand}`, `{code}`, `{minutes}`, `{min_word}`) перечислены
в [mailservice/texts.py](mailservice/texts.py). Файл читается один раз, поэтому после
правки сервис нужно перезапустить.

**Письмо с кодом из редактора Brevo.** Если задать `CODE_TEMPLATE_ID`, письмо с кодом
пойдёт по шаблону Brevo вместо встроенного. В шаблоне доступны `{{ params.code }}`,
`{{ params.minutes }}` и `{{ params.brand }}`. Работает только через API Brevo.

**Свои письма.** Всё, чего нет среди встроенных, отправляется через `/v1/send/notice`
(во встроенной рамке), `/v1/send/template` (шаблон Brevo) или `/v1/send` (свой HTML).

## Настройки (`.env`)

| Переменная | Зачем |
|---|---|
| `BREVO_API_KEY` | API-ключ Brevo (`xkeysib-…`). Рекомендуемый способ: возвращает id письма и умеет шаблоны |
| `MAIL_HOST`, `MAIL_PORT`, `MAIL_USER`, `MAIL_PASSWORD` | SMTP Brevo: `smtp-relay.brevo.com`, `587`, логин из раздела SMTP, **SMTP-ключ** (`xsmtpsib-…`) |
| `MAIL_TRANSPORT` | `brevo_api` или `smtp`, если заданы оба. Пусто — берётся API |
| `MAIL_FROM` | `Название <noreply@ваш-домен>`, домен подтверждён в Brevo. **Обязателен** |
| `MAIL_REPLY_TO` | куда уйдёт ответ человека, например `support@ваш-домен` |
| `SERVICE_TOKEN` | общий секрет с бэкендом. **Обязателен**, без него сервис не стартует |
| `CODE_SECRET` | подпись кодов. **Обязателен**. Смена гасит все живые коды |
| `DATABASE_URL` | по умолчанию `sqlite:///./data/mail.db`; для Postgres `postgresql+psycopg://…` и `pip install "psycopg[binary]"` |
| `BRAND_NAME`, `BRAND_TAGLINE`, `LOGO_URL`, `SITE_URL`, `PRIVACY_URL` | шапка и подвал писем |
| `HEADER_BG`, `ACCENT_COLOR`, `ACCENT_TEXT_COLOR` | цвета шапки и кнопки |
| `MAIL_LANG` | язык встроенных писем по умолчанию: `ru`, `uk`, `en` |
| `TEXTS_FILE` | JSON со своими формулировками |
| `CODE_TEMPLATE_ID` | шаблон Brevo для письма с кодом |
| `CODE_MINUTES`, `CODE_DIGITS`, `CODE_MAX_ATTEMPTS`, `CODES_PER_EMAIL_HOUR`, `CODES_PER_IP_HOUR` | правила кодов |

## Безопасность

- **Сервис не выставляется в интернет.** Он живёт во внутренней сети рядом с бэкендом
  (в `docker-compose.yml` порт открыт только на `127.0.0.1`). Открытая ручка отправки
  писем от вашего домена — подарок спамерам.
- **Коды хранятся подписью HMAC** с `CODE_SECRET`, которого в базе нет. Утечка базы
  не раскрывает живые коды.
- **Частота ограничена** на адрес и на IP. Без этого ручку кода используют для рассылки
  спама на чужие адреса, и репутация домена сгорает за день. IP человека передавайте
  в поле `ip`.
- **Ключи Brevo не пересылайте в чатах.** Если ключ засветился, выпустите новый, а старый удалите.

## Настройка Brevo и домена

Пошагово с граблями описано в [docs/brevo-setup.md](docs/brevo-setup.md). Коротко:

1. Подтвердить домен в Brevo: TXT для владения, два CNAME для DKIM, DMARC с `p=none`.
2. Выпустить ключ: API-ключ для `BREVO_API_KEY` или SMTP-ключ для `MAIL_PASSWORD`.
   Это разные ключи.
3. **Сразу написать в поддержку Brevo** с просьбой заменить `List-Unsubscribe` на
   `List-Help` для транзакционных писем. Иначе человек, нажавший в Gmail «отписаться»
   от письма с кодом, перестанет получать коды и ссылки для смены пароля, и вы об этом
   не узнаете.

## Разработка

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

| Файл | Что внутри |
|---|---|
| `mailservice/transport.py` | доставка одного письма через API или SMTP Brevo, никогда не бросает исключение |
| `mailservice/letters.py` | HTML-рамка и встроенные письма, бренд из настроек |
| `mailservice/texts.py` | фразы писем на ru, uk, en и склонение «минут» |
| `mailservice/journal.py` | «отправить и записать»: маскировка секретов, журнал |
| `mailservice/codes.py` | коды: выдача, подпись, лимиты, проверка |
| `mailservice/store.py` | таблицы `mail_events` и `email_codes`, создаются при старте |
| `mailservice/api.py` | HTTP-ручки (FastAPI) |
| `docs/preview/` | как выглядят встроенные письма: откройте HTML в браузере |
