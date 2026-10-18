"""API do backoffice (D-33): /api/v1/backoffice/contas/...

Somente superusuario ativo logado por sessao. Qualquer outro (anonimo, profissional, staff) recebe o
mesmo 404. Nunca expoe clientes, agendamentos, servicos nem senhas: so dados cadastrais da conta.
"""

import re

from django.db.models import Count, Q
from rest_framework import serializers
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clientes.telefone import telefone_e164_ou_none
from apps.core.exceptions import ErroApi, NaoEncontrado

from .models import Profissional, TransicaoInvalidaError
from .servicos import mudar_status_auditado

Status = Profissional.Status

# acao -> (status destino, status de origem exigido, motivo obrigatorio)
ACOES = {
    "aprovar": (Status.ATIVA, Status.PENDENTE, False),
    "rejeitar": (Status.REJEITADA, None, True),
    "suspender": (Status.SUSPENSA, None, True),
    "reativar": (Status.ATIVA, Status.SUSPENSA, False),
}


class TransicaoInvalida(ErroApi):
    status_code = 409
    code = "transicao_invalida"
    default_detail = "Esta acao nao se aplica ao status atual da conta."


class AutenticacaoBackoffice(SessionAuthentication):
    """Sessao; o CSRF so e cobrado de superusuario ativo (os demais viram 404 antes, sem 403 revelador)."""

    def authenticate(self, request):
        user = getattr(request._request, "user", None)
        if user is None or not (user.is_active and user.is_superuser):
            return None
        return super().authenticate(request)


class SuperusuarioAtivo(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.is_superuser)


class BackofficeView(APIView):
    authentication_classes = [AutenticacaoBackoffice]
    permission_classes = [SuperusuarioAtivo]

    def permission_denied(self, request, message=None, code=None):
        raise NaoEncontrado()


def _iso(dt):
    return dt.isoformat() if dt else None


def conta_para_dict(p):
    return {
        "id": p.pk,
        "username": p.user.username,
        "nome_exibicao": p.nome_exibicao,
        "slug": p.slug,
        "telefone": telefone_e164_ou_none(p.telefone),
        "status": p.status,
        "cadastrado_em": _iso(p.user.date_joined),
        "aprovado_por": p.aprovado_por.username if p.aprovado_por else None,
        "aprovado_em": _iso(p.aprovado_em),
        "motivo_status": p.motivo_status,
        "termos_versao": p.termos_versao,
        "termos_aceitos_em": _iso(p.termos_aceitos_em),
    }


def _consulta():
    return Profissional.objects.select_related("user", "aprovado_por")


class ContasView(BackofficeView):
    def get(self, request):
        status_filtro = request.query_params.get("status", "").strip()
        if status_filtro and status_filtro not in Status.values:
            raise serializers.ValidationError({"status": ["Status invalido."]})
        busca = request.query_params.get("q", "").strip()[:100]

        total = Profissional.objects.aggregate(
            **{s: Count("pk", filter=Q(status=s)) for s in Status.values}
        )
        contas = _consulta()
        if status_filtro:
            contas = contas.filter(status=status_filtro)
        if busca:
            filtro = (
                Q(nome_exibicao__icontains=busca)
                | Q(user__username__icontains=busca)
                | Q(slug__icontains=busca)
                | Q(telefone__icontains=busca)
            )
            digitos = re.sub(r"\D", "", busca)
            if digitos:  # telefone guardado em E.164: aceita busca digitada com mascara
                filtro |= Q(telefone__contains=digitos)
            contas = contas.filter(filtro)

        contas = list(contas)
        pendentes = sorted(
            (c for c in contas if c.status == Status.PENDENTE), key=lambda c: (c.user.date_joined, c.pk)
        )
        demais = sorted(
            (c for c in contas if c.status != Status.PENDENTE),
            key=lambda c: (c.user.date_joined, c.pk), reverse=True,
        )
        return Response(
            {"contagens": total, "resultados": [conta_para_dict(c) for c in pendentes + demais]}
        )


class MotivoSerializer(serializers.Serializer):
    motivo = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class AcaoContaView(BackofficeView):
    def post(self, request, pk, acao):
        if acao not in ACOES:
            raise NaoEncontrado()
        destino, origem, exige_motivo = ACOES[acao]
        entrada = MotivoSerializer(data=request.data if hasattr(request.data, "get") else {})
        entrada.is_valid(raise_exception=True)
        motivo = entrada.validated_data["motivo"].strip()
        if exige_motivo and len(motivo) < 3:
            raise serializers.ValidationError({"motivo": ["Informe o motivo (minimo 3 caracteres)."]})
        try:
            mudar_status_auditado(pk, destino, request.user, motivo, so_de=origem)
        except Profissional.DoesNotExist:
            raise NaoEncontrado()
        except TransicaoInvalidaError:
            raise TransicaoInvalida()
        return Response(conta_para_dict(_consulta().get(pk=pk)))
