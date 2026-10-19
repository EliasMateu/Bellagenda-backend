"""Healthcheck do container web: GET /api/v1/saude/ (200 = API e banco ok). Usa o 1o host permitido."""

import os
import sys
import urllib.request

hosts = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]
requisicao = urllib.request.Request(
    "http://127.0.0.1:8000/api/v1/saude/",
    headers={"Host": hosts[0] if hosts else "127.0.0.1"},
)
try:
    with urllib.request.urlopen(requisicao, timeout=4) as resposta:
        sys.exit(0 if resposta.status == 200 else 1)
except Exception:
    sys.exit(1)
