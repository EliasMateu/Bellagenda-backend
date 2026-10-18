"""API publica de agendamento (sem login): /api/v1/publico/<slug>/..."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clientes.models import Cliente
from apps.clientes.telefone import normalizar_telefone_br
from apps.contas.models import Profissional
from apps.core.exceptions import ErroApi, NaoEncontrado
from apps.core.throttles import (
    LimiteConsultaPorIPeSlug,
    LimitePorIP,
    LimitePorTelefone,
    LimitePorTenant,
)
from apps.servicos.models import Servico

from .horarios import (
    STATUS_ATIVOS,
    configuracao_efetiva,
    horario_oferecido,
    horarios_livres,
    proximos_horarios,
)
from .models import Agendamento, TransicaoAgendamentoInvalida
from .tokens import gerar_token_e_hash, hash_token

MAX_AGENDAMENTOS_FUTUROS_POR_TELEFONE = 2
NOME_CONSTRAINT_SOBREPOSICAO = "agendamento_sem_sobreposicao"


class ConflitoHorario(ErroApi):
    status_code = status.HTTP_409_CONFLICT
    code = "conflito_horario"
    default_detail = "Este horario acabou de ser ocupado. Escolha outro."


class LimiteAgendamentos(ErroApi):
    status_code = status.HTTP_409_CONFLICT
    code = "limite_agendamentos_ativos"
    default_detail = "Este telefone ja possui o maximo de agendamentos futuros."


class ForaDoPrazo(ErroApi):
    status_code = status.HTTP_403_FORBIDDEN
    code = "fora_do_prazo"
    default_detail = "O prazo para cancelar este agendamento ja passou."


class HorarioIndisponivel(ErroApi):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "horario_indisponivel"
    default_detail = "Este horario nao esta disponivel."


class NaoCancelavel(ErroApi):
    status_code = status.HTTP_409_CONFLICT
    code = "nao_cancelavel"
    default_detail = "Este agendamento nao pode mais ser cancelado."


def obter_profissional_ativa(slug):
    """Profissional ativa pelo slug; qualquer falha (inexistente/nao ativa) e o mesmo 404."""
    profissional = Profissional.objects.filter(
        slug=str(slug).lower(), status=Profissional.Status.ATIVA
    ).first()
    if profissional is None:
        raise NaoEncontrado()
    return profissional


class PublicoView(APIView):
    authentication_classes = []  # sem sessao, logo sem CSRF
    permission_classes = [AllowAny]
    throttle_classes = []


def _servico_ativo(profissional, pk):
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        return None
    return Servico.objects.filter(profissional=profissional, pk=pk, ativo=True).first()


def _iso(dt, tz):
    return dt.astimezone(tz).isoformat()


def mascarar_telefone(telefone):
    return f"(**) *****-{telefone[-4:]}"


class ServicosView(PublicoView):
    def get(self, request, slug):
        profissional = obter_profissional_ativa(slug)
        servicos = Servico.objects.filter(profissional=profissional, ativo=True).order_by("nome")
        return Response(
            {
                "nome_exibicao": profissional.nome_exibicao,
                "servicos": [
                    {"id": s.pk, "nome": s.nome, "duracao_min": s.duracao_min, "preco": str(s.preco)}
                    for s in servicos
                ],
            }
        )


class HorariosQuery(serializers.Serializer):
    servico = serializers.IntegerField()
    data = serializers.DateField()


class HorariosView(PublicoView):
    def get(self, request, slug):
        profissional = obter_profissional_ativa(slug)
        consulta = HorariosQuery(data=request.query_params)
        consulta.is_valid(raise_exception=True)
        servico = _servico_ativo(profissional, consulta.validated_data["servico"])
        if servico is None:
            raise NaoEncontrado()
        config = configuracao_efetiva(profissional)
        tz = ZoneInfo(config.timezone)
        dia = consulta.validated_data["data"]
        livres = horarios_livres(profissional, servico, dia, config=config)
        return Response(
            {
                "servico": servico.pk,
                "data": dia.isoformat(),
                "fuso": config.timezone,
                "horarios": [_iso(h, tz) for h in livres],
            }
        )


class CriarAgendamentoSerializer(serializers.Serializer):
    servico = serializers.IntegerField()
    inicio = serializers.DateTimeField()
    nome = serializers.CharField(min_length=2, max_length=100)
    telefone = serializers.CharField(max_length=30)
    observacao = serializers.CharField(max_length=500, required=False, allow_blank=True)
    consentimento = serializers.BooleanField()
    # Campo-isca: invisivel para pessoas; robos costumam preenche-lo.
    website = serializers.CharField(required=False, allow_blank=True, max_length=200)

    def validate_telefone(self, valor):
        try:
            return normalizar_telefone_br(valor)
        except Exception:
            raise serializers.ValidationError("Telefone invalido. Use DDD + numero.")

    def validate_consentimento(self, valor):
        if valor is not True:
            raise serializers.ValidationError("E necessario aceitar o uso dos dados para agendar.")
        return valor

    def validate_website(self, valor):
        if valor:
            raise serializers.ValidationError("Valor invalido.")
        return valor


class CriarAgendamentoView(PublicoView):
    throttle_classes = [LimitePorIP, LimitePorTelefone, LimitePorTenant]

    def post(self, request, slug):
        profissional = obter_profissional_ativa(slug)
        entrada = CriarAgendamentoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data

        servico = _servico_ativo(profissional, dados["servico"])
        if servico is None:
            raise serializers.ValidationError({"servico": ["Servico invalido."]})

        agora = timezone.now()
        inicio = dados["inicio"]
        # O horario precisa ser um dos oferecidos (ignora agendamentos: conflito e decidido
        # pelo banco abaixo, o que gera o 409).
        if not horario_oferecido(
            profissional, servico, inicio, agora, considerar_agendamentos=False
        ):
            raise HorarioIndisponivel()

        config = configuracao_efetiva(profissional)
        token, token_hash = gerar_token_e_hash()
        try:
            with transaction.atomic():
                # Serializa as criacoes da mesma profissional (limite por telefone sem corrida).
                Profissional.objects.select_for_update(no_key=True).get(pk=profissional.pk)
                cliente, _ = Cliente.objects.get_or_create(
                    profissional=profissional,
                    telefone=dados["telefone"],
                    defaults={"nome": dados["nome"]},
                )
                if cliente.consentimento_em is None:
                    cliente.consentimento_em = agora
                    cliente.save(update_fields=["consentimento_em"])
                futuros = Agendamento.objects.filter(
                    profissional=profissional,
                    cliente=cliente,
                    status__in=STATUS_ATIVOS,
                    inicio__gt=agora,
                ).count()
                if futuros >= MAX_AGENDAMENTOS_FUTUROS_POR_TELEFONE:
                    raise LimiteAgendamentos()
                agendamento = Agendamento(
                    profissional=profissional,
                    cliente=cliente,
                    servico=servico,
                    inicio=inicio,
                    fim=inicio + timedelta(minutes=servico.duracao_min),
                    status=(
                        Agendamento.Status.CONFIRMADO
                        if config.confirmacao_automatica
                        else Agendamento.Status.PENDENTE
                    ),
                    preco_cobrado=servico.preco,
                    token_hash=token_hash,
                    origem=Agendamento.Origem.ONLINE,
                    observacao_cliente=dados.get("observacao", ""),
                )
                agendamento.save()
        except IntegrityError as erro:
            diag = getattr(erro.__cause__, "diag", None)
            if getattr(diag, "constraint_name", None) != NOME_CONSTRAINT_SOBREPOSICAO:
                raise
            tz = ZoneInfo(config.timezone)
            sugestoes = proximos_horarios(profissional, servico, inicio, agora=agora)
            raise ConflitoHorario(extra={"sugestoes": [_iso(h, tz) for h in sugestoes]})

        tz = ZoneInfo(config.timezone)
        return Response(
            {
                "agendamento": {
                    "codigo": agendamento.codigo_curto,
                    "token": token,  # exibido uma unica vez; so o hash fica no banco
                    "status": agendamento.status,
                    "inicio": _iso(agendamento.inicio, tz),
                    "fim": _iso(agendamento.fim, tz),
                    "servico": servico.nome,
                    "profissional": profissional.nome_exibicao,
                }
            },
            status=status.HTTP_201_CREATED,
        )


class IdentificacaoSerializer(serializers.Serializer):
    token = serializers.CharField(required=False, max_length=200)
    codigo = serializers.CharField(required=False, max_length=20)
    telefone = serializers.CharField(required=False, max_length=30)

    def validate(self, attrs):
        if attrs.get("token") or (attrs.get("codigo") and attrs.get("telefone")):
            return attrs
        raise serializers.ValidationError("Informe o token ou o codigo com o telefone.")


def localizar_agendamento(profissional, dados, agora, para_atualizar=False):
    """Agendamento do tenant identificado por token OU codigo+telefone; senao 404 unico.

    Nao revela se token/codigo/telefone existem: todas as falhas viram NaoEncontrado.
    """
    consulta = Agendamento.objects.filter(profissional=profissional)
    if para_atualizar:
        consulta = consulta.select_for_update()
    if dados.get("token"):
        consulta = consulta.filter(token_hash=hash_token(dados["token"]))
    else:
        try:
            telefone = normalizar_telefone_br(dados["telefone"])
        except Exception:
            raise NaoEncontrado()
        consulta = consulta.filter(
            codigo_curto=dados["codigo"].strip().upper(), cliente__telefone=telefone
        )
    agendamento = consulta.select_related("cliente", "servico").first()
    if agendamento is None or agendamento.fim <= agora:
        raise NaoEncontrado()
    return agendamento


def _limite_cancelamento(agendamento, config):
    return agendamento.inicio - timedelta(hours=config.antecedencia_min_cancelamento_h)


class ConsultarAgendamentoView(PublicoView):
    throttle_classes = [LimiteConsultaPorIPeSlug, LimitePorTenant]

    def post(self, request, slug):
        profissional = obter_profissional_ativa(slug)
        entrada = IdentificacaoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        agora = timezone.now()
        agendamento = localizar_agendamento(profissional, entrada.validated_data, agora)
        config = configuracao_efetiva(profissional)
        tz = ZoneInfo(config.timezone)
        limite = _limite_cancelamento(agendamento, config)
        return Response(
            {
                "codigo": agendamento.codigo_curto,
                "status": agendamento.status,
                "inicio": _iso(agendamento.inicio, tz),
                "fim": _iso(agendamento.fim, tz),
                "servico": {
                    "nome": agendamento.servico.nome,
                    "duracao_min": agendamento.servico.duracao_min,
                },
                "preco": str(agendamento.preco_cobrado),
                "profissional": profissional.nome_exibicao,
                "cliente": {
                    "nome": agendamento.cliente.nome,
                    "telefone": mascarar_telefone(agendamento.cliente.telefone),
                },
                "cancelamento_ate": _iso(limite, tz),
                "pode_cancelar": agendamento.status in STATUS_ATIVOS and agora < limite,
            }
        )


class CancelarAgendamentoView(PublicoView):
    throttle_classes = [LimiteConsultaPorIPeSlug, LimitePorTenant]

    def post(self, request, slug):
        profissional = obter_profissional_ativa(slug)
        entrada = IdentificacaoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        agora = timezone.now()
        config = configuracao_efetiva(profissional)
        with transaction.atomic():
            agendamento = localizar_agendamento(
                profissional, entrada.validated_data, agora, para_atualizar=True
            )
            if agendamento.status not in STATUS_ATIVOS:
                raise NaoCancelavel()
            if agora >= _limite_cancelamento(agendamento, config):
                raise ForaDoPrazo()
            try:
                agendamento.aplicar_status(Agendamento.Status.CANCELADO_CLIENTE)
            except TransicaoAgendamentoInvalida:
                raise NaoCancelavel()
        return Response({"codigo": agendamento.codigo_curto, "status": agendamento.status})
