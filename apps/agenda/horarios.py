"""Calculo de horarios livres (sem HTTP, testavel isoladamente).

Regras: HorarioTrabalho do dia da semana, Bloqueio, agendamentos ativos (usando fim_ocupado),
duracao do servico + intervalo, antecedencia minima, janela maxima e fuso da Configuracao.
Sem Configuracao valem os padroes do modelo. Sem HorarioTrabalho no dia, nao ha horarios.
"""

from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import Agendamento, Bloqueio, Configuracao, HorarioTrabalho

PASSO_MIN = 15
STATUS_ATIVOS = (Agendamento.Status.PENDENTE, Agendamento.Status.CONFIRMADO)
UTC = dt_timezone.utc


def configuracao_efetiva(profissional):
    """Configuracao salva ou uma instancia nao salva com os padroes."""
    return (
        Configuracao.objects.filter(profissional=profissional).first()
        or Configuracao(profissional=profissional)
    )


def _hora_local(dia, hora, tz):
    """Instante (UTC) de um horario de parede local, ou None se ele nao existe (salto de DST)."""
    ingenuo = datetime.combine(dia, hora)
    local = ingenuo.replace(tzinfo=tz)  # fold=0 resolve horarios repetidos pela 1a ocorrencia
    ida_e_volta = local.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    if ida_e_volta != ingenuo:
        return None
    return local.astimezone(UTC)


def horarios_livres(
    profissional, servico, dia, agora=None, config=None, considerar_agendamentos=True
):
    """Lista de inicios livres (datetimes no fuso da profissional) para `dia` (date local)."""
    config = config or configuracao_efetiva(profissional)
    tz = ZoneInfo(config.timezone)
    agora = agora or timezone.now()
    minimo = agora + timedelta(hours=config.antecedencia_min_agendamento_h)
    maximo = agora + timedelta(days=config.janela_max_dias)
    duracao = timedelta(minutes=servico.duracao_min)
    intervalo = timedelta(minutes=config.intervalo_entre_atendimentos_min)

    faixa_ini = datetime.combine(dia - timedelta(days=1), time.min, tzinfo=UTC)
    faixa_fim = datetime.combine(dia + timedelta(days=2), time.min, tzinfo=UTC)
    bloqueios = list(
        Bloqueio.objects.filter(
            profissional=profissional, inicio__lt=faixa_fim, fim__gt=faixa_ini
        ).values_list("inicio", "fim")
    )
    ocupados = []
    if considerar_agendamentos:
        ocupados = list(
            Agendamento.objects.filter(
                profissional=profissional,
                status__in=STATUS_ATIVOS,
                inicio__lt=faixa_fim,
                fim_ocupado__gt=faixa_ini,
            ).values_list("inicio", "fim_ocupado")
        )

    expedientes = HorarioTrabalho.objects.filter(
        profissional=profissional, dia_semana=dia.weekday()
    ).order_by("inicio")

    livres = set()
    for expediente in expedientes:
        fim_expediente = _hora_local(dia, expediente.fim, tz)
        if fim_expediente is None:
            continue
        primeiro = expediente.inicio.hour * 60 + expediente.inicio.minute
        ultimo = expediente.fim.hour * 60 + expediente.fim.minute
        for minuto in range(primeiro, ultimo, PASSO_MIN):
            inicio = _hora_local(dia, time(minuto // 60, minuto % 60), tz)
            if inicio is None:
                continue
            fim = inicio + duracao
            if fim > fim_expediente or inicio < minimo or inicio > maximo:
                continue
            if any(b_ini < fim and inicio < b_fim for b_ini, b_fim in bloqueios):
                continue
            fim_ocupado = fim + intervalo
            if any(a_ini < fim_ocupado and inicio < a_fim for a_ini, a_fim in ocupados):
                continue
            livres.add(inicio)
    return [inicio.astimezone(tz) for inicio in sorted(livres)]


def horario_oferecido(profissional, servico, inicio, agora=None, considerar_agendamentos=True):
    """True se `inicio` (datetime aware) esta entre os horarios oferecidos para o servico."""
    config = configuracao_efetiva(profissional)
    dia = inicio.astimezone(ZoneInfo(config.timezone)).date()
    livres = horarios_livres(
        profissional, servico, dia, agora, config, considerar_agendamentos
    )
    return inicio.astimezone(UTC) in {h.astimezone(UTC) for h in livres}


def proximos_horarios(profissional, servico, a_partir_de, limite=5, dias=7, agora=None):
    """Proximos horarios livres depois de `a_partir_de`, olhando ate `dias` dias a frente."""
    config = configuracao_efetiva(profissional)
    tz = ZoneInfo(config.timezone)
    primeiro_dia: date = a_partir_de.astimezone(tz).date()
    encontrados = []
    for deslocamento in range(dias + 1):
        dia = primeiro_dia + timedelta(days=deslocamento)
        for horario in horarios_livres(profissional, servico, dia, agora, config):
            if horario > a_partir_de:
                encontrados.append(horario)
                if len(encontrados) >= limite:
                    return encontrados
    return encontrados
