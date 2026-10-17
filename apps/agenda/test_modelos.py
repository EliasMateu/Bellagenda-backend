from datetime import time, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.core.testutils import BASE, criar_agendamento, criar_cliente, criar_profissional, criar_servico

from .models import Agendamento, Bloqueio, Configuracao, HorarioTrabalho


class ConfiguracaoTests(TestCase):
    def test_padroes(self):
        c = Configuracao.objects.create(profissional=criar_profissional("prof-a"))
        self.assertEqual(c.timezone, "America/Sao_Paulo")
        self.assertEqual(c.antecedencia_min_cancelamento_h, 24)
        self.assertEqual(c.antecedencia_min_agendamento_h, 2)
        self.assertEqual(c.janela_max_dias, 60)
        self.assertEqual(c.intervalo_entre_atendimentos_min, 10)
        self.assertFalse(c.confirmacao_automatica)

    def test_uma_por_profissional(self):
        p = criar_profissional("prof-a")
        Configuracao.objects.create(profissional=p)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Configuracao.objects.create(profissional=p)

    def test_timezone_invalido(self):
        c = Configuracao(profissional=criar_profissional("prof-a"), timezone="Marte/Olympus")
        with self.assertRaises(ValidationError):
            c.full_clean()


class HorarioBloqueioTests(TestCase):
    def setUp(self):
        self.p = criar_profissional("prof-a")

    def test_horario_fim_depois_do_inicio(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            HorarioTrabalho.objects.create(
                profissional=self.p, dia_semana=0, inicio=time(18), fim=time(9)
            )

    def test_horario_unico(self):
        HorarioTrabalho.objects.create(profissional=self.p, dia_semana=0, inicio=time(9), fim=time(12))
        with self.assertRaises(IntegrityError), transaction.atomic():
            HorarioTrabalho.objects.create(profissional=self.p, dia_semana=0, inicio=time(9), fim=time(18))

    def test_dia_semana_entre_0_e_6(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            HorarioTrabalho.objects.create(profissional=self.p, dia_semana=7, inicio=time(9), fim=time(18))

    def test_bloqueio_fim_depois_do_inicio(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Bloqueio.objects.create(profissional=self.p, inicio=BASE, fim=BASE - timedelta(hours=1))


class AgendamentoModeloTests(TestCase):
    def setUp(self):
        self.a = criar_profissional("prof-a")
        self.b = criar_profissional("prof-b")

    def test_padroes_e_codigo_curto(self):
        ag = criar_agendamento(self.a)
        self.assertEqual(ag.status, Agendamento.Status.PENDENTE)
        self.assertEqual(ag.origem, Agendamento.Origem.ONLINE)
        self.assertRegex(ag.codigo_curto, r"^[A-HJKMNP-Z2-9]{8}$")
        self.assertEqual(len(ag.token_hash), 64)

    def test_fim_ocupado_padrao_10_min_sem_configuracao(self):
        ag = criar_agendamento(self.a)
        self.assertEqual(ag.fim_ocupado, ag.fim + timedelta(minutes=10))

    def test_fim_ocupado_usa_configuracao(self):
        Configuracao.objects.create(profissional=self.a, intervalo_entre_atendimentos_min=25)
        ag = criar_agendamento(self.a)
        self.assertEqual(ag.fim_ocupado, ag.fim + timedelta(minutes=25))

    def test_cliente_de_outro_tenant_recusado(self):
        with self.assertRaises(ValidationError) as ctx:
            criar_agendamento(self.a, cliente=criar_cliente(self.b))
        self.assertIn("cliente", ctx.exception.message_dict)

    def test_servico_de_outro_tenant_recusado(self):
        with self.assertRaises(ValidationError) as ctx:
            criar_agendamento(self.a, servico=criar_servico(self.b))
        self.assertIn("servico", ctx.exception.message_dict)

    def test_fim_deve_ser_depois_do_inicio(self):
        with self.assertRaises(ValidationError):
            criar_agendamento(self.a, duracao_min=-30)

    def test_codigo_curto_unico_por_profissional(self):
        criar_agendamento(self.a, codigo_curto="ABCDEFGH")
        with self.assertRaises(IntegrityError), transaction.atomic():
            criar_agendamento(self.a, inicio_min=600, codigo_curto="ABCDEFGH")
        criar_agendamento(self.b, codigo_curto="ABCDEFGH")  # outro tenant: permitido

    def test_token_hash_unico_global(self):
        ag = criar_agendamento(self.a)
        with self.assertRaises(IntegrityError), transaction.atomic():
            criar_agendamento(self.b, token_hash=ag.token_hash)
