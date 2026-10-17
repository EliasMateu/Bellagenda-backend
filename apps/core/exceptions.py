"""Erros da API padronizados como {code, message, fields}."""

from rest_framework import exceptions, status
from rest_framework.views import exception_handler


class ErroApi(exceptions.APIException):
    """Erro de negocio com `code` estavel para o frontend e dados extras opcionais."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "erro"
    default_detail = "Requisicao invalida."

    def __init__(self, detail=None, code=None, extra=None):
        super().__init__(detail)
        if code:
            self.code = code
        self.extra = extra or {}


class NaoEncontrado(ErroApi):
    """404 unico para qualquer falha de localizacao: nao revela se o recurso existe."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "nao_encontrado"
    default_detail = "Nao encontrado."


def _textos(valor):
    if isinstance(valor, (list, tuple)):
        return [t for item in valor for t in _textos(item)]
    if isinstance(valor, dict):
        return [t for item in valor.values() for t in _textos(item)]
    return [str(valor)]


def _campos(dados):
    if isinstance(dados, dict):
        return {str(campo): _textos(erros) for campo, erros in dados.items()}
    return {"non_field_errors": _textos(dados)}


def tratar_excecao(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None

    fields = {}
    extra = {}
    if isinstance(exc, ErroApi):
        code, message = exc.code, str(exc.detail)
        extra = exc.extra
    elif isinstance(exc, exceptions.ValidationError):
        code, message = "validacao", "Dados invalidos."
        fields = _campos(exc.detail)
    elif isinstance(exc, exceptions.Throttled):
        code, message = "limite_requisicoes", "Muitas requisicoes. Tente novamente mais tarde."
    elif isinstance(exc, exceptions.NotFound):
        code, message = "nao_encontrado", "Nao encontrado."
    elif isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        code, message = "nao_autenticado", "Autenticacao necessaria."
    elif isinstance(exc, exceptions.PermissionDenied):
        code, message = "acesso_negado", "Acesso negado."
    else:
        code, message = "erro", str(getattr(exc, "detail", "Erro."))

    response.data = {"code": code, "message": message, "fields": fields, **extra}
    return response
