#!/usr/bin/env bash
# =============================================================================
# verify-phase2.sh — MTG Orchestrator Phase 2 live functional verification
#
# Phase 2 is backend-only (proxy orchestration). This script drives the REAL
# stack on the Frankfurt VPS: it exercises create / list / stop / start /
# duplicate / invalid / restart-persistence / startup-reconcile / delete
# against the running backend, and cross-checks Docker + nginx side effects.
#
# All API calls go to the backend's INTERNAL http port (localhost:8080) from
# inside the backend container via `docker compose exec -T backend python`,
# because:
#   - the image has no curl/wget (python:3.12-slim), and
#   - the external panel path (:443 -> backend:8443 TLS) is a known broken
#     deployment gap (no 8443 listener wired in compose).
#
# Usage:
#   bash scripts/verify-phase2.sh                 # interactive, no rebuild
#   bash scripts/verify-phase2.sh --build         # git pull + build + up -d backend first
#   bash scripts/verify-phase2.sh --yes           # non-interactive (assume yes)
#   bash scripts/verify-phase2.sh --domain a.ru   # use a different test domain
#   bash scripts/verify-phase2.sh --keep          # don't delete the test instance at the end
#   bash scripts/verify-phase2.sh --build --yes   # full unattended run
#
# Flags can be combined. Exit code 0 if all checks pass, 1 otherwise.
#
# Secrets (mtg secret, tg_url tail) are masked in output.
# =============================================================================

set -uo pipefail   # NOT -e: we want to run every check and count failures
# NOTE: do NOT set IFS=$'\n\t' here — $COMPOSE holds "docker compose" (two
# words) and is invoked unquoted, so it must word-split on spaces.

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
# Load .env strictly (KEY=VALUE only; never source). Mirrors verify-infra.sh.
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
    # confirm "question" -> 0 if yes
    $ASSUME_YES && return 0
    local ans
    read -r -p "        $1 [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]]
}

mask() { sed -E 's/(secret=ee)[0-9a-fA-F]+/\1***MASKED***/g; s/("secret":[[:space:]]*")[^"]*/\1***MASKED***/g'; }

# ---------------------------------------------------------------------------
# API helper — calls backend internal :8080 from inside the backend container.
# Prints: line 1 = HTTP status (or 000 on transport error), rest = body.
# ---------------------------------------------------------------------------
api() {
    local method="$1" path="$2" body="${3:-}"
    $COMPOSE exec -T backend python - "$method" "$path" "$body" <<'PYEOF'
import sys, urllib.request, urllib.error
method, path = sys.argv[1], sys.argv[2]
body = sys.argv[3] if len(sys.argv) > 3 else ""
url = "http://localhost:8080" + path
data = body.encode() if body else None
headers = {"Content-Type": "application/json"} if data else {}
req = urllib.request.Request(url, data=data, method=method, headers=headers)
try:
    r = urllib.request.urlopen(req, timeout=60)
    sys.stdout.write(str(r.status) + "\n"); sys.stdout.write(r.read().decode())
except urllib.error.HTTPError as e:
    sys.stdout.write(str(e.code) + "\n"); sys.stdout.write(e.read().decode())
except Exception as e:
    sys.stdout.write("000\n"); sys.stdout.write("TRANSPORT ERROR: %r" % e)
PYEOF
}
status_of() { printf '%s' "$1" | head -n1; }
body_of()   { printf '%s' "$1" | tail -n +2; }

# Poll the backend until /healthz returns 200 (uvicorn needs a moment to bind
# after a recreate/restart). Returns 1 after ~30s.
wait_backend() {
    local i
    for i in $(seq 1 30); do
        [[ "$(status_of "$(api GET /healthz)")" == "200" ]] && return 0
        sleep 1
    done
    return 1
}

# JSON field extractor (host python3); usage: jget '<json>' 'expr using d'
jget() { python3 -c 'import sys,json; d=json.loads(sys.stdin.read() or "null"); print(eval(sys.argv[1]))' "$2" <<<"$1" 2>/dev/null; }

# slug == domain_to_slug(domain): lower, dots->_, non [a-z0-9_-]->_
slugify() { echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/\./_/g; s/[^a-z0-9_-]/_/g' | cut -c1-63; }

SLUG="$(slugify "$TEST_DOMAIN")"
CNAME="mtg-${SLUG}"
NGINX_VOL="$(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -Fx nginx-config \
            || docker volume ls --format '{{.Name}}' 2>/dev/null | grep -E 'nginx-config$' | head -n1)"

# nginx loads its active config from the shared volume at /data/nginx (nginx -c),
# NOT /etc/nginx — read the file nginx actually serves.
read_live_nginx()   { $COMPOSE exec -T nginx cat /data/nginx/nginx.conf 2>/dev/null; }
read_rendered_nginx(){ $COMPOSE exec -T backend cat /data/nginx/nginx.conf 2>/dev/null; }

echo ""
echo "=== MTG Orchestrator Phase 2 — Live Functional Verification ==="
echo "Repo:        $REPO_DIR"
echo "Compose:     $COMPOSE"
echo "Test domain: $TEST_DOMAIN  (slug=$SLUG, container=$CNAME)"
echo "nginx vol:   ${NGINX_VOL:-<none found>}"
echo "Mode:        build=$DO_BUILD  assume-yes=$ASSUME_YES  keep=$KEEP"
if ! $ASSUME_YES; then
    echo ""
    echo "This will create/stop/start/DELETE real mtg containers and restart"
    echo "the backend. It does NOT touch your mtg-default masquerade proxy."
    confirm "Proceed?" || { echo "Aborted."; exit 0; }
fi

# ===========================================================================
# 0. Optional: pull + build + restart backend
# ===========================================================================
if $DO_BUILD; then
    hdr 0 "Deploy (git pull + build backend + up -d)"
    if confirm "git fetch && git pull --ff-only origin main ?"; then
        git fetch origin && git pull --ff-only origin main && pass "git pull --ff-only" \
            || fail "git pull --ff-only" "resolve manually (local changes / divergence)"
    else
        skip "git pull" "declined"
    fi
    info "HEAD: $(git rev-parse --short HEAD 2>/dev/null)"
    $COMPOSE build backend && pass "docker compose build backend" || fail "docker compose build backend"
    # Bring up the WHOLE stack (not just backend): nginx config/volume/command
    # changed and nginx now waits on backend health, so it must be recreated too.
    $COMPOSE up -d && pass "docker compose up -d (full stack)" || fail "docker compose up -d"
fi

# ===========================================================================
# 0b. Wait for backend to accept connections (covers recreate race)
# ===========================================================================
hdr 0b "Backend readiness"
if wait_backend; then
    pass "backend listening on :8080"
else
    fail "backend not ready after 30s" "$COMPOSE logs --tail=80 backend"
fi

# ===========================================================================
# 1. Stack is up
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
# 2. Backend health (Test 1)
# ===========================================================================
hdr 2 "Backend health (UAT Test 1)"
R="$(api GET /healthz)"
if [[ "$(status_of "$R")" == "200" ]] && printf '%s' "$(body_of "$R")" | grep -q '"status":"ok"'; then
    pass "GET /healthz -> {\"status\":\"ok\"}"
else
    fail "GET /healthz" "$(body_of "$R")"
fi

# ===========================================================================
# 3. Baseline list (Test 4 baseline)
# ===========================================================================
hdr 3 "Baseline registry"
R="$(api GET /api/v1/instances/)"
if [[ "$(status_of "$R")" == "200" ]]; then
    pass "GET /api/v1/instances/ -> 200"
    info "current: $(body_of "$R" | mask)"
else
    fail "GET /api/v1/instances/" "$(body_of "$R")"
fi

# ===========================================================================
# 4. Create instance (Test 2)
# ===========================================================================
hdr 4 "Create instance (UAT Test 2)"
R="$(api POST /api/v1/instances/ "{\"domain\":\"$TEST_DOMAIN\"}")"
ST="$(status_of "$R")"; BODY="$(body_of "$R")"
INSTANCE_ID=""; ALLOC_PORT=""
if [[ "$ST" == "201" ]]; then
    pass "POST create -> 201"
    INSTANCE_ID="$(jget "$BODY" 'd.get("id","")')"
    ALLOC_PORT="$(jget "$BODY" 'd.get("port","")')"
    TG="$(jget "$BODY" 'd.get("tg_url","")')"
    info "id=$INSTANCE_ID port=$ALLOC_PORT tg_url=$(printf '%s' "$TG" | mask)"
    if printf '%s' "$TG" | grep -qE "^tg://proxy\?server=${MOSCOW_IP:-[0-9.]+}&port=443&secret=ee"; then
        pass "tg_url format (server=MOSCOW_IP, port=443, secret=ee...)"
    else
        fail "tg_url format" "got: $(printf '%s' "$TG" | mask)"
    fi
else
    fail "POST create (expected 201)" "status=$ST body=$(printf '%s' "$BODY" | mask)"
    if [[ "$ST" == "500" || "$ST" == "000" ]]; then
        echo "        ---- backend logs (tail 60) ----"
        $COMPOSE logs --tail=60 backend 2>&1 | mask | sed 's/^/        | /'
        echo "        --------------------------------"
    fi
fi

# ===========================================================================
# 5. mtg container created (Test 3a)
# ===========================================================================
hdr 5 "mtg container (UAT Test 3)"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CNAME"; then
    pass "container $CNAME is running"
else
    fail "container $CNAME not running" "docker ps | grep $CNAME"
fi

# ===========================================================================
# 6. nginx route — rendered vs LIVE (Test 3b)  [catches G4]
# ===========================================================================
hdr 6 "nginx route propagation (UAT Test 3 / Gap G4)"
RENDERED="$(read_rendered_nginx)"
LIVE="$(read_live_nginx)"
if printf '%s' "$RENDERED" | grep -q "$TEST_DOMAIN"; then
    pass "backend RENDERED a route for $TEST_DOMAIN (in nginx-config volume)"
    if [[ -n "$ALLOC_PORT" ]] && printf '%s' "$RENDERED" | grep "$TEST_DOMAIN" | grep -q ":$ALLOC_PORT"; then
        pass "rendered route uses allocated port :$ALLOC_PORT"
    fi
else
    fail "backend did not render a route for $TEST_DOMAIN" "checked /data/nginx/nginx.conf"
fi
if printf '%s' "$LIVE" | grep -q "$TEST_DOMAIN"; then
    pass "LIVE nginx serves a route for $TEST_DOMAIN"
else
    fail "LIVE nginx has NO route for $TEST_DOMAIN (Gap G4: nginx does not mount nginx-config)" \
         "running nginx /etc/nginx/nginx.conf lacks the dynamic route; SIGHUP reloaded the static bootstrap template"
fi

# ===========================================================================
# 7. List hides secret (Test 4)
# ===========================================================================
hdr 7 "List hides secret (UAT Test 4)"
R="$(api GET /api/v1/instances/)"; BODY="$(body_of "$R")"
if printf '%s' "$BODY" | grep -q "$TEST_DOMAIN"; then
    pass "instance listed"
    if printf '%s' "$BODY" | grep -q '"secret"'; then
        fail "list response EXPOSES secret field"
    else
        pass "no raw secret field in list"
    fi
    if printf '%s' "$BODY" | grep -qE '"tg_url":[[:space:]]*""'; then
        pass "tg_url empty in list"
    else
        info "note: tg_url not empty in list (verify intent)"
    fi
else
    fail "instance not in list" "$(printf '%s' "$BODY" | mask)"
fi

# ===========================================================================
# 8. Stop (Test 5)
# ===========================================================================
hdr 8 "Stop instance (UAT Test 5)"
if [[ -n "$INSTANCE_ID" ]]; then
    R="$(api PATCH "/api/v1/instances/$INSTANCE_ID/stop")"
    if [[ "$(status_of "$R")" == "200" ]] && printf '%s' "$(body_of "$R")" | grep -q '"status":"stopped"'; then
        pass "stop -> status=stopped"
    else
        fail "stop" "$(body_of "$R" | mask)"
    fi
    if read_live_nginx | grep -q "$TEST_DOMAIN"; then
        info "live nginx still has route after stop (expected removed; see G4 if route never propagated)"
    else
        pass "route absent from live nginx after stop"
    fi
    if docker ps -a --format '{{.Names}}' | grep -qx "$CNAME"; then
        pass "container row/object kept after stop"
    fi
else
    skip "stop" "no instance id"
fi

# ===========================================================================
# 9. Start (Test 6)
# ===========================================================================
hdr 9 "Start instance (UAT Test 6)"
if [[ -n "$INSTANCE_ID" ]]; then
    R="$(api PATCH "/api/v1/instances/$INSTANCE_ID/start")"
    if [[ "$(status_of "$R")" == "200" ]] && printf '%s' "$(body_of "$R")" | grep -q '"status":"running"'; then
        pass "start -> status=running"
        NEWP="$(jget "$(body_of "$R")" 'd.get("port","")')"
        [[ "$NEWP" == "$ALLOC_PORT" ]] && pass "same port reused ($NEWP)" || fail "port changed on start" "was $ALLOC_PORT now $NEWP"
    else
        fail "start" "$(body_of "$R" | mask)"
    fi
else
    skip "start" "no instance id"
fi

# ===========================================================================
# 10. Duplicate domain rejected (Test 7)
# ===========================================================================
hdr 10 "Duplicate domain rejected (UAT Test 7)"
R="$(api POST /api/v1/instances/ "{\"domain\":\"$TEST_DOMAIN\"}")"; ST="$(status_of "$R")"
if [[ "$ST" == "409" ]]; then
    pass "duplicate -> 409"
elif [[ "$ST" == "201" ]]; then
    fail "duplicate domain ACCEPTED (created second instance)"
else
    info "duplicate -> $ST (not 201 — acceptable if it is a rejection)"; pass "duplicate not accepted ($ST)"
fi

# ===========================================================================
# 11. Invalid / SSRF domain rejected (Test 8)
# ===========================================================================
hdr 11 "Invalid/SSRF domain rejected (UAT Test 8)"
R="$(api POST /api/v1/instances/ '{"domain":"localhost"}')"; ST="$(status_of "$R")"
if [[ "$ST" =~ ^4 ]]; then
    pass "SSRF/invalid (localhost) -> $ST (rejected)"
elif [[ "$ST" == "201" ]]; then
    fail "SSRF domain ACCEPTED (localhost created an instance!)"
else
    info "localhost -> $ST"; fail "unexpected status for localhost" "$ST"
fi
AFTER="$(body_of "$(api GET /api/v1/instances/)")"
if printf '%s' "$AFTER" | grep -q '"domain":[[:space:]]*"localhost"'; then
    fail "localhost row leaked into registry (rollback failed)"
else
    pass "no localhost row in registry (clean rollback)"
fi

# ===========================================================================
# 12. Restart persistence (Test 9)
# ===========================================================================
hdr 12 "Restart persistence (UAT Test 9)"
if confirm "Restart backend to test persistence?"; then
    $COMPOSE restart backend >/dev/null 2>&1
    wait_backend || info "backend slow to come back"
    if printf '%s' "$(body_of "$(api GET /api/v1/instances/)")" | grep -q "$TEST_DOMAIN"; then
        pass "instance survives backend restart"
    else
        fail "instance missing after restart"
    fi
else
    skip "restart persistence" "declined"
fi

# ===========================================================================
# 13. Startup reconciliation (Test 10)  [destructive to test container]
# ===========================================================================
hdr 13 "Startup reconciliation (UAT Test 10)"
if confirm "Kill $CNAME outside the app and restart backend to test reconcile?"; then
    docker rm -f "$CNAME" >/dev/null 2>&1
    $COMPOSE restart backend >/dev/null 2>&1
    wait_backend || info "backend slow to come back"
    BODY="$(body_of "$(api GET /api/v1/instances/)")"
    ST_FIELD="$(jget "$BODY" "[r.get('status') for r in (d or []) if r.get('domain')=='$TEST_DOMAIN']")"
    if printf '%s' "$ST_FIELD" | grep -q 'error'; then
        pass "stale running row reconciled to status=error"
    else
        fail "reconcile did not mark missing container as error" "domain status=$ST_FIELD"
    fi
else
    skip "reconcile" "declined"
fi

# ===========================================================================
# 14. Delete / cleanup (Test 11)
# ===========================================================================
hdr 14 "Delete instance / cleanup (UAT Test 11)"
if $KEEP; then
    skip "delete" "--keep set; leaving $TEST_DOMAIN in place"
elif [[ -n "$INSTANCE_ID" ]]; then
    R="$(api DELETE "/api/v1/instances/$INSTANCE_ID")"; ST="$(status_of "$R")"
    if [[ "$ST" == "204" ]]; then
        pass "DELETE -> 204"
    else
        fail "DELETE (expected 204)" "status=$ST $(body_of "$R" | mask)"
    fi
    if docker ps --format '{{.Names}}' | grep -qx "$CNAME"; then
        fail "container $CNAME still running after delete"
    else
        pass "container $CNAME gone after delete"
    fi
    if printf '%s' "$(body_of "$(api GET /api/v1/instances/)")" | grep -q "$TEST_DOMAIN"; then
        fail "row still present after delete"
    else
        pass "registry row removed after delete"
    fi
else
    skip "delete" "no instance id"
fi

# ===========================================================================
# 15. Telegram reachability (Test 12) — manual
# ===========================================================================
hdr 15 "Telegram reachability (UAT Test 12 — manual)"
if [[ -n "${TG:-}" ]] && $KEEP; then
    TME="${TG/tg:\/\/proxy?/https:\/\/t.me\/proxy?}"
    echo "        Instance kept (--keep). Open ONE of these on your phone:"
    echo ""
    echo "          $TG"
    echo "          $TME"
    echo ""
    echo "        (Contains the live proxy secret — do not paste this block into"
    echo "         shared logs.)"
    if command -v qrencode >/dev/null 2>&1; then
        echo "        Scan the QR in Telegram (Settings -> Data and Storage -> Proxy):"
        qrencode -t ANSIUTF8 "$TG" | sed 's/^/        /'
    else
        info "Tip: 'apt-get install -y qrencode' to print a scannable QR here."
    fi
elif [[ -n "${TG:-}" ]]; then
    info "Instance was deleted (no --keep), so its URL is dead now."
    info "Re-run with --keep to get a live URL:  bash scripts/verify-phase2.sh --domain $TEST_DOMAIN --keep"
else
    info "No instance created (create failed earlier) — nothing to open."
fi

# ===========================================================================
# Summary
# ===========================================================================
echo ""
echo "=== Summary ==="
echo "  Passed:  $PASSED"
echo "  Failed:  $FAILED"
echo "  Skipped: $SKIPPED"
echo ""
if [[ $FAILED -gt 0 ]]; then
    echo "RESULT: FAILED ($FAILED failure(s)) — paste this output back for diagnosis"
    exit 1
else
    echo "RESULT: PASSED"
    exit 0
fi
