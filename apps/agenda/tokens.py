"""Token do link publico (guardado so como hash) e codigo curto para o WhatsApp."""

import hashlib
import secrets

# Sem caracteres ambiguos: I, L, O, 0, 1.
ALFABETO_CODIGO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
TAMANHO_CODIGO = 8


def gerar_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def gerar_token_e_hash():
    """Retorna (token, hash). O token vai so para o cliente; o banco guarda apenas o hash."""
    token = gerar_token()
    return token, hash_token(token)


def gerar_codigo_curto():
    return "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(TAMANHO_CODIGO))
