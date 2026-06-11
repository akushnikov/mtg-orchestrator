#!/bin/sh
# =============================================================================
# backend entrypoint — choose the uvicorn listener based on TLS cert presence.
#
# The nginx stream mux passes the panel SNI through to backend:8443 (raw TLS),
# where uvicorn terminates real TLS with the Let's Encrypt cert. If the cert is
# not issued yet, fall back to plain HTTP on :8080 so the backend still boots
# (internal API + orchestration work; the external panel is unreachable until
# the cert exists). This keeps a fresh deploy from crash-looping on missing
# certs while making the panel work automatically once certbot has run.
# =============================================================================
set -eu

CERT="/certs/live/${PANEL_DOMAIN}"

if [ -f "${CERT}/fullchain.pem" ] && [ -f "${CERT}/privkey.pem" ]; then
    echo "[entrypoint] TLS cert found for ${PANEL_DOMAIN} -> uvicorn on :8443 (panel TLS)"
    exec uvicorn app.main:app \
        --host 0.0.0.0 --port 8443 \
        --ssl-certfile "${CERT}/fullchain.pem" \
        --ssl-keyfile  "${CERT}/privkey.pem" \
        --workers 1
fi

echo "[entrypoint] WARN: no TLS cert at ${CERT} -> uvicorn on :8080 (plain HTTP)."
echo "[entrypoint] The external panel stays unreachable until the cert is issued (certbot)."
exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1
