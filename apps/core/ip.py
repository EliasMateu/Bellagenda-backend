"""IP do cliente para limites de requisicao e qualquer registro de IP (funcao unica)."""

import hmac
import ipaddress

from django.conf import settings

CABECALHO_SEGREDO = "HTTP_X_BELA_PROXY_SECRET"
CABECALHO_IP = "HTTP_X_BELA_CLIENT_IP"


def ip_do_cliente(request):
    """IP de origem da requisicao.

    Usa X-Bela-Client-Ip SOMENTE se X-Bela-Proxy-Secret for igual a settings.PROXY_SECRET (comparacao em
    tempo constante) e o segredo estiver configurado; o valor precisa ser um IP valido. Em qualquer outro
    caso, REMOTE_ADDR. X-Forwarded-For nunca e considerado.
    """
    remoto = request.META.get("REMOTE_ADDR", "")
    segredo = getattr(settings, "PROXY_SECRET", "")
    if not segredo:
        return remoto
    enviado = request.META.get(CABECALHO_SEGREDO, "")
    if not hmac.compare_digest(enviado.encode("utf-8"), segredo.encode("utf-8")):
        return remoto
    try:
        return str(ipaddress.ip_address(request.META.get(CABECALHO_IP, "").strip()))
    except ValueError:
        return remoto
