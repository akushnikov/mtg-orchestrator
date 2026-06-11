"""Container healthcheck: probe /healthz on whichever port the backend chose.

The entrypoint runs uvicorn on :8443 (TLS) when the panel cert exists, else on
:8080 (plain HTTP). Try the TLS port first (cert CN won't match localhost, so
verification is disabled), then fall back to the HTTP port. Exit 0 if either
returns 200.
"""

import ssl
import sys
import urllib.request

_CTX = ssl._create_unverified_context()

for url in ("https://localhost:8443/healthz", "http://localhost:8080/healthz"):
    try:
        if url.startswith("https"):
            resp = urllib.request.urlopen(url, context=_CTX, timeout=5)
        else:
            resp = urllib.request.urlopen(url, timeout=5)
        if resp.status == 200:
            sys.exit(0)
    except Exception:
        pass

sys.exit(1)
