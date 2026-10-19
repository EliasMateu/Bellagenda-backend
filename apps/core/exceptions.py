"""Erros da API padronizados como {code, message, fields}."""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, OperationalError
from django.db.models import ProtectedError, RestrictedError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
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


# Constraint do banco -> (status HTTP, code, mensagem, campo).
RESTRICOES = {
    "servico_nome_unico_por_profissional": (
        400, "duplicado", "Ja existe um servico com este nome.", "nome"),
    "cliente_telefone_unico_por_profissional": (
        400, "duplicado", "Ja existe um cliente com este telefone.", "telefone"),
    "horario_unico_por_profissional_dia_inicio": (
        400, "duplicado", "Ja existe um horario neste dia com este inicio.", "inicio"),
    "agendamento_sem_sobreposicao": (
        409, "conflito_horario", "Este horario ja esta ocupado.", None),
    # Colisoes aleatorias (codigo curto) ou token repetido: controladas, o cliente pode tentar de novo.
    "agendamento_codigo_unico_por_profissional": (
        409, "tente_novamente", "Nao foi possivel concluir agora. Tente novamente.", None),
}
# Constraints com nome gerado pelo Django (unique=True em campo), casadas por prefixo.
PREFIXOS_RESTRICOES = {
    "agenda_agendamento_token_hash": (
        409, "tente_novamente", "Nao foi possivel concluir agora. Tente novamente.", None),
}

# SQLSTATE de conflito de concorrencia: deadlock_detected e serialization_failure.
SQLSTATES_CONCORRENCIA = {"40P01", "40001"}


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


def _resposta_banco(exc):
    """Resposta padronizada para violacoes conhecidas de constraint; None se desconhecida."""
    if isinstance(exc, (ProtectedError, RestrictedError)):
        return Response(
            {
                "code": "em_uso",
                "message": "Registro em uso; desative-o em vez de apagar.",
                "fields": {},
            },
            status=status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, IntegrityError):
        diag = getattr(exc.__cause__, "diag", None)
        nome = getattr(diag, "constraint_name", None) or ""
        regra = RESTRICOES.get(nome) or next(
            (r for prefixo, r in PREFIXOS_RESTRICOES.items() if nome.startswith(prefixo)), None
        )
        if regra is not None:
            http, code, message, campo = regra
            fields = {campo: [message]} if campo else {}
            return Response({"code": code, "message": message, "fields": fields}, status=http)
    if isinstance(exc, OperationalError):
        if getattr(exc.__cause__, "sqlstate", None) in SQLSTATES_CONCORRENCIA:
            return Response(
                {
                    "code": "conflito_concorrente",
                    "message": "Outra operacao estava em andamento ao mesmo tempo. Tente novamente.",
                    "fields": {},
                },
                status=status.HTTP_409_CONFLICT,
            )
    return None


def tratar_excecao(exc, context):
    if isinstance(exc, (IntegrityError, OperationalError, ProtectedError, RestrictedError)):
        return _resposta_banco(exc)  # None => segue como erro 500

    if isinstance(exc, DjangoValidationError):  # validacao de modelo (clean/save) => 400
        dados = exc.message_dict if hasattr(exc, "error_dict") else exc.messages
        return Response(
            {"code": "validacao", "message": "Dados invalidos.", "fields": _campos(dados)},
            status=status.HTTP_400_BAD_REQUEST,
        )

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
    elif isinstance(exc, (exceptions.NotFound, Http404)):
        code, message = "nao_encontrado", "Nao encontrado."
    elif isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        code, message = "nao_autenticado", "Autenticacao necessaria."
    elif isinstance(exc, (exceptions.PermissionDenied, DjangoPermissionDenied)):
        code, message = "acesso_negado", "Acesso negado."
    elif isinstance(exc, exceptions.MethodNotAllowed):
        code, message = "metodo_nao_permitido", "Metodo nao permitido."
    else:
        code, message = "erro", str(getattr(exc, "detail", "Erro."))

    response.data = {"code": code, "message": message, "fields": fields, **extra}
    return response
