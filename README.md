# mtg-orchestrator

Самохостируемый оркестратор MTProto-прокси для обхода блокировок Telegram.

Управляет пулом [`mtg`](https://github.com/9seconds/mtg)-инстансов (fake-TLS),
маршрутизирует входящий трафик через nginx по SNI на порту `:443`, и предоставляет
Telegram Mini App для управления и мониторинга. Разворачивается на VPS одной командой.

---

## Топология

```
Telegram-клиент
      │
      ▼ :443 (TCP)
┌──────────────┐
│  Москва VPS  │  iptables DNAT → Франкфурт :443 и :80
└──────────────┘
      │
      ▼ :443 / :80
┌───────────────────────────────────────────────────┐
│  Франкфурт VPS  (Docker-стек)                     │
│                                                   │
│  nginx:443 (ssl_preread SNI mux)                  │
│    ├─ exact PANEL_DOMAIN SNI → backend:8443        │
│    └─ default / unknown SNI → mtg-default:3128    │
│                                                   │
│  nginx:80 (ACME HTTP-01 only)                     │
│    └─ /.well-known/acme-challenge/ → certbot vol  │
│                                                   │
│  backend (FastAPI + Mini App)                     │
│  mtg-default (fake-TLS)                           │
│  certbot (Let's Encrypt, run on-demand)           │
│  docker-socket-proxy (Docker API boundary)        │
└───────────────────────────────────────────────────┘
```

Панель (`https://PANEL_DOMAIN`) доступна только через `:443` по точному SNI.
Все остальные подключения (включая сканеры без SNI) получают fake-TLS от `mtg-default` —
панель не видна без знания правильного поддомена.

---

## Быстрый старт

### 1. Требования

- Два VPS: **Москва** (тонкий L4-релей) и **Франкфурт** (рабочий узел).
- На Москве: iptables DNAT портов **443 и 80** на Франкфурт.
- На Франкфурте: Docker + docker-compose v2.
- Домен (`*.ru` или любой) с A-записью на **московский IP**.

### 2. Подготовка сервера (Москва — iptables)

Порты 443 **и** 80 должны быть проброшены на Франкфурт:

```bash
# Замените FRANKFURT_IP на реальный IP Франкфурта
FRANKFURT_IP=5.6.7.8

# DNAT :443
iptables -t nat -A PREROUTING -p tcp --dport 443 -j DNAT --to-destination ${FRANKFURT_IP}:443
# DNAT :80 (обязательно для HTTP-01 certbot)
iptables -t nat -A PREROUTING -p tcp --dport 80  -j DNAT --to-destination ${FRANKFURT_IP}:80
# MASQUERADE (чтобы Франкфурт видел московский публичный IP, а не RFC1918)
iptables -t nat -A POSTROUTING -d ${FRANKFURT_IP} -j MASQUERADE
# MSS clamping для предотвращения фрагментации
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# Сохранить правила
iptables-save > /etc/iptables/rules.v4
```

> **Важно:** порт 80 должен быть проброшен до первого запуска certbot. Без него
> HTTP-01 challenge не пройдёт.

### 3. Настройка конфигурации (Франкфурт)

```bash
git clone https://github.com/your-org/mtg-orchestrator
cd mtg-orchestrator

# Скопировать шаблон и заполнить реальными значениями
cp .env.example .env
nano .env  # или любой текстовый редактор
```

Заполнить в `.env`:

| Переменная | Значение |
|---|---|
| `MOSCOW_IP` | Публичный IP московского VPS |
| `FRANKFURT_IP` | Публичный IP Франкфурта |
| `PANEL_DOMAIN` | Поддомен панели, например `mtg.yourdomain.ru` |
| `LE_EMAIL` | Email для Let's Encrypt (уведомления об истечении) |
| `MTG_DEFAULT_SECRET` | Секрет существующего mtg-инстанса (`ee…`) |
| `MTG_DEFAULT_DOMAIN` | Маскировочный домен (например `ria.ru`) |
| `BOT_TOKEN` | Токен Telegram-бота от @BotFather |
| `OWNER_USER_ID` | Ваш Telegram user ID (число) |
| `WEBHOOK_SECRET` | Случайная строка для webhook |

> **Никогда не коммитьте `.env`** — файл добавлен в `.gitignore`.

### 4. Запустить базовые сервисы

```bash
docker compose up -d nginx backend mtg-default docker-socket-proxy
```

Проверить, что сервисы запустились:

```bash
docker compose ps
docker compose logs nginx
docker compose logs backend
```

### 5. Проверить DNS и порт 80

Перед запуском certbot убедитесь:

```bash
# Проверить, что A-запись domain указывает на московский IP
nslookup mtg.yourdomain.ru

# Проверить, что порт 80 пробрасывается с Москвы на Франкфурт
# (запустить с внешней машины, НЕ с московского VPS)
curl -v http://mtg.yourdomain.ru/.well-known/acme-challenge/test
# Ожидается 404 от nginx (это нормально — файла нет, но соединение дошло)
```

---

## Получение TLS-сертификата (HTTP-01 bootstrap)

Сертификат выпускается через certbot в режиме `--webroot` (HTTP-01).

**Как это работает:**
1. certbot записывает challenge-файл в `/var/www/certbot/.well-known/acme-challenge/`
2. Let's Encrypt подключается к `http://PANEL_DOMAIN:80/.well-known/acme-challenge/<token>`
3. nginx обслуживает этот путь напрямую из общего тома `acme-webroot`
4. certbot получает сертификат и сохраняет в `certs-volume:/etc/letsencrypt`

**Выполнить на Франкфурте:**

```bash
# Загрузите переменные из .env
source .env

# Первичная выдача сертификата
docker compose run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d "${PANEL_DOMAIN}" \
  --email "${LE_EMAIL}" \
  --agree-tos --no-eff-email \
  ${CERTBOT_STAGING}
```

> Для тестирования без расходования лимитов Let's Encrypt установите в `.env`:
> `CERTBOT_STAGING=--staging`

После успешной выдачи сертификат находится в томе `certs-volume`:
- `/etc/letsencrypt/live/${PANEL_DOMAIN}/fullchain.pem`
- `/etc/letsencrypt/live/${PANEL_DOMAIN}/privkey.pem`

**Перезапустить backend, чтобы применить сертификат:**

```bash
docker compose restart backend
```

### Автоматическое продление

Добавить в crontab на Франкфурте (запускать от пользователя с доступом к Docker):

```bash
# Запускать в 3:00 AM UTC каждый день; certbot пропустит, если до истечения > 30 дней
0 3 * * * cd /path/to/mtg-orchestrator && docker compose run --rm certbot renew --quiet && docker compose restart backend
```

> **Примечание:** порт 80 (DNAT на Москве) должен оставаться открытым для автоматического
> продления. Если правило сбросится после перезагрузки Москвы — продление упадёт
> и сертификат истечёт через ~90 дней.

---

## Проверка после деплоя

```bash
# 1. Docker-сервисы
docker compose ps
# Ожидается: nginx, backend, mtg-default, docker-socket-proxy — Up

# 2. Backend health
curl http://localhost:8080/healthz
# Ожидается: {"status":"ok"}

# 3. Панель по TLS (после выдачи сертификата)
curl -v https://mtg.yourdomain.ru/healthz
# Ожидается: HTTP/2 200, сертификат от Let's Encrypt

# 4. Сканер без SNI → mtg (не панель)
openssl s_client -connect MOSCOW_IP:443
# Ожидается: fake-TLS handshake (от mtg), не ответ панели

# 5. Ротация логов
docker inspect mtg-orchestrator-nginx-1 | grep -A5 LogConfig
# Ожидается: "max-size": "10m", "max-file": "3"

# 6. Изоляция сети
docker network inspect mtg-orchestrator_socket-net | grep Internal
# Ожидается: "Internal": true
```

---

## Структура проекта

```
.
├── backend/               FastAPI + Uvicorn (приложение панели)
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── app/
│       ├── main.py        /healthz + StaticFiles для SPA
│       └── static/
│           └── index.html  (placeholder — Phase 3 заменит на Vue 3)
├── infra/
│   ├── nginx/
│   │   └── nginx.conf     SNI mux (stream:443) + ACME handler (http:80)
│   └── mtg/
│       └── default.config.toml  Конфиг mtg-default (маска-домен)
├── docker-compose.yml     Все статические сервисы
├── .env.example           Шаблон конфигурации (без секретов)
└── README.md
```

---

## Безопасность

- **Docker-сокет**: только `docker-socket-proxy` монтирует `/var/run/docker.sock`.
  Backend использует `tcp://docker-socket-proxy:2375` (минимальные права).
- **Сети**: `socket-net` — `internal: true`. nginx и mtg-контейнеры не имеют доступа к Docker API.
- **SNI-маскировка**: `default` в nginx map → mtg (fake-TLS). Сканеры видят прокси, а не панель.
- **Секреты**: `.env`, сертификаты, ключи — в `.gitignore`. Никогда не попадают в git.
- **Порт 443**: единственный публичный вход для пользователей панели и MTProto-клиентов.
  Порт 80 — только для ACME HTTP-01; не является пользовательской поверхностью.

---

## Разработка (Windows)

```powershell
# Проверить конфигурацию compose без запуска
docker compose --env-file .env config

# Запустить только nginx для проверки конфига
docker run --rm -v "${PWD}/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" nginx:1.27-alpine nginx -t
```

---

*Phase 1: Infrastructure + TLS baseline. Phase 3 adds Telegram bot, Mini App, and full panel UI.*
