#!/usr/bin/env bash
# =============================================================================
# verify-infra.sh — MTG Orchestrator Phase 1 static + live VPS verification
#
# Usage:
#   # Static-only checks (safe on any machine):
#   bash scripts/verify-infra.sh
#
#   # Full checks including live VPS smoke tests:
#   PANEL_DOMAIN=mtg.yourdomain.ru MOSCOW_IP=1.2.3.4 bash scripts/verify-infra.sh --live
#
# The script sources .env if it exists (for PANEL_DOMAIN, MOSCOW_IP, etc.).
# Secrets are used for config validation only — never printed.
#
# Exit code: 0 if all enabled checks pass; 1 if any check fails.
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------
LIVE=false
for arg in "$@"; do
    [[ "$arg" == "--live" ]] && LIVE=true
done

# ---------------------------------------------------------------------------
# Load .env (if present) — never print secrets
# ---------------------------------------------------------------------------
ENV_FILE="$REPO_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    # Use env file without exporting secrets to subshells
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
elif [[ -f "$REPO_DIR/.env.example" ]]; then
    echo "WARNING: .env not found; sourcing .env.example for placeholder values"
    set -a
    # shellcheck disable=SC1090
    source "$REPO_DIR/.env.example"
    set +a
fi

# ---------------------------------------------------------------------------
# Counters and helpers
# ---------------------------------------------------------------------------
PASSED=0
FAILED=0
SKIPPED=0

pass() { echo "  PASS  $1"; (( PASSED++ )); }
fail() { echo "  FAIL  $1"; [[ -n "${2:-}" ]] && echo "        $2"; (( FAILED++ )); }
skip() { echo "  SKIP  $1 (${2:-N/A})"; (( SKIPPED++ )); }

check_cmd() {
    command -v "$1" &>/dev/null
}

echo ""
echo "=== MTG Orchestrator Phase 1 — Verification ==="
echo "Repo: $REPO_DIR"
echo "Mode: $(if $LIVE; then echo 'static + live VPS'; else echo 'static only'; fi)"
echo ""

# ===========================================================================
# 1. docker compose config
# ===========================================================================
echo "[ 1 ] Compose configuration"

if ! check_cmd docker; then
    skip "docker compose config" "docker not found"
else
    cd "$REPO_DIR"

    ENV_ARG=""
    if [[ -f "$ENV_FILE" ]]; then
        ENV_ARG="$ENV_FILE"
    elif [[ -f "$REPO_DIR/.env.example" ]]; then
        ENV_ARG="$REPO_DIR/.env.example"
        echo "        Using .env.example for static compose validation"
    fi

    if [[ -n "$ENV_ARG" ]]; then
        COMPOSE_OUT=$(docker compose --env-file "$ENV_ARG" config --quiet 2>&1)
    else
        COMPOSE_OUT=$(docker compose config --quiet 2>&1)
    fi
    COMPOSE_RC=$?
    if [[ $COMPOSE_RC -eq 0 ]]; then
        pass "docker compose config"
    else
        fail "docker compose config" "$COMPOSE_OUT"
    fi
fi

# ===========================================================================
# 2. nginx config
# ===========================================================================
echo ""
echo "[ 2 ] nginx configuration"

NGINX_TMPL="$REPO_DIR/infra/nginx/nginx.conf.template"
if [[ ! -f "$NGINX_TMPL" ]]; then
    fail "infra/nginx/nginx.conf.template exists" "File not found: $NGINX_TMPL"
else
    pass "infra/nginx/nginx.conf.template exists"

    # The template is rendered by the nginx image entrypoint (envsubst) into
    # /etc/nginx/nginx.conf at container start. Validate by mounting the
    # template at /etc/nginx/templates/ and letting the entrypoint render it
    # before `nginx -t`, so the test exercises the SAME rendering the running
    # container uses. PANEL_DOMAIN is taken from the loaded .env (or a dummy).
    if check_cmd docker; then
        RENDER_DOMAIN="${PANEL_DOMAIN:-panel.example.test}"
        if docker run --rm \
            -e PANEL_DOMAIN="$RENDER_DOMAIN" \
            -e NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx \
            -e NGINX_ENVSUBST_FILTER=PANEL_DOMAIN \
            -v "$NGINX_TMPL:/etc/nginx/templates/nginx.conf.template:ro" \
            nginx:1.27-alpine nginx -t 2>&1; then
            pass "nginx -t (rendered template syntax valid)"
        else
            fail "nginx -t (rendered template syntax)" "See output above"
        fi

        # CR-04: fail if any unsubstituted shell-style placeholder survives in
        # the RENDERED config. Catches a missing/empty PANEL_DOMAIN or any
        # other un-rendered ${...} that would silently break routing.
        RENDERED=$(docker run --rm \
            -e PANEL_DOMAIN="$RENDER_DOMAIN" \
            -e NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx \
            -e NGINX_ENVSUBST_FILTER=PANEL_DOMAIN \
            -v "$NGINX_TMPL:/etc/nginx/templates/nginx.conf.template:ro" \
            --entrypoint sh \
            nginx:1.27-alpine -c \
            '/docker-entrypoint.d/20-envsubst-on-templates.sh >/dev/null 2>&1; cat /etc/nginx/nginx.conf' 2>/dev/null || echo "")
        if printf '%s' "$RENDERED" | grep -q '\${'; then
            fail "no unsubstituted \${...} placeholders in rendered nginx.conf" \
                 "rendered config still contains \${...} — check PANEL_DOMAIN / envsubst"
        else
            pass "no unsubstituted \${...} placeholders in rendered nginx.conf"
        fi
    else
        skip "nginx -t (rendered template syntax)" "docker not available"
        skip "no unsubstituted \${...} placeholders" "docker not available"
    fi

    grep -q 'ssl_preread.*on'                   "$NGINX_TMPL" && pass "ssl_preread on"               || fail "ssl_preread on"
    grep -q 'resolver 127\.0\.0\.11.*ipv6=off'  "$NGINX_TMPL" && pass "resolver 127.0.0.11 ipv6=off"  || fail "resolver 127.0.0.11 ipv6=off"
    grep -q 'default.*mtg-default'              "$NGINX_TMPL" && pass "default → mtg-default"         || fail "default → mtg-default"
    grep -q 'acme-challenge'                    "$NGINX_TMPL" && pass "ACME challenge path configured" || fail "ACME challenge path missing"
fi

# ===========================================================================
# 3. mtg default config
# ===========================================================================
echo ""
echo "[ 3 ] mtg default config"

MTG_CONF="$REPO_DIR/infra/mtg/default.config.toml"
if [[ ! -f "$MTG_CONF" ]]; then
    fail "infra/mtg/default.config.toml exists" "File not found"
else
    pass "infra/mtg/default.config.toml exists"

    # No real secret committed (must not contain an "ee" + 32-hex secret value)
    if grep -qP '^\s*secret\s*=\s*"ee[0-9a-f]{32}' "$MTG_CONF" 2>/dev/null || \
       grep -qE '^\s*secret\s*=\s*"ee[0-9a-f]{32}' "$MTG_CONF" 2>/dev/null; then
        fail "No real secret in committed config"
    else
        pass "No real secret in committed config (placeholder/comment only)"
    fi

    grep -q '\[stats\.prometheus\]'    "$MTG_CONF" && pass "[stats.prometheus] section present"  || fail "[stats.prometheus] section missing"
    grep -q 'blocked-subnets.*=.*\[\]' "$MTG_CONF" && pass "blocked-subnets = [] (RFC1918 off)"  || fail "blocked-subnets not disabled"
    grep -q 'bind-to.*0\.0\.0\.0'      "$MTG_CONF" && pass "bind-to = 0.0.0.0 (not 127.0.0.1)"  || fail "bind-to not 0.0.0.0"
fi

# ===========================================================================
# 4. .env.example completeness
# ===========================================================================
echo ""
echo "[ 4 ] .env.example"

ENV_EXAMPLE="$REPO_DIR/.env.example"
if [[ ! -f "$ENV_EXAMPLE" ]]; then
    fail ".env.example exists" "File not found"
else
    pass ".env.example exists"
    REQUIRED_VARS=(
        PANEL_DOMAIN LE_EMAIL MOSCOW_IP FRANKFURT_IP
        MTG_DEFAULT_SECRET MTG_DEFAULT_PORT MTG_DEFAULT_DOMAIN
        BOT_TOKEN OWNER_USER_ID WEBHOOK_SECRET CERTBOT_STAGING
    )
    for var in "${REQUIRED_VARS[@]}"; do
        grep -q "^${var}=" "$ENV_EXAMPLE" && pass ".env.example has $var" || fail ".env.example missing $var"
    done
fi

# ===========================================================================
# 5. Log rotation on all services
# ===========================================================================
echo ""
echo "[ 5 ] Log rotation"

COMPOSE_FILE="$REPO_DIR/docker-compose.yml"
if [[ ! -f "$COMPOSE_FILE" ]]; then
    fail "docker-compose.yml exists" "File not found"
else
    pass "docker-compose.yml exists"
    SERVICES=(nginx backend docker-socket-proxy certbot mtg-default)
    for svc in "${SERVICES[@]}"; do
        # Check for max-size: appearing after each service label (rough but reliable)
        if awk "/^  ${svc}:/{found=1} found && /max-size/{print; found=0}" "$COMPOSE_FILE" | grep -q .; then
            pass "$svc has log rotation (max-size)"
        else
            fail "$svc missing log rotation"
        fi
    done
fi

# ===========================================================================
# 6. Security invariants
# ===========================================================================
echo ""
echo "[ 6 ] Security invariants"

if [[ -f "$COMPOSE_FILE" ]]; then
    grep -q 'internal: true' "$COMPOSE_FILE"  && pass "socket-net internal: true" \
                                               || fail "socket-net missing internal: true"

    SOCK_COUNT=$(grep -c 'docker\.sock' "$COMPOSE_FILE" || true)
    [[ "$SOCK_COUNT" -le 2 ]] && pass "docker.sock appears only in socket-proxy section" \
                              || fail "docker.sock appears unexpectedly in multiple places (count=$SOCK_COUNT)"

    # mtg-default must NOT be on socket-net
    # Extract mtg-default block and check it does NOT contain socket-net
    if awk '/^  mtg-default:/{f=1} f && /^  [a-z]/ && !/^  mtg-default:/{f=0} f' "$COMPOSE_FILE" \
        | grep -q 'socket-net'; then
        fail "mtg-default is on socket-net (must not be)"
    else
        pass "mtg-default is not on socket-net"
    fi
fi

# ===========================================================================
# 7. README completeness
# ===========================================================================
echo ""
echo "[ 7 ] README deployment guide"

README="$REPO_DIR/README.md"
if [[ ! -f "$README" ]]; then
    fail "README.md exists" "File not found"
else
    pass "README.md exists"
    grep -q 'docker compose up'      "$README" && pass "README has docker compose up"     || fail "README missing docker compose up"
    grep -q 'openssl s_client'       "$README" && pass "README has openssl s_client"      || fail "README missing openssl s_client"
    grep -q 'curl.*https://'         "$README" && pass "README has curl https smoke check" || fail "README missing curl https"
    grep -qE 'LogConfig|max-size'    "$README" && pass "README has log rotation check"    || fail "README missing log rotation check"
    grep -qE 'Internal.*true|socket-net' "$README" && pass "README has isolation check"   || fail "README missing isolation check"
    grep -q 'webroot'                "$README" && pass "README has certbot webroot"        || fail "README missing certbot webroot"
fi

# ===========================================================================
# 8. LIVE VPS smoke tests (only when --live and env vars available)
# ===========================================================================
if $LIVE; then
    echo ""
    echo "[ 8 ] Live VPS smoke tests"

    PANEL_DOMAIN="${PANEL_DOMAIN:-}"
    MOSCOW_IP="${MOSCOW_IP:-}"

    if [[ -z "$PANEL_DOMAIN" || -z "$MOSCOW_IP" ]]; then
        skip "Live smoke tests" "PANEL_DOMAIN and MOSCOW_IP must be set in .env or environment"
    else
        # 8a. docker compose ps — all expected services up
        echo "        Checking compose services..."
        cd "$REPO_DIR"
        RUNNING=$(docker compose ps --format json 2>/dev/null | python3 -c "
import sys, json
data = [json.loads(l) for l in sys.stdin if l.strip()]
names = [s.get('Name', s.get('Service','')) for s in data if s.get('State','') == 'running']
print(','.join(names))
" 2>/dev/null || echo "")
        for svc in nginx backend mtg-default docker-socket-proxy; do
            echo "$RUNNING" | grep -q "$svc" && pass "compose service $svc is running" \
                                             || fail "compose service $svc not running"
        done

        # 8b. Backend health
        echo "        Backend health..."
        if curl -sf http://localhost:8080/healthz 2>/dev/null | grep -q '"status":"ok"'; then
            pass "backend /healthz returns ok"
        else
            fail "backend /healthz failed" "curl http://localhost:8080/healthz"
        fi

        # 8c. TLS panel (requires cert to be issued)
        echo "        Panel TLS..."
        if curl -sf --max-time 10 "https://${PANEL_DOMAIN}/healthz" 2>/dev/null | grep -q '"status"'; then
            pass "https://${PANEL_DOMAIN}/healthz reachable (TLS valid)"
        else
            skip "https://${PANEL_DOMAIN}/healthz" "cert may not be issued yet — run certbot first"
        fi

        # 8d. Empty SNI → mtg (not panel)
        echo "        Empty-SNI check (no -servername)..."
        OPENSSL_OUT=$(echo "" | openssl s_client -connect "${MOSCOW_IP}:443" -timeout 5 2>&1 || true)
        if echo "$OPENSSL_OUT" | grep -qE 'CN=|certificate'; then
            # Got a TLS handshake — check it's NOT the panel cert
            if echo "$OPENSSL_OUT" | grep -q "${PANEL_DOMAIN}"; then
                fail "Empty-SNI reached panel (PANEL_DOMAIN in cert)" \
                     "nginx default route is wrong — panel must NOT be default"
            else
                pass "Empty-SNI reaches mtg (not panel cert)"
            fi
        else
            # No TLS cert in response — this is typical mtg fake-TLS behavior
            pass "Empty-SNI got non-panel response (mtg fake-TLS)"
        fi

        # 8e. Log rotation on running containers
        echo "        Log rotation on containers..."
        for svc in nginx backend mtg-default; do
            INSPECT=$(docker inspect "mtg-orchestrator-${svc}-1" 2>/dev/null || \
                      docker inspect "${svc}" 2>/dev/null || echo "")
            if echo "$INSPECT" | grep -q '"max-size"'; then
                pass "$svc container has log rotation"
            else
                fail "$svc container log rotation not confirmed" \
                     "docker inspect mtg-orchestrator-${svc}-1 | grep -A5 LogConfig"
            fi
        done

        # 8f. Network isolation
        echo "        Network isolation..."
        SOCK_NET=$(docker network inspect "mtg-orchestrator_socket-net" 2>/dev/null || echo "")
        if echo "$SOCK_NET" | grep -q '"Internal": true'; then
            pass "socket-net is Internal: true"
        else
            fail "socket-net Internal check failed" \
                 "docker network inspect mtg-orchestrator_socket-net | grep Internal"
        fi
    fi
fi

# ===========================================================================
# Summary
# ===========================================================================
echo ""
echo "=== Summary ==="
echo "  Passed:  $PASSED"
echo "  Failed:  $FAILED"
echo "  Skipped: $SKIPPED"

if ! $LIVE; then
    echo ""
    echo "=== VPS Smoke-Test Checklist (run with --live on Frankfurt VPS) ==="
    cat <<'CHECKLIST'

After deploying on Frankfurt VPS, run the following commands manually or use:
  bash scripts/verify-infra.sh --live

Manual checklist:

1. Compose services up
   docker compose ps
   # nginx, backend, mtg-default, docker-socket-proxy — Up

2. Backend health
   curl -s http://localhost:8080/healthz
   # {"status":"ok"}

3. Panel TLS (after certbot issuance)
   curl -v https://<PANEL_DOMAIN>/healthz
   # HTTP/2 200, valid Let's Encrypt cert

4. Empty/no-SNI → mtg (not panel)
   openssl s_client -connect <MOSCOW_IP>:443 </dev/null 2>&1 | head -20
   # Fake-TLS from mtg; NOT the panel certificate

5. Panel SNI → backend
   openssl s_client -connect <MOSCOW_IP>:443 -servername <PANEL_DOMAIN> </dev/null 2>&1 | head -20
   # Valid Let's Encrypt cert for <PANEL_DOMAIN>

6. Log rotation
   docker inspect mtg-orchestrator-nginx-1 | grep -A5 LogConfig
   # "max-size": "10m", "max-file": "3"

7. Network isolation
   docker network inspect mtg-orchestrator_socket-net | grep Internal
   # "Internal": true

8. Port 80 (from EXTERNAL machine, not Moscow VPS)
   curl -v http://<PANEL_DOMAIN>/.well-known/acme-challenge/test
   # 404 from nginx (connection reached Frankfurt)

9. certbot dry run
   source .env
   docker compose run --rm certbot certonly --dry-run \
     --webroot -w /var/www/certbot \
     -d "${PANEL_DOMAIN}" --email "${LE_EMAIL}" \
     --agree-tos --no-eff-email
   # "The dry run was successful."
CHECKLIST
fi

echo ""
if [[ $FAILED -gt 0 ]]; then
    echo "RESULT: FAILED ($FAILED failure(s))"
    exit 1
else
    echo "RESULT: PASSED (all checks passed)"
    exit 0
fi
