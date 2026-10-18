"""Limite de requisicoes por janela fixa, com contadores no cache em tabela (sem Redis).

Observacao: o contador faz get + set, entao sob concorrencia extrema pode contar a menos.
Os identificadores sao hasheados antes de ir ao cache (telefone/IP nao ficam em texto).
"""

import hashlib
import time

from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import Throttled
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

    def escopo_da_requisicao(self, request):
        return self.escopo

    def allow_request(self, request, view):
        if request.method not in self.metodos:
            return True
        ident = self.identificador(request, view)
        if ident is None:
            return True
        escopo = self.escopo_da_requisicao(request)
        maximo, janela = settings.LIMITES_PUBLICOS[escopo]
        agora = time.time()
        balde = int(agora // janela)
        bruto = f"{escopo}:{ident}"
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


def identificacao_por_token(request):
    """True se o corpo identifica o agendamento por token (token tem precedencia sobre codigo+telefone).

    Mesma regra do serializer (string nao vazia apos strip); qualquer outro formato cai no limite estrito.
    """
    dados = request.data
    token = dados.get("token") if hasattr(dados, "get") else None
    return isinstance(token, str) and bool(token.strip())


class LimiteConsultaPorIPeSlug(LimiteJanelaFixa):
    """Consultar/cancelar: por token (inviavel de adivinhar) o limite e generoso; por codigo+telefone, estrito.

    Toda tentativa conta, inclusive token invalido, para frear adivinhacao em massa.
    """

    escopo = "consulta"
    escopo_token = "consulta_token"

    def escopo_da_requisicao(self, request):
        return self.escopo_token if identificacao_por_token(request) else self.escopo

    def identificador(self, request, view):
        return f"{ip_do_cliente(request)}|{str(view.kwargs.get('slug', '')).lower()}"


class LimiteConsultaPorTenant(LimitePorTenant):
    """Por token, o teto por profissional e alto e separado do de criacao; por codigo+telefone segue o de tenant."""

    escopo_token = "tenant_consulta_token"

    def escopo_da_requisicao(self, request):
        return self.escopo_token if identificacao_por_token(request) else self.escopo


class LimiteFalhasLogin:
    """Conta SOMENTE falhas de autenticacao (sucesso nao conta), em tres chaves.

    - login_usuario_ip: username + IP (estrita): quem erra a propria senha e bloqueado so ali;
    - login_usuario: username em qualquer IP (mais frouxa): freia ataque distribuido;
    - login_ip: IP isolado (frouxa): freia varredura de varios usernames.
    Terceiros que erram a senha de alguem de OUTRO IP nao esgotam a chave estrita da vitima.
    """

    ESCOPOS = ("login_usuario_ip", "login_usuario", "login_ip")

    def __init__(self, request, username):
        usuario = str(username).strip().lower()
        ip = ip_do_cliente(request)
        self.agora = time.time()
        self.identificadores = {
            "login_usuario_ip": f"{usuario}|{ip}",
            "login_usuario": usuario,
            "login_ip": ip,
        }

    def _chave(self, escopo):
        _, janela = settings.LIMITES_PUBLICOS[escopo]
        balde = int(self.agora // janela)
        bruto = f"{escopo}:{self.identificadores[escopo]}"
        return f"thr:{hashlib.sha256(bruto.encode()).hexdigest()}:{balde}"

    def verificar(self):
        """429 se alguma chave ja atingiu o maximo de falhas na janela."""
        for escopo in self.ESCOPOS:
            maximo, janela = settings.LIMITES_PUBLICOS[escopo]
            if (cache.get(self._chave(escopo)) or 0) >= maximo:
                raise Throttled(wait=janela - (self.agora % janela))

    def registrar_falha(self):
        for escopo in self.ESCOPOS:
            _, janela = settings.LIMITES_PUBLICOS[escopo]
            chave = self._chave(escopo)
            cache.set(chave, (cache.get(chave) or 0) + 1, timeout=janela)

    def limpar(self):
        """Login com sucesso zera a chave estrita (as frouxas seguem contando o que ja houve)."""
        cache.delete(self._chave("login_usuario_ip"))
