#!/usr/bin/env bash
# =============================================================================
# verify-phase3.sh — MTG Orchestrator Phase 3 live functional verification
#
# Phase 3 adds: Telegram-initData auth guard, the aiogram webhook bot
# (/start + /proxies), the SSE create-progress + default-proxy API, the Vue
# Mini App (served as static from the backend image), and the bundle secret
# boundary.
#
# This script drives the REAL stack on the Frankfurt VPS and exercises every
# Phase 3 behavior that can be asserted from scripts/requests:
#   - backend health + stack up
#   - Mini App static is served at GET /
#   - auth matrix (no header / owner / expired / tampered / non-owner) on a
#     protected route — forges valid+invalid Telegram initData with the real
#     BOT_TOKEN inside the backend container
#   - default-proxy endpoint (read-only mtg-default)
#   - SSE create stream: success (creates+cleans a real proxy), invalid-domain
#     error event, and unauthenticated 403
#   - bot webhook security gate (wrong secret 403, correct secret 200)
#   - Telegram webhook REGISTRATION path (getWebhookInfo) — catches the
#     /bot/webhook vs /api/v1/bot/webhook mismatch that makes the bot dead in
#     prod even though unit tests pass
#   - static bundle secret boundary (no BOT_TOKEN / WEBHOOK_SECRET in assets)
#
# Mini App UI behavior (screens, tg:// copy, MainButton, theme, decoy UX) is
# NOT scriptable — see scripts/verify-phase3-manual.md for the human checklist.
#
# All API calls go to the backend's INTERNAL port (https://localhost:8443, then
# http://localhost:8080 fallback) from INSIDE the backend container via
# `docker compose exec -T backend python`, because the image has no curl and
# the external panel TLS path is environment-dependent.
#
# Usage:
#   bash scripts/verify-phase3.sh                 # interactive, no rebuild
#   bash scripts/verify-phase3.sh --build         # git pull + build + up -d first
#   bash scripts/verify-phase3.sh --yes           # non-interactive (assume yes)
#   bash scripts/verify-phase3.sh --domain a.ru   # SSE create test domain
#   bash scripts/verify-phase3.sh --keep          # keep the SSE-created proxy
#   bash scripts/verify-phase3.sh --build --yes   # full unattended run
#
# Exit code 0 if all checks pass, 1 otherwise. Secrets are masked in output.
# =============================================================================

set -uo pipefail   # NOT -e: run every check and count failures

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------
DO_BUILD=false
ASSUME_YES=false
KEEP=false
TEST_DOMAIN="ria.ru"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build) DO_BUILD=true; shift ;;
        --yes|-y) ASSUME_YES=true; shift ;;
        --keep) KEEP=true; shift ;;
        --domain) TEST_DOMAIN="${2:?--domain needs a value}"; shift 2 ;;
        --domain=*) TEST_DOMAIN="${1#*=}"; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

# ---------------------------------------------------------------------------
# Load .env strictly (KEY=VALUE only; never source). Mirrors verify-phase2.sh.
# ---------------------------------------------------------------------------
load_env() {
    local env_path="$1" line key val
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^[[:space:]]*$ ]] && continue
        if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"; val="${BASH_REMATCH[2]}"
            if [[ "$val" =~ ^\"(.*)\"$ ]] || [[ "$val" =~ ^\'(.*)\'$ ]]; then
                val="${BASH_REMATCH[1]}"
            fi
            export "$key=$val"
        fi
    done < "$env_path"
}
[[ -f "$REPO_DIR/.env" ]] && load_env "$REPO_DIR/.env"
# DOCKER_HOST in .env points the BACKEND at the socket-proxy; it must NOT
# redirect this script's own docker CLI (which talks to the local daemon).
unset DOCKER_HOST

MOSCOW_IP="${MOSCOW_IP:-}"
PANEL_DOMAIN="${PANEL_DOMAIN:-}"

# ---------------------------------------------------------------------------
# Compose command detection
# ---------------------------------------------------------------------------
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "ERROR: docker compose not found" >&2; exit 2
fi

# ---------------------------------------------------------------------------
# Output helpers + counters
# ---------------------------------------------------------------------------
PASSED=0; FAILED=0; SKIPPED=0
pass() { echo "  PASS  $1"; PASSED=$(( PASSED + 1 )); }
fail() { echo "  FAIL  $1"; [[ -n "${2:-}" ]] && echo "        $2"; FAILED=$(( FAILED + 1 )); }
skip() { echo "  SKIP  $1 (${2:-N/A})"; SKIPPED=$(( SKIPPED + 1 )); }
info() { echo "        $1"; }
hdr()  { echo ""; echo "[ $1 ] $2"; }

confirm() {
    $ASSUME_YES && return 0
    local ans
    read -r -p "        $1 [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]]
}

mask() { sed -E 's/(secret=ee)[0-9a-fA-F]+/\1***MASKED***/g; s/("secret":[[:space:]]*")[^"]*/\1***MASKED***/g; s/(bot)[0-9]+:[A-Za-z0-9_-]+/\1***MASKED***/g'; }

# ---------------------------------------------------------------------------
# Unified API client — runs INSIDE the backend container so it can read the
# real BOT_TOKEN / OWNER_USER_ID / WEBHOOK_SECRET env and forge initData.
#
#   api METHOD PATH BODY MODE
#
# MODE controls the auth header:
#   none           -> no Authorization (expect 403 on protected routes)
#   owner          -> valid owner initData (expect 200)
#   expired        -> valid HMAC but auth_date 400s old (expect 403)
#   tampered       -> valid initData with one hash char flipped (expect 403)
#   nonowner       -> valid HMAC for a NON-owner user id (expect 403)
#   webhook_ok     -> X-Telegram-Bot-Api-Secret-Token = real WEBHOOK_SECRET
#   webhook_wrong  -> X-Telegram-Bot-Api-Secret-Token = wrong value
#
# Prints: line 1 = HTTP status (000 on transport error), rest = body.
# ---------------------------------------------------------------------------
api() {
    local method="$1" path="$2" body="${3:-}" mode="${4:-none}"
    $COMPOSE exec -T backend python - "$method" "$path" "$body" "$mode" <<'PYEOF'
import sys, os, ssl, time, json, hmac, hashlib
import urllib.request, urllib.error
from urllib.parse import quote, urlencode

method, path, body, mode = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

bot_token = os.environ.get("BOT_TOKEN", "")
owner_id  = int(os.environ.get("OWNER_USER_ID", "0") or "0")
webhook_secret = os.environ.get("WEBHOOK_SECRET", "")


def build_init_data(*, user_id, auth_date, tamper=False):
    params = {
        "auth_date": str(auth_date),
        "query_id": "AAEAAAE",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if tamper:
        # flip the first hex char so the HMAC no longer matches
        h = ("1" if h[0] == "0" else "0") + h[1:]
    params["hash"] = h
    return urlencode(params, quote_via=quote)


now = int(time.time())
headers = {}
if body:
    headers["Content-Type"] = "application/json"

if mode == "owner":
    headers["Authorization"] = "tma " + build_init_data(user_id=owner_id, auth_date=now)
elif mode == "expired":
    headers["Authorization"] = "tma " + build_init_data(user_id=owner_id, auth_date=now - 400)
elif mode == "tampered":
    headers["Authorization"] = "tma " + build_init_data(user_id=owner_id, auth_date=now, tamper=True)
elif mode == "nonowner":
    headers["Authorization"] = "tma " + build_init_data(user_id=owner_id + 9999, auth_date=now)
elif mode == "webhook_ok":
    headers["X-Telegram-Bot-Api-Secret-Token"] = webhook_secret
elif mode == "webhook_wrong":
    headers["X-Telegram-Bot-Api-Secret-Token"] = "definitely-not-the-secret"
# mode == "none" -> send nothing

data = body.encode() if body else None
ctx = ssl._create_unverified_context()
last = None
for base in ("https://localhost:8443", "http://localhost:8080"):
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    kw = {"timeout": 90}
    if base.startswith("https"):
        kw["context"] = ctx
    try:
        r = urllib.request.urlopen(req, **kw)
        sys.stdout.write(str(r.status) + "\n")
        sys.stdout.write(r.read().decode(errors="replace"))
        sys.exit(0)
    except urllib.error.HTTPError as e:
        sys.stdout.write(str(e.code) + "\n")
        sys.stdout.write(e.read().decode(errors="replace"))
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        last = e
        continue
sys.stdout.write("000\n")
sys.stdout.write("TRANSPORT ERROR: %r" % last)
PYEOF
}
status_of() { printf '%s' "$1" | head -n1; }
body_of()   { printf '%s' "$1" | tail -n +2; }

jget() { python3 -c 'import sys,json; d=json.loads(sys.stdin.read() or "null"); print(eval(sys.argv[1]))' "$2" <<<"$1" 2>/dev/null; }

wait_backend() {
    local i
    for i in $(seq 1 30); do
        [[ "$(status_of "$(api GET /healthz "" none)")" == "200" ]] && return 0
        sleep 1
    done
    return 1
}

echo ""
echo "=== MTG Orchestrator Phase 3 — Live Functional Verification ==="
echo "Repo:         $REPO_DIR"
echo "Compose:      $COMPOSE"
echo "Panel domain: ${PANEL_DOMAIN:-<unset>}"
echo "SSE domain:   $TEST_DOMAIN"
echo "Mode:         build=$DO_BUILD  assume-yes=$ASSUME_YES  keep=$KEEP"
if ! $ASSUME_YES; then
    echo ""
    echo "This forges Telegram initData with your real BOT_TOKEN (inside the"
    echo "backend container) and creates + DELETES one real mtg proxy for the"
    echo "SSE test. It does NOT touch your mtg-default masquerade proxy."
    confirm "Proceed?" || { echo "Aborted."; exit 0; }
fi

# ===========================================================================
# 0. Optional: pull + build + restart
# ===========================================================================
if $DO_BUILD; then
    hdr 0 "Deploy (git pull + build + up -d)"
    if confirm "git fetch && git pull --ff-only origin main ?"; then
        git fetch origin && git pull --ff-only origin main && pass "git pull --ff-only" \
            || fail "git pull --ff-only" "resolve manually (local changes / divergence)"
    else
        skip "git pull" "declined"
    fi
    info "HEAD: $(git rev-parse --short HEAD 2>/dev/null)"
    # Phase 3 frontend is built INTO the backend image (node builder stage),
    # so the backend image must be rebuilt to pick up Mini App changes.
    $COMPOSE build backend && pass "docker compose build backend" || fail "docker compose build backend"
    $COMPOSE up -d && pass "docker compose up -d (full stack)" || fail "docker compose up -d"
fi

# ===========================================================================
# 0b. Backend readiness
# ===========================================================================
hdr 0b "Backend readiness"
if wait_backend; then
    pass "backend listening (/healthz 200)"
else
    fail "backend not ready after 30s" "$COMPOSE logs --tail=80 backend"
fi

# ===========================================================================
# 1. Stack services up
# ===========================================================================
hdr 1 "Stack services"
PS="$($COMPOSE ps 2>/dev/null)"
for svc in nginx backend mtg-default docker-socket-proxy; do
    if printf '%s' "$PS" | grep -E "\b${svc}\b" | grep -qiE 'up|running'; then
        pass "service $svc is up"
    else
        fail "service $svc not up" "$COMPOSE ps"
    fi
done

# ===========================================================================
# 2. Backend health
# ===========================================================================
hdr 2 "Backend health"
R="$(api GET /healthz "" none)"
if [[ "$(status_of "$R")" == "200" ]] && printf '%s' "$(body_of "$R")" | grep -q '"status":"ok"'; then
    pass "GET /healthz -> {\"status\":\"ok\"}"
else
    fail "GET /healthz" "$(body_of "$R")"
fi

# ===========================================================================
# 3. Mini App static served (Manual UAT precondition)
# ===========================================================================
hdr 3 "Mini App static assets served at GET /"
R="$(api GET / "" none)"
ST="$(status_of "$R")"; BODY="$(body_of "$R")"
if [[ "$ST" == "200" ]] && printf '%s' "$BODY" | grep -qiE '<div id="?app"?|<script[^>]*type="module"|/assets/'; then
    pass "GET / serves the built Vue Mini App index"
else
    fail "GET / did not return the Mini App index" "status=$ST"
fi
# index.html + assets/ must be present in the image's static dir (built in CI stage)
if $COMPOSE exec -T backend sh -c 'test -f /backend/app/static/index.html && test -d /backend/app/static/assets' 2>/dev/null; then
    pass "container has /backend/app/static/index.html + assets/"
    info "assets: $($COMPOSE exec -T backend sh -c 'ls /backend/app/static/assets 2>/dev/null | head -3' | tr '\n' ' ')"
else
    fail "Mini App static not packaged into backend image" "expected /backend/app/static/{index.html,assets/}"
fi

# ===========================================================================
# 4. Auth matrix on a protected route (AUTH-01/02/03)
# ===========================================================================
hdr 4 "Telegram initData auth guard (AUTH-01/02/03)"
P="/api/v1/instances/"
declare -A EXP=( [none]=403 [expired]=403 [tampered]=403 [nonowner]=403 [owner]=200 )
for mode in none expired tampered nonowner owner; do
    R="$(api GET "$P" "" "$mode")"; ST="$(status_of "$R")"
    case "$mode" in
        none)     LABEL="no Authorization header -> 403" ;;
        expired)  LABEL="auth_date > 300s old -> 403" ;;
        tampered) LABEL="tampered HMAC -> 403" ;;
        nonowner) LABEL="valid HMAC, non-owner user -> 403" ;;
        owner)    LABEL="valid owner initData -> 200" ;;
    esac
    if [[ "$ST" == "${EXP[$mode]}" ]]; then
        pass "$LABEL"
    else
        fail "$LABEL" "got status=$ST"
        if [[ "$mode" == "owner" && "$ST" == "403" ]]; then
            info "owner rejected: check BOT_TOKEN/OWNER_USER_ID in .env match the real bot+owner,"
            info "and that DEV_MOCK_INIT_DATA is NOT set in production."
        fi
    fi
done

# ===========================================================================
# 5. Default proxy endpoint (read-only mtg-default)
# ===========================================================================
hdr 5 "Default proxy endpoint"
R="$(api GET /api/v1/instances/default "" owner)"; ST="$(status_of "$R")"; BODY="$(body_of "$R")"
if [[ "$ST" == "200" ]]; then
    pass "GET /api/v1/instances/default -> 200 (owner)"
    if printf '%s' "$BODY" | grep -q '"read_only":[[:space:]]*true' && printf '%s' "$BODY" | grep -q '"id":[[:space:]]*-1'; then
        pass "default is id=-1, read_only=true"
    else
        fail "default payload shape" "$(printf '%s' "$BODY" | mask)"
    fi
    if printf '%s' "$BODY" | grep -qE '"tg_url":[[:space:]]*"tg://proxy'; then
        pass "default exposes a tg:// link (MTG_DEFAULT_SECRET set)"
    else
        info "default tg_url empty — MTG_DEFAULT_SECRET unset or not ee-prefixed (OK if intended)"
    fi
else
    fail "GET /instances/default" "status=$ST $(printf '%s' "$BODY" | mask)"
fi
# unauthenticated default must be refused
R="$(api GET /api/v1/instances/default "" none)"
[[ "$(status_of "$R")" == "403" ]] && pass "default unauthenticated -> 403" || fail "default unauthenticated" "got $(status_of "$R")"

# ===========================================================================
# 6. SSE create stream — success (creates a REAL proxy, then cleans up)
# ===========================================================================
hdr 6 "SSE create stream — success (AUTH-01/02/03 + create flow)"
# drop any leftover instance for this domain first (clean slate)
PREID="$(jget "$(body_of "$(api GET /api/v1/instances/ '' owner)")" "next((str(r['id']) for r in (d or []) if r.get('domain')=='$TEST_DOMAIN'), '')")"
if [[ -n "$PREID" ]]; then
    info "removing leftover instance id=$PREID for $TEST_DOMAIN"
    api DELETE "/api/v1/instances/$PREID" "" owner >/dev/null
fi
R="$(api POST /api/v1/instances/create/stream "{\"domain\":\"$TEST_DOMAIN\"}" owner)"
ST="$(status_of "$R")"; BODY="$(body_of "$R")"
if [[ "$ST" == "200" ]]; then
    pass "POST /instances/create/stream -> 200 (owner)"
    printf '%s' "$BODY" | grep -q 'event: progress' && pass "stream emits progress events" || fail "no progress events" "$(printf '%s' "$BODY" | head -3)"
    printf '%s' "$BODY" | grep -q 'event: done'     && pass "stream emits terminal done event" || fail "no done event" "$(printf '%s' "$BODY" | tail -3)"
    printf '%s' "$BODY" | grep -qE 'tg://proxy'     && pass "done event carries a tg:// link" || info "no tg:// in stream (verify intent)"
else
    fail "SSE create stream (expected 200)" "status=$ST $(printf '%s' "$BODY" | mask | head -5)"
fi
# cleanup the created proxy unless --keep
NEWID="$(jget "$(body_of "$(api GET /api/v1/instances/ '' owner)")" "next((str(r['id']) for r in (d or []) if r.get('domain')=='$TEST_DOMAIN'), '')")"
if $KEEP; then
    info "--keep: leaving instance id=${NEWID:-?} ($TEST_DOMAIN) in place"
elif [[ -n "$NEWID" ]]; then
    if [[ "$(status_of "$(api DELETE "/api/v1/instances/$NEWID" '' owner)")" == "204" ]]; then
        pass "cleanup: deleted SSE-created instance id=$NEWID"
    else
        fail "cleanup delete failed for id=$NEWID" "remove manually"
    fi
fi

# ===========================================================================
# 7. SSE create stream — invalid domain emits error event
# ===========================================================================
hdr 7 "SSE create stream — invalid domain -> error event"
R="$(api POST /api/v1/instances/create/stream '{"domain":"localhost"}' owner)"
BODY="$(body_of "$R")"
if printf '%s' "$BODY" | grep -q 'event: error'; then
    pass "invalid domain (localhost) -> error event"
else
    fail "invalid domain did not emit error event" "$(printf '%s' "$BODY" | head -5)"
fi
# ensure no localhost row leaked
if printf '%s' "$(body_of "$(api GET /api/v1/instances/ '' owner)")" | grep -q '"domain":[[:space:]]*"localhost"'; then
    fail "localhost row leaked into registry (rollback failed)"
else
    pass "no localhost row leaked (clean rollback)"
fi

# ===========================================================================
# 8. SSE create stream — unauthenticated rejected
# ===========================================================================
hdr 8 "SSE create stream — unauthenticated -> 403"
R="$(api POST /api/v1/instances/create/stream "{\"domain\":\"$TEST_DOMAIN\"}" none)"
[[ "$(status_of "$R")" == "403" ]] && pass "unauthenticated create stream -> 403" || fail "unauthenticated create stream" "got $(status_of "$R")"

# ===========================================================================
# 9. Bot webhook security gate (BOT-01/02)
# ===========================================================================
hdr 9 "Bot webhook secret gate"
R="$(api POST /api/v1/bot/webhook '{"update_id":1}' webhook_wrong)"
[[ "$(status_of "$R")" == "403" ]] && pass "wrong secret token -> 403" || fail "wrong secret token" "got $(status_of "$R")"
# correct secret + an EMPTY update (no message) dispatches with no Telegram side effect
R="$(api POST /api/v1/bot/webhook '{"update_id":1}' webhook_ok)"
ST="$(status_of "$R")"
if [[ "$ST" == "200" ]]; then
    pass "correct secret token -> 200 (update dispatched)"
elif [[ -z "${WEBHOOK_SECRET:-}" ]]; then
    skip "correct secret dispatch" "WEBHOOK_SECRET unset in env"
else
    fail "correct secret token dispatch" "got status=$ST $(body_of "$R")"
fi

# ===========================================================================
# 10. Telegram webhook REGISTRATION path (catches the dead-bot prod bug)
# ===========================================================================
hdr 10 "Telegram webhook registration path (getWebhookInfo)"
# The bot must be REGISTERED at the route FastAPI actually serves:
#   route:   /api/v1/bot/webhook   (api_router mounted at /api/v1)
# If main.py set_webhook used a different path, Telegram posts into the static
# SPA mount and /start + /proxies silently never fire. Unit tests miss this
# because they POST the correct path directly.
EXPECTED_WH_PATH="/api/v1/bot/webhook"
WHINFO="$($COMPOSE exec -T backend python - <<'PYEOF'
import os, json, ssl, urllib.request
tok = os.environ.get("BOT_TOKEN", "")
if not tok:
    print("NO_TOKEN"); raise SystemExit
try:
    ctx = ssl.create_default_context()
    r = urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getWebhookInfo", timeout=20, context=ctx)
    d = json.loads(r.read().decode())
    print(d.get("result", {}).get("url", ""))
except Exception as e:  # noqa: BLE001
    print("ERR:%r" % e)
PYEOF
)"
WHINFO="$(printf '%s' "$WHINFO" | tr -d '\r')"
if [[ "$WHINFO" == "NO_TOKEN" ]]; then
    skip "webhook registration" "BOT_TOKEN unset"
elif [[ "$WHINFO" == ERR:* || -z "$WHINFO" ]]; then
    skip "webhook registration" "getWebhookInfo unreachable: ${WHINFO:-empty}"
else
    info "registered url: $(printf '%s' "$WHINFO" | sed -E 's#(https?://[^/]+).*#\1...#')"
    WH_PATH="$(printf '%s' "$WHINFO" | sed -E 's#https?://[^/]+##')"
    if [[ "$WH_PATH" == "$EXPECTED_WH_PATH" ]]; then
        pass "webhook registered at $EXPECTED_WH_PATH (bot will receive updates)"
    else
        fail "webhook registered at '$WH_PATH', backend serves '$EXPECTED_WH_PATH'" \
             "BUG: Telegram posts to a path the backend does not route -> /start and /proxies are DEAD in prod."
        info "Fix: backend/app/main.py set_webhook url must be https://{panel}${EXPECTED_WH_PATH}"
    fi
    if [[ -n "$PANEL_DOMAIN" ]] && ! printf '%s' "$WHINFO" | grep -q "$PANEL_DOMAIN"; then
        info "note: registered host does not contain PANEL_DOMAIN ($PANEL_DOMAIN)"
    fi
fi

# ===========================================================================
# 11. Static bundle secret boundary (SC-5)
# ===========================================================================
hdr 11 "Static bundle secret boundary (SC-5)"
SCAN="$($COMPOSE exec -T backend python - <<'PYEOF'
import os, pathlib
needles = {k: os.environ.get(k, "") for k in ("BOT_TOKEN", "WEBHOOK_SECRET")}
needles = {k: v for k, v in needles.items() if v}
root = pathlib.Path("/backend/app/static")
hits = []
if not root.exists():
    print("NO_STATIC"); raise SystemExit
for p in root.rglob("*"):
    if not p.is_file():
        continue
    try:
        text = p.read_text(errors="ignore")
    except Exception:  # noqa: BLE001
        continue
    for name, val in needles.items():
        if val and val in text:
            hits.append(f"{name} in {p}")
print("OK" if not hits else "LEAK:" + "; ".join(hits))
PYEOF
)"
SCAN="$(printf '%s' "$SCAN" | tr -d '\r')"
case "$SCAN" in
    OK)         pass "no BOT_TOKEN / WEBHOOK_SECRET in served static assets" ;;
    NO_STATIC)  fail "static dir missing in backend image" "/backend/app/static absent" ;;
    LEAK:*)     fail "SECRET LEAKED into static bundle" "${SCAN#LEAK:}" ;;
    *)          skip "bundle secret scan" "unexpected output: $SCAN" ;;
esac

# ===========================================================================
# Summary
# ===========================================================================
echo ""
echo "=== Summary ==="
echo "  Passed:  $PASSED"
echo "  Failed:  $FAILED"
echo "  Skipped: $SKIPPED"
echo ""
echo "Manual Mini App checks (screens, tg:// copy, MainButton, theme, decoy UX):"
echo "  see scripts/verify-phase3-manual.md"
echo ""
if [[ $FAILED -gt 0 ]]; then
    echo "RESULT: FAILED ($FAILED failure(s)) — paste this output back for diagnosis"
    exit 1
else
    echo "RESULT: PASSED"
    exit 0
fi
