import re

from django.core.exceptions import ValidationError

SLUG_REGEX = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")

SLUGS_RESERVADOS = frozenset(
    {
        "admin", "api", "p", "login", "cadastro", "painel", "backoffice",
        "suporte", "ajuda", "www", "bela", "belaagenda", "static", "media",
    }
)


def validar_slug(valor):
    """Slug de 3 a 40 caracteres: minusculas, digitos e hifens (sem hifens duplos nem reservados)."""
    if not SLUG_REGEX.match(valor) or "--" in valor:
        raise ValidationError(
            "Use de 3 a 40 caracteres: letras minusculas, numeros e hifens "
            "(sem hifens duplos, nem no inicio ou no fim).",
            code="slug_invalido",
        )
    if valor in SLUGS_RESERVADOS:
        raise ValidationError("Este endereco e reservado.", code="slug_reservado")
