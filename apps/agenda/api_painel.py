"""API do painel da profissional (autenticada, sempre dentro do tenant)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clientes.models import Cliente
from apps.clientes.telefone import normalizar_telefone_br
from apps.contas.models import Profissional
from apps.core.exceptions import ErroApi
from apps.core.tenancy import ProfissionalAtiva, TenantModelViewSet, TenantSerializer

from .horarios import configuracao_efetiva, horario_oferecido
from .models import (
    Agendamento,
    Bloqueio,
    Configuracao,
    HorarioTrabalho,
    TransicaoAgendamentoInvalida,
)
from .tokens import gerar_token_e_hash


# ---------------------------------------------------------------- horarios de trabalho

class HorarioTrabalhoSerializer(TenantSerializer):
    class Meta:
        model = HorarioTrabalho
        fields = ["id", "dia_semana", "inicio", "fim"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instancia = self.instance
        dia = attrs.get("dia_semana", getattr(instancia, "dia_semana", None))
        inicio = attrs.get("inicio", getattr(instancia, "inicio", None))
        fim = attrs.get("fim", getattr(instancia, "fim", None))
        if fim <= inicio:
            raise serializers.ValidationError({"fim": ["O fim deve ser depois do inicio."]})
        outros = HorarioTrabalho.objects.do_profissional(self._profissional()).filter(
            dia_semana=dia, inicio__lt=fim, fim__gt=inicio
        )
        if instancia is not None:
            outros = outros.exclude(pk=instancia.pk)
        if outros.exists():
            raise serializers.ValidationError(
                {"inicio": ["Esta faixa sobrepoe outra faixa do mesmo dia."]}
            )
        return attrs


class HorarioTrabalhoViewSet(TenantModelViewSet):
    queryset = HorarioTrabalho.objects.order_by("dia_semana", "inicio")
    serializer_class = HorarioTrabalhoSerializer


# ---------------------------------------------------------------- bloqueios

class BloqueioSerializer(TenantSerializer):
    class Meta:
        model = Bloqueio
        fields = ["id", "inicio", "fim", "motivo"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        inicio = attrs.get("inicio", getattr(self.instance, "inicio", None))
        fim = attrs.get("fim", getattr(self.instance, "fim", None))
        if fim <= inicio:
            raise serializers.ValidationError({"fim": ["O fim deve ser depois do inicio."]})
        return attrs


class BloqueioViewSet(TenantModelViewSet):
    queryset = Bloqueio.objects.order_by("inicio")
    serializer_class = BloqueioSerializer


# ---------------------------------------------------------------- configuracao (singleton)

class ConfiguracaoSerializer(TenantSerializer):
    class Meta:
        model = Configuracao
        fields = [
            "timezone",
            "antecedencia_min_cancelamento_h",
            "antecedencia_min_agendamento_h",
            "janela_max_dias",
            "intervalo_entre_atendimentos_min",
            "confirmacao_automatica",
        ]


class ConfiguracaoView(APIView):
    """GET/PATCH da configuracao da profissional logada (criada com padroes no 1o acesso)."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [ProfissionalAtiva]

    def _configuracao(self, request):
        """Singleton do tenant; tolera dois primeiros acessos simultaneos (IntegrityError -> rele)."""
        profissional = request.user.profissional
        try:
            return Configuracao.objects.get(profissional=profissional)
        except Configuracao.DoesNotExist:
            pass
        try:
            with transaction.atomic():
                return Configuracao.objects.create(profissional=profissional)
        except IntegrityError:
            return Configuracao.objects.get(profissional=profissional)

    def get(self, request):
        serializer = ConfiguracaoSerializer(
            self._configuracao(request), context={"request": request}
        )
        return Response(serializer.data)

    def patch(self, request):
        serializer = ConfiguracaoSerializer(
            self._configuracao(request),
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(profissional=request.user.profissional)
        return Response(serializer.data)


# ---------------------------------------------------------------- agendamentos

CAMPOS_PROTEGIDOS = {
    "status", "token", "token_hash", "codigo_curto", "codigo", "preco_cobrado",
    "profissional", "inicio", "fim", "fim_ocupado", "cliente", "servico", "origem",
}


class ClienteResumoSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    nome = serializers.CharField()
    telefone = serializers.CharField()  # E.164 normalizado; o front monta o wa.me


class ServicoResumoSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    nome = serializers.CharField()
    duracao_min = serializers.IntegerField()


class AgendamentoSerializer(TenantSerializer):
    """Leitura de agendamento para o painel (nunca expoe token_hash)."""

    cliente = ClienteResumoSerializer(read_only=True)
    servico = ServicoResumoSerializer(read_only=True)

    class Meta:
        model = Agendamento
        fields = [
            "id", "cliente", "servico", "inicio", "fim", "fim_ocupado", "status",
            "preco_cobrado", "codigo_curto", "origem", "observacao_cliente",
            "criado_em", "atualizado_em",
        ]
        read_only_fields = fields


class AgendamentoEdicaoSerializer(TenantSerializer):
    """Unico campo editavel diretamente: o recado/observacao."""

    class Meta:
        model = Agendamento
        fields = ["observacao_cliente"]


class AgendamentoCriarSerializer(TenantSerializer):
    """Agendamento manual no balcao: cliente existente (id) ou novo (nome + telefone)."""

    cliente_nome = serializers.CharField(required=False, min_length=2, max_length=100, write_only=True)
    cliente_telefone = serializers.CharField(required=False, max_length=30, write_only=True)
    override_grade = serializers.BooleanField(required=False, default=False, write_only=True)

    class Meta:
        model = Agendamento
        fields = [
            "servico", "inicio", "cliente", "observacao_cliente",
            "cliente_nome", "cliente_telefone", "override_grade",
        ]
        extra_kwargs = {"cliente": {"required": False}}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        servico = attrs["servico"]
        if not servico.ativo:
            raise serializers.ValidationError({"servico": ["Servico inativo."]})
        if not attrs.get("cliente"):
            if not (attrs.get("cliente_nome") and attrs.get("cliente_telefone")):
                raise serializers.ValidationError(
                    {"cliente": ["Informe o cliente (id) ou cliente_nome e cliente_telefone."]}
                )
            try:
                attrs["cliente_telefone"] = normalizar_telefone_br(attrs["cliente_telefone"])
            except DjangoValidationError:
                raise serializers.ValidationError(
                    {"cliente_telefone": ["Telefone invalido. Use DDD + numero."]}
                )
        if not attrs.get("override_grade") and not horario_oferecido(
            self._profissional(), servico, attrs["inicio"], timezone.now(),
            considerar_agendamentos=False, aplicar_antecedencia=False,
        ):
            raise serializers.ValidationError(
                {"inicio": ["Horario fora da grade de trabalho ou bloqueado (use override_grade)."]}
            )
        return attrs

    def create(self, validated_data):
        profissional = validated_data["profissional"]
        servico = validated_data["servico"]
        # Serializa criacoes concorrentes da mesma profissional (evita deadlock da exclusao).
        Profissional.objects.select_for_update(no_key=True).get(pk=profissional.pk)
        cliente = validated_data.get("cliente")
        if cliente is None:
            cliente, _ = Cliente.objects.get_or_create(
                profissional=profissional,
                telefone=validated_data["cliente_telefone"],
                defaults={"nome": validated_data["cliente_nome"]},
            )
        inicio = validated_data["inicio"]
        _, token_hash = gerar_token_e_hash()  # o painel nao usa o link publico; so o hash e gravado
        agendamento = Agendamento(
            profissional=profissional,
            cliente=cliente,
            servico=servico,
            inicio=inicio,
            fim=inicio + timedelta(minutes=servico.duracao_min),
            status=Agendamento.Status.CONFIRMADO,
            preco_cobrado=servico.preco,
            token_hash=token_hash,
            origem=Agendamento.Origem.BALCAO,
            observacao_cliente=validated_data.get("observacao_cliente", ""),
        )
        agendamento.save()
        return agendamento


class RemarcarSerializer(serializers.Serializer):
    inicio = serializers.DateTimeField()
    override_grade = serializers.BooleanField(required=False, default=False)


class TransicaoNaoPermitida(ErroApi):
    status_code = status.HTTP_409_CONFLICT
    code = "transicao_invalida"
    default_detail = "Esta mudanca de status nao e permitida."


class AgendamentoViewSet(TenantModelViewSet):
    queryset = Agendamento.objects.select_related("cliente", "servico").order_by("inicio")
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return AgendamentoCriarSerializer
        if self.action == "partial_update":
            return AgendamentoEdicaoSerializer
        return AgendamentoSerializer

    def _saida(self, agendamento):
        agendamento = self.get_queryset().get(pk=agendamento.pk)
        return AgendamentoSerializer(agendamento, context=self.get_serializer_context()).data

    # ----- listagem com filtros (datas no fuso da profissional)
    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != "list":
            return queryset
        params = self.request.query_params
        tz = ZoneInfo(configuracao_efetiva(self.request.user.profissional).timezone)
        erros = {}

        def data_param(nome):
            bruto = params.get(nome)
            if not bruto:
                return None
            try:
                return date.fromisoformat(bruto)
            except ValueError:
                erros[nome] = ["Use o formato AAAA-MM-DD."]
                return None

        de, ate = data_param("de"), data_param("ate")
        status_param = [s for s in params.get("status", "").split(",") if s]
        invalidos = [s for s in status_param if s not in Agendamento.Status.values]
        if invalidos:
            erros["status"] = [f"Status invalido: {', '.join(invalidos)}."]
        if erros:
            raise serializers.ValidationError(erros)
        if de:
            queryset = queryset.filter(inicio__gte=datetime.combine(de, time.min, tzinfo=tz))
        if ate:
            queryset = queryset.filter(
                inicio__lt=datetime.combine(ate + timedelta(days=1), time.min, tzinfo=tz)
            )
        if status_param:
            queryset = queryset.filter(status__in=status_param)
        return queryset

    # ----- criacao manual (balcao)
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(self._saida(serializer.instance), status=status.HTTP_201_CREATED)

    # ----- PATCH: so a observacao; campos sensiveis sao recusados explicitamente
    def partial_update(self, request, *args, **kwargs):
        self.get_object()  # 404 antes de qualquer outra resposta
        proibidos = sorted(CAMPOS_PROTEGIDOS & set(request.data))
        if proibidos:
            raise serializers.ValidationError(
                {campo: ["Campo somente leitura; use as acoes do agendamento."] for campo in proibidos}
            )
        resposta = super().partial_update(request, *args, **kwargs)
        return Response(self._saida(self.get_object()), status=resposta.status_code)

    # ----- acoes de status
    def _mudar_status(self, novo, permitido_de=None):
        agendamento = self.get_object()  # 404 para objeto de outro tenant
        with transaction.atomic():
            travado = Agendamento.objects.select_for_update().get(pk=agendamento.pk)
            try:
                if permitido_de and travado.status not in permitido_de:
                    raise TransicaoAgendamentoInvalida("fora do estado esperado")
                travado.aplicar_status(novo)
            except TransicaoAgendamentoInvalida:
                raise TransicaoNaoPermitida(
                    f"Nao e possivel mudar de {travado.status} para {novo}."
                )
        return Response(self._saida(travado))

    @action(detail=True, methods=["post"])
    def confirmar(self, request, pk=None):
        return self._mudar_status(Agendamento.Status.CONFIRMADO)

    @action(detail=True, methods=["post"])
    def recusar(self, request, pk=None):
        return self._mudar_status(
            Agendamento.Status.CANCELADO_SALAO, permitido_de={Agendamento.Status.PENDENTE}
        )

    @action(detail=True, methods=["post"])
    def cancelar(self, request, pk=None):
        """Cancela. Corpo opcional {"origem": "cliente"} registra como cancelado pelo cliente."""
        if request.data.get("origem") == "cliente":
            return self._mudar_status(Agendamento.Status.CANCELADO_CLIENTE)
        return self._mudar_status(Agendamento.Status.CANCELADO_SALAO)

    @action(detail=True, methods=["post"])
    def concluir(self, request, pk=None):
        return self._mudar_status(Agendamento.Status.CONCLUIDO)

    @action(detail=True, methods=["post"])
    def faltou(self, request, pk=None):
        return self._mudar_status(Agendamento.Status.FALTA)

    @action(detail=True, methods=["post"])
    def remarcar(self, request, pk=None):
        agendamento = self.get_object()  # 404 para objeto de outro tenant
        entrada = RemarcarSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        novo_inicio = entrada.validated_data["inicio"]
        with transaction.atomic():
            Profissional.objects.select_for_update(no_key=True).get(pk=agendamento.profissional_id)
            travado = Agendamento.objects.select_for_update().select_related("servico").get(
                pk=agendamento.pk
            )
            if travado.status not in (Agendamento.Status.PENDENTE, Agendamento.Status.CONFIRMADO):
                raise TransicaoNaoPermitida("So e possivel remarcar agendamento pendente ou confirmado.")
            if not entrada.validated_data["override_grade"] and not horario_oferecido(
                travado.profissional, travado.servico, novo_inicio, timezone.now(),
                considerar_agendamentos=False, aplicar_antecedencia=False,
            ):
                raise serializers.ValidationError(
                    {"inicio": ["Horario fora da grade de trabalho ou bloqueado (use override_grade)."]}
                )
            duracao = travado.fim - travado.inicio
            travado.inicio = novo_inicio
            travado.fim = novo_inicio + duracao
            travado.save()  # conflito => IntegrityError => 409 conflito_horario
        return Response(self._saida(travado))
