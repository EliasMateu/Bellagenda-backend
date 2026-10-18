from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import TenantModel

from .tokens import gerar_codigo_curto

INTERVALO_PADRAO_MIN = 10


class TsTzRange(models.Func):
    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


def validar_timezone(valor):
    try:
        ZoneInfo(valor)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValidationError("Fuso horario invalido.", code="timezone_invalido")


class Configuracao(TenantModel):
    # Sobrescreve a FK abstrata: uma configuracao por profissional.
    profissional = models.OneToOneField(
        "contas.Profissional",
        on_delete=models.PROTECT,
        related_name="configuracao",
    )
    timezone = models.CharField(
        max_length=64, default="America/Sao_Paulo", validators=[validar_timezone]
    )
    antecedencia_min_cancelamento_h = models.PositiveSmallIntegerField(default=24)
    antecedencia_min_agendamento_h = models.PositiveSmallIntegerField(default=2)
    janela_max_dias = models.PositiveSmallIntegerField(
        default=60, validators=[MinValueValidator(1)]
    )
    intervalo_entre_atendimentos_min = models.PositiveSmallIntegerField(default=10)
    confirmacao_automatica = models.BooleanField(default=False)

    def __str__(self):
        return f"Configuracao de {self.profissional_id}"


class HorarioTrabalho(TenantModel):
    dia_semana = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(6)],
        help_text="0 = segunda-feira ... 6 = domingo",
    )
    inicio = models.TimeField()
    fim = models.TimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profissional", "dia_semana", "inicio"],
                name="horario_unico_por_profissional_dia_inicio",
            ),
            models.CheckConstraint(
                condition=models.Q(fim__gt=models.F("inicio")), name="horario_fim_apos_inicio"
            ),
            models.CheckConstraint(
                condition=models.Q(dia_semana__gte=0, dia_semana__lte=6),
                name="horario_dia_semana_valido",
            ),
        ]

    def clean(self):
        if self.inicio and self.fim and self.fim <= self.inicio:
            raise ValidationError({"fim": "O fim deve ser depois do inicio."})


class Bloqueio(TenantModel):
    inicio = models.DateTimeField()
    fim = models.DateTimeField()
    motivo = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fim__gt=models.F("inicio")), name="bloqueio_fim_apos_inicio"
            ),
        ]

    def clean(self):
        if self.inicio and self.fim and self.fim <= self.inicio:
            raise ValidationError({"fim": "O fim deve ser depois do inicio."})


class Agendamento(TenantModel):
    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        CONFIRMADO = "confirmado", "Confirmado"
        CANCELADO_CLIENTE = "cancelado_cliente", "Cancelado pelo cliente"
        CANCELADO_SALAO = "cancelado_salao", "Cancelado pela profissional"
        CONCLUIDO = "concluido", "Concluido"
        FALTA = "falta", "Falta"

    class Origem(models.TextChoices):
        ONLINE = "online", "Online"
        BALCAO = "balcao", "Balcao"

    cliente = models.ForeignKey(
        "clientes.Cliente", on_delete=models.PROTECT, related_name="agendamentos"
    )
    servico = models.ForeignKey(
        "servicos.Servico", on_delete=models.PROTECT, related_name="agendamentos"
    )
    inicio = models.DateTimeField()
    fim = models.DateTimeField()
    # fim + intervalo entre atendimentos; e o que a restricao de exclusao usa.
    fim_ocupado = models.DateTimeField(editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDENTE
    )
    preco_cobrado = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(0)]
    )
    codigo_curto = models.CharField(max_length=8, blank=True)
    # sha256 hex do token do link publico; o token em si nunca e guardado.
    token_hash = models.CharField(max_length=64, unique=True)
    origem = models.CharField(max_length=10, choices=Origem.choices, default=Origem.ONLINE)
    # Recado deixado pelo cliente no agendamento publico (nao altera o cadastro do cliente).
    observacao_cliente = models.CharField(max_length=500, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["profissional", "inicio"], name="agend_prof_inicio_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["profissional", "codigo_curto"],
                name="agendamento_codigo_unico_por_profissional",
            ),
            models.CheckConstraint(
                condition=models.Q(fim__gt=models.F("inicio")), name="agendamento_fim_apos_inicio"
            ),
            # Conflito de horario garantido pelo PostgreSQL (D-14/D-20): na mesma profissional,
            # intervalos [inicio, fim_ocupado) ativos nao podem se sobrepor.
            ExclusionConstraint(
                name="agendamento_sem_sobreposicao",
                expressions=[
                    ("profissional", RangeOperators.EQUAL),
                    (TsTzRange("inicio", "fim_ocupado", RangeBoundary()), RangeOperators.OVERLAPS),
                ],
                condition=models.Q(status__in=["pendente", "confirmado"]),
            ),
        ]

    def __str__(self):
        return f"{self.codigo_curto} {self.inicio:%d/%m/%Y %H:%M}"

    def _intervalo_min(self):
        intervalo = (
            Configuracao.objects.filter(profissional_id=self.profissional_id)
            .values_list("intervalo_entre_atendimentos_min", flat=True)
            .first()
        )
        return INTERVALO_PADRAO_MIN if intervalo is None else intervalo

    def clean(self):
        erros = {}
        if self.inicio and self.fim and self.fim <= self.inicio:
            erros["fim"] = "O fim deve ser depois do inicio."
        if self.cliente_id and self.cliente.profissional_id != self.profissional_id:
            erros["cliente"] = "O cliente pertence a outra profissional."
        if self.servico_id and self.servico.profissional_id != self.profissional_id:
            erros["servico"] = "O servico pertence a outra profissional."
        if erros:
            raise ValidationError(erros)

    def save(self, *args, **kwargs):
        """Valida tenant e datas, calcula fim_ocupado e gera o codigo curto se ausente."""
        self.clean()
        self.fim_ocupado = self.fim + timedelta(minutes=self._intervalo_min())
        if self._state.adding and not self.codigo_curto:
            self.codigo_curto = self._novo_codigo_curto()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = {*update_fields, "fim_ocupado", "codigo_curto"}
        super().save(*args, **kwargs)

    def _novo_codigo_curto(self):
        for _ in range(10):
            codigo = gerar_codigo_curto()
            if not Agendamento.objects.filter(
                profissional_id=self.profissional_id, codigo_curto=codigo
            ).exists():
                return codigo
        raise ValidationError("Nao foi possivel gerar um codigo curto unico.")
