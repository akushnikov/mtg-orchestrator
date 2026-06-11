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
│  mtg-default (fake-TLS, всегда активен)           │
│  certbot (Let's Encrypt, запускается вручную)     │
│  docker-socket-proxy (граница Docker API)         │
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
- На Франкфурте: Docker + docker-compose v2, git.
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
# MSS clamping для предотвращения фрагментации (Pitfall 14)
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# Сохранить правила
iptables-save > /etc/iptables/rules.v4
```

> **Важно:** порт 80 должен быть проброшен до первого запуска certbot. Без него
> HTTP-01 challenge не пройдёт.

**Проверить MTU (с московского VPS):**

```bash
# Если эта команда падает с "Frag needed" — добавьте явный MSS clamp: --set-mss 1400
ping -M do -s 1472 <FRANKFURT_IP>
```

### 3. Настройка конфигурации (Франкфурт)

```bash
git clone https://github.com/your-org/mtg-orchestrator
cd mtg-orchestrator

# Скопировать шаблон и заполнить реальными значениями
cp .env.example .env
nano .env  # или любой текстовый редактор
```

Заполнить в `.env`:

| Переменная | Описание | Пример |
|---|---|---|
| `MOSCOW_IP` | Публичный IP московского VPS | `1.2.3.4` |
| `FRANKFURT_IP` | Публичный IP Франкфурта | `5.6.7.8` |
| `PANEL_DOMAIN` | Поддомен панели (A-запись → московский IP) | `mtg.yourdomain.ru` |
| `LE_EMAIL` | Email для Let's Encrypt | `admin@yourdomain.ru` |
| `MTG_DEFAULT_SECRET` | Секрет существующего mtg (`ee…`) | см. ниже |
| `MTG_DEFAULT_DOMAIN` | Маскировочный домен | `ria.ru` |
| `BOT_TOKEN` | Токен Telegram-бота от @BotFather | `123456:AAA…` |
| `OWNER_USER_ID` | Ваш Telegram user ID | `123456789` |
| `WEBHOOK_SECRET` | Случайная строка для webhook | (случайная) |

> **Никогда не коммитьте `.env`** — файл добавлен в `.gitignore`.

#### Как получить MTG_DEFAULT_SECRET из работающего VPS

Если у вас уже запущен `mtg` на VPS, выполните одно из:

```bash
# Вариант 1: из compose env
docker inspect mtg-default | grep -i secret

# Вариант 2: из командной строки контейнера
docker inspect mtg-default | grep Cmd

# Вариант 3: из конфиг-файла внутри контейнера
docker exec mtg-default cat /config.toml 2>/dev/null | grep secret

# Если mtg запускался с флагом generate-secret:
# mtg generate-secret --hex ria.ru
# Формат: ee<32 hex символа ключа><hex(domain)>
# Пример для ria.ru: ee<key>7269612e7275
```

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

### 5. Статическая проверка конфигурации

**На Windows (перед деплоем):**

```powershell
.\scripts\verify-infra.ps1
# Все 40+ проверок должны пройти (PASS) перед деплоем
```

**На VPS (статические проверки без сети):**

```bash
bash scripts/verify-infra.sh
```

### 6. Проверить DNS и порт 80

Перед запуском certbot убедитесь:

```bash
# Проверить, что A-запись domain указывает на московский IP
nslookup mtg.yourdomain.ru

# Проверить, что порт 80 пробрасывается с Москвы на Франкфурт
# (запустить с ВНЕШНЕЙ машины, НЕ с московского VPS)
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

Запустить полную автоматизированную проверку на VPS:

```bash
source .env
bash scripts/verify-infra.sh --live
```

Или выполнить ручные проверки по шагам:

```bash
# 1. Docker-сервисы
docker compose ps
# Ожидается: nginx, backend, mtg-default, docker-socket-proxy — Up

# 2. Backend health
curl -s http://localhost:8080/healthz
# Ожидается: {"status":"ok"}

# 3. Панель по TLS (после выдачи сертификата)
# Замените mtg.yourdomain.ru на ваш PANEL_DOMAIN из .env
curl -v https://mtg.yourdomain.ru/healthz
# Ожидается: HTTP/2 200, сертификат от Let's Encrypt

# 4. Сканер без SNI → mtg (не панель)
# ОБЯЗАТЕЛЬНО: панель НЕ должна отвечать на пустой SNI
openssl s_client -connect MOSCOW_IP:443 </dev/null 2>&1 | head -20
# Ожидается: fake-TLS handshake (от mtg), NOT сертификат панели
# Сертификат Let's Encrypt для PANEL_DOMAIN НЕ должен появляться здесь

# 5. Панель по точному SNI
openssl s_client -connect MOSCOW_IP:443 -servername mtg.yourdomain.ru </dev/null 2>&1 | head -20
# Ожидается: действительный сертификат Let's Encrypt для PANEL_DOMAIN

# 6. Ротация логов
docker inspect mtg-orchestrator-nginx-1 | grep -A5 LogConfig
# Ожидается: "max-size": "10m", "max-file": "3"

# 7. Изоляция сети
docker network inspect mtg-orchestrator_socket-net | grep Internal
# Ожидается: "Internal": true

# 8. backend не имеет прямого доступа к docker.sock
docker inspect mtg-orchestrator-backend-1 | grep docker.sock
# Ожидается: пустой вывод
```

---

## Устранение неисправностей

### HTTP-01 challenge падает (certbot ошибка)

```bash
# 1. Убедитесь, что nginx запущен
docker compose ps nginx

# 2. Проверьте доступность порта 80 с ВНЕШНЕЙ машины (не с Москвы)
curl -v http://${PANEL_DOMAIN}/.well-known/acme-challenge/test
# 404 — нормально, соединение дошло до Франкфурта

# 3. Проверьте DNAT на Москве
iptables -t nat -L PREROUTING -n | grep 80

# 4. Проверьте, что Москва видит Франкфурт
curl -v http://localhost:80/.well-known/acme-challenge/test  # с Франкфурта
```

### mtg-default не запускается

```bash
docker compose logs mtg-default

# Проверьте формат MTG_DEFAULT_SECRET (должен начинаться с 'ee'):
source .env && echo ${MTG_DEFAULT_SECRET:0:2}
# Ожидается: ee

# Длина секрета должна быть минимум 36 символов (2 + 32 + 2 для однобуквенного домена):
echo ${#MTG_DEFAULT_SECRET}
```

### Соединение с прокси не устанавливается

```bash
# Проверьте, что masquerade domain доступен с Франкфурта
source .env && curl -v https://${MTG_DEFAULT_DOMAIN}
# Должен установиться TLS-сеанс

# Проверьте, что mtg логи не показывают blocklist ошибок
docker compose logs mtg-default | grep -i block

# Проверьте MSS clamping на Москве
ping -M do -s 1472 <FRANKFURT_IP>
```

### nginx не маршрутизирует по SNI

```bash
# Проверьте nginx конфиг
docker compose exec nginx nginx -t
docker compose logs nginx

# Проверьте resolver
docker compose exec nginx grep -r resolver /etc/nginx/
# Ожидается: resolver 127.0.0.11 valid=10s ipv6=off;
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
│       └── default.config.toml  Конфиг mtg-default (для Phase 2+)
├── scripts/
│   ├── verify-infra.ps1   Статическая проверка (Windows/PowerShell)
│   └── verify-infra.sh    Статическая + live-проверка (Linux/VPS)
├── docker-compose.yml     Все статические сервисы Phase 1
├── .env.example           Шаблон конфигурации (без секретов)
└── README.md
```

---

## Безопасность

- **Docker-сокет**: только `docker-socket-proxy` монтирует `/var/run/docker.sock`.
  Backend использует `tcp://docker-socket-proxy:2375` с минимальными правами
  (`CONTAINERS=1, NETWORKS=1, POST=1` — всё остальное заблокировано).
- **Сети**: `socket-net` — `internal: true`. nginx и mtg-контейнеры не имеют маршрута
  к Docker socket proxy.
- **SNI-маскировка**: `default` в nginx map → mtg (fake-TLS). Сканеры видят прокси, а не панель.
  Пустой SNI и неизвестные домены всегда попадают в mtg — не в панель.
- **Секреты**: `.env`, сертификаты, ключи — в `.gitignore`. Никогда не попадают в git.
- **Порт 443**: единственный публичный вход для пользователей панели и MTProto-клиентов.
  Порт 80 — только для ACME HTTP-01; не является пользовательской поверхностью.
- **Ротация логов**: все сервисы ограничены `max-size: 10m, max-file: 3` чтобы не
  переполнить диск (30 ГБ NVMe).

---

## Разработка (Windows)

```powershell
# Запустить статическую проверку (рекомендуется перед каждым деплоем)
.\scripts\verify-infra.ps1

# Проверить конфигурацию compose без запуска
docker compose --env-file .env config

# Проверить синтаксис nginx конфига
docker run --rm -v "${PWD}/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" nginx:1.27-alpine nginx -t
```

---

## Что входит в Phase 1 / что будет позже

**Phase 1 включает:**
- Docker Compose с 5 статическими сервисами (nginx, backend, mtg-default, certbot, docker-socket-proxy)
- nginx stream SNI mux: `:443` с `ssl_preread`, маршрутизация по SNI
- HTTP-01 ACME сертификат через certbot + webroot
- Compose-управляемый `mtg-default` (всегда активный дефолтный прокси)
- Безопасная конфигурация Docker сетей (proxy-net + socket-net internal)
- Ротация логов на всех сервисах
- Скрипты проверки (`scripts/verify-infra.*`)

**Phase 2+ (не входит в Phase 1):**
- Динамическое создание/удаление mtg-инстансов через Docker SDK
- SQLite-реестр прокси-инстансов
- Jinja2-рендеринг `nginx.conf` с перезагрузкой nginx
- Prometheus-метрики mtg и их скрейпинг

**Phase 3+ (не входит в Phase 1):**
- Telegram-бот (aiogram 3.x)
- Telegram Mini App (Vue 3 + Vant 4)
- Аутентификация через initData
- API управления прокси

**Phase 4+ (не входит в Phase 1):**
- Observability dashboard (метрики, логи)
- QR-коды для tg:// ссылок

---

*Phase 1: Infrastructure + TLS baseline (compose-managed default mtg, nginx SNI mux, certbot HTTP-01).*
*Phase 2: Backend orchestration (dynamic mtg lifecycle, SQLite registry, nginx config render).*
*Phase 3: Auth + Telegram bot + Mini App UI.*
*Phase 4: Observability.*
