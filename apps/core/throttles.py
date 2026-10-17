"""Limite de requisicoes por janela fixa, com contadores no cache em tabela (sem Redis).

Observacao: o contador faz get + set, entao sob concorrencia extrema pode contar a menos.
Os identificadores sao hasheados antes de ir ao cache (telefone/IP nao ficam em texto).
"""

import hashlib
import time

from django.conf import settings
from django.core.cache import cache
from rest_framework.throttling import BaseThrottle

from apps.clientes.telefone import normalizar_telefone_br


def ip_do_cliente(request):
    """IP de origem. Por ora so REMOTE_ADDR (ver NUM_PROXIES em settings.py)."""
    return request.META.get("REMOTE_ADDR", "")


class LimiteJanelaFixa(BaseThrottle):
    escopo = None  # chave em settings.LIMITES_PUBLICOS
    metodos = ("POST",)

    def identificador(self, request, view):
        raise NotImplementedError

    def allow_request(self, request, view):
        if request.method not in self.metodos:
            return True
        ident = self.identificador(request, view)
        if ident is None:
            return True
        maximo, janela = settings.LIMITES_PUBLICOS[self.escopo]
        agora = time.time()
        balde = int(agora // janela)
        bruto = f"{self.escopo}:{ident}"
        chave = f"thr:{hashlib.sha256(bruto.encode()).hexdigest()}:{balde}"
        total = (cache.get(chave) or 0) + 1
        cache.set(chave, total, timeout=janela)
        self._espera = janela - (agora % janela)
        return total <= maximo

    def wait(self):
        return getattr(self, "_espera", None)


class LimitePorIP(LimiteJanelaFixa):
    escopo = "ip_post"

    def identificador(self, request, view):
        return ip_do_cliente(request)


class LimitePorTenant(LimiteJanelaFixa):
    escopo = "tenant"

    def identificador(self, request, view):
        return str(view.kwargs.get("slug", "")).lower()


class LimitePorTelefone(LimiteJanelaFixa):
    escopo = "telefone"

    def identificador(self, request, view):
        telefone = request.data.get("telefone") if hasattr(request.data, "get") else None
        try:
            return normalizar_telefone_br(telefone)
        except Exception:  # telefone invalido: a validacao do serializer responde 400
            return None


class LimiteConsultaPorIPeSlug(LimiteJanelaFixa):
    escopo = "consulta"

    def identificador(self, request, view):
        return f"{ip_do_cliente(request)}|{str(view.kwargs.get('slug', '')).lower()}"
