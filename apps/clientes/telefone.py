import re

from django.core.exceptions import ValidationError

_E164_BR = re.compile(r"^\+55[1-9][1-9](?:9\d{8}|[2-5]\d{7})$")


def validar_telefone_e164(valor):
    if not _E164_BR.match(valor or ""):
        raise ValidationError(
            "Telefone invalido. Use o formato +55DDDNUMERO.", code="telefone_invalido"
        )


def normalizar_telefone_br(valor):
    """Converte um telefone brasileiro para E.164 (+55DDDNUMERO) ou levanta ValidationError.

    Validacao simples: DDD de 11 a 99, celular com 9 digitos iniciando em 9,
    fixo com 8 digitos iniciando em 2 a 5.
    """
    digitos = re.sub(r"\D", "", valor or "")
    if digitos.startswith("55") and len(digitos) in (12, 13):
        digitos = digitos[2:]
    candidato = "+55" + digitos
    validar_telefone_e164(candidato)
    return candidato


def telefone_e164_ou_none(valor):
    """E.164 se o valor for um telefone brasileiro valido; None se vazio ou invalido."""
    try:
        return normalizar_telefone_br(valor)
    except ValidationError:
        return None
