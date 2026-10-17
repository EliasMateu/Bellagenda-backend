"""Servico de horarios livres, sem HTTP."""

from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase

from apps.core.testutils import criar_agendamento, criar_cliente, criar_profissional, criar_servico
from apps.servicos.models import Servico

from .horarios import horario_oferecido, horarios_livres, proximos_horarios
from .models import Agendamento, Bloqueio, Configuracao, HorarioTrabalho

UTC = dt_timezone.utc
SP = ZoneInfo("America/Sao_Paulo")
SEGUNDA = date(2030, 1, 7)  # segunda-feira
AGORA = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


def hhmm(horarios):
    return [h.strftime("%H:%M") for h in horarios]


def sp(dia, h, m=0):
    return datetime.combine(dia, time(h, m), tzinfo=SP)


class HorariosBase(TestCase):
    def setUp(self):
        self.p = criar_profissional("calita")
        self.servico = criar_servico(self.p, duracao_min=60)
        HorarioTrabalho.objects.create(profissional=self.p, dia_semana=0, inicio=time(9), fim=time(12))

    def livres(self, dia=SEGUNDA, agora=AGORA, servico=None):
        return horarios_livres(self.p, servico or self.servico, dia, agora)


class GradeTests(HorariosBase):
    def test_grade_de_15_minutos_ate_caber_o_servico(self):
        livres = hhmm(self.livres())
        self.assertEqual(livres[0], "09:00")
        self.assertEqual(livres[-1], "11:00")  # 11:00 + 60 min = 12:00 (fim do expediente)
        self.assertEqual(len(livres), 9)

    def test_servico_de_45_min(self):
        servico = Servico.objects.create(
            profissional=self.p, nome="Curto", duracao_min=45, preco=Decimal("10")
        )
        self.assertEqual(hhmm(self.livres(servico=servico))[-1], "11:15")

    def test_dia_sem_expediente(self):
        self.assertEqual(self.livres(date(2030, 1, 8)), [])  # terca

    def test_fim_de_dia(self):
        HorarioTrabalho.objects.create(profissional=self.p, dia_semana=1, inicio=time(22), fim=time(23, 59))
        livres = hhmm(self.livres(date(2030, 1, 8)))
        self.assertEqual(livres[-1], "22:45")  # 23:00 + 60 min passaria da meia-noite

    def test_varios_expedientes_no_dia(self):
        HorarioTrabalho.objects.create(profissional=self.p, dia_semana=0, inicio=time(14), fim=time(15))
        livres = hhmm(self.livres())
        self.assertIn("14:00", livres)
        self.assertNotIn("14:15", livres)
        self.assertNotIn("12:00", livres)

    def test_retorno_no_fuso_da_profissional(self):
        self.assertEqual(self.livres()[0], sp(SEGUNDA, 9))
        self.assertEqual(str(self.livres()[0].utcoffset()), "-1 day, 21:00:00")


class BloqueioAgendamentoTests(HorariosBase):
    def test_bloqueio(self):
        Bloqueio.objects.create(profissional=self.p, inicio=sp(SEGUNDA, 10), fim=sp(SEGUNDA, 10, 30))
        # 09:00-10:00 encosta no bloqueio sem sobrepor; 10:30 em diante esta livre
        self.assertEqual(hhmm(self.livres()), ["09:00", "10:30", "10:45", "11:00"])

    def test_bloqueio_que_vem_do_dia_anterior_pela_meia_noite(self):
        Bloqueio.objects.create(
            profissional=self.p, inicio=sp(date(2030, 1, 6), 22), fim=sp(SEGUNDA, 9, 30)
        )
        self.assertEqual(hhmm(self.livres())[0], "09:30")

    def test_agendamento_usa_fim_ocupado(self):
        inicio = sp(SEGUNDA, 9)
        Agendamento.objects.create(
            profissional=self.p,
            cliente=criar_cliente(self.p),
            servico=self.servico,
            inicio=inicio,
            fim=inicio + timedelta(minutes=60),
            preco_cobrado=Decimal("50"),
            token_hash="a" * 64,
        )
        # termina 10:00, ocupado ate 10:10 -> proximo livre e 10:15
        self.assertEqual(hhmm(self.livres())[0], "10:15")

    def test_agendamento_cancelado_nao_bloqueia(self):
        ag = criar_agendamento(self.p, servico=self.servico)
        ag.status = Agendamento.Status.CANCELADO_CLIENTE
        ag.save()
        self.assertEqual(hhmm(self.livres())[0], "09:00")

    def test_intervalo_da_configuracao(self):
        Configuracao.objects.create(profissional=self.p, intervalo_entre_atendimentos_min=30)
        criar_agendamento(self.p, servico=self.servico)  # 07:00-08:00 local
        HorarioTrabalho.objects.filter(profissional=self.p).update(inicio=time(7), fim=time(12))
        # ocupado ate 08:30 -> primeiro livre 08:30
        self.assertEqual(hhmm(self.livres())[0], "08:30")


class AntecedenciaJanelaTests(HorariosBase):
    def test_antecedencia_minima_padrao_2h(self):
        agora = sp(SEGUNDA, 8).astimezone(UTC)  # 08:00 local
        self.assertEqual(hhmm(self.livres(agora=agora))[0], "10:00")

    def test_antecedencia_configuravel(self):
        Configuracao.objects.create(profissional=self.p, antecedencia_min_agendamento_h=4)
        agora = sp(SEGUNDA, 6).astimezone(UTC)  # 06:00 + 4 h = 10:00 (com 2 h seria 09:00)
        self.assertEqual(hhmm(self.livres(agora=agora))[0], "10:00")

    def test_dia_passado(self):
        self.assertEqual(self.livres(agora=sp(date(2030, 1, 8), 8).astimezone(UTC)), [])

    def test_alem_da_janela_maxima(self):
        agora = datetime(2029, 10, 1, 12, tzinfo=UTC)  # > 60 dias antes de 2030-01-07
        self.assertEqual(self.livres(agora=agora), [])

    def test_dentro_da_janela(self):
        agora = datetime(2029, 11, 20, 12, tzinfo=UTC)  # 48 dias antes
        self.assertNotEqual(self.livres(agora=agora), [])

    def test_horario_oferecido_e_proximos(self):
        livres = self.livres()
        self.assertTrue(horario_oferecido(self.p, self.servico, livres[0], AGORA))
        self.assertFalse(horario_oferecido(self.p, self.servico, livres[0] + timedelta(minutes=7), AGORA))
        proximos = proximos_horarios(self.p, self.servico, livres[0], limite=3, agora=AGORA)
        self.assertEqual(hhmm(proximos), ["09:15", "09:30", "09:45"])

    def test_sem_configuracao_usa_padroes(self):
        self.assertFalse(Configuracao.objects.filter(profissional=self.p).exists())
        self.assertEqual(len(self.livres()), 9)


class HorarioDeVeraoTests(TestCase):
    """Bordas de DST usando um fuso que ainda o pratica (Brasil nao tem mais)."""

    def _prep(self, dia_semana, inicio, fim):
        p = criar_profissional("nova-york")
        Configuracao.objects.create(profissional=p, timezone="America/New_York")
        HorarioTrabalho.objects.create(profissional=p, dia_semana=dia_semana, inicio=inicio, fim=fim)
        servico = Servico.objects.create(profissional=p, nome="S", duracao_min=30, preco=Decimal("1"))
        return p, servico

    def test_horario_inexistente_no_salto_de_primavera_e_pulado(self):
        p, servico = self._prep(6, time(1), time(4))  # domingo 2030-03-10: 02:00 vira 03:00
        agora = datetime(2030, 3, 1, 12, tzinfo=UTC)
        livres = horarios_livres(p, servico, date(2030, 3, 10), agora)
        locais = hhmm(livres)
        self.assertEqual(locais[:4], ["01:00", "01:15", "01:30", "01:45"])
        self.assertFalse([h for h in locais if h.startswith("02")])
        self.assertEqual(locais[4], "03:00")
        self.assertEqual(locais[-1], "03:30")  # termina as 04:00 (EDT)

    def test_horario_repetido_no_outono_nao_duplica(self):
        p, servico = self._prep(6, time(0), time(3))  # domingo 2030-11-03: 01:00-02:00 repete
        agora = datetime(2030, 10, 25, 12, tzinfo=UTC)
        livres = horarios_livres(p, servico, date(2030, 11, 3), agora)
        instantes = [h.astimezone(UTC) for h in livres]
        self.assertEqual(len(instantes), len(set(instantes)))
        self.assertEqual(instantes, sorted(instantes))
        self.assertEqual(len(livres), 11)  # 00:00..02:30 em horario de parede; so a 1a ocorrencia de 01:xx
