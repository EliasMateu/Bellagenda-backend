"""DB-03: o PostgreSQL impede agendamentos sobrepostos da mesma profissional."""

import threading
from datetime import timedelta

from django.db import IntegrityError, OperationalError, connection, transaction
from django.test import TestCase, TransactionTestCase

from apps.core.testutils import (
    BASE,
    criar_agendamento,
    criar_cliente,
    criar_profissional,
    criar_servico,
)

from .models import Agendamento


class SobreposicaoTests(TestCase):
    def setUp(self):
        self.a = criar_profissional("prof-a")
        self.b = criar_profissional("prof-b")
        # A: 10:00-11:00 (ocupado ate 11:10)
        self.base = criar_agendamento(self.a, inicio_min=0, duracao_min=60)

    def _tentar(self, profissional, inicio_min, duracao_min=60, **extra):
        with transaction.atomic():
            return criar_agendamento(profissional, inicio_min, duracao_min, **extra)

    def test_sobreposicao_no_mesmo_profissional_falha(self):
        for inicio_min in (0, 30, 59, -30):
            with self.subTest(inicio_min=inicio_min):
                with self.assertRaises(IntegrityError):
                    self._tentar(self.a, inicio_min)

    def test_mesmo_horario_em_profissionais_diferentes_passa(self):
        self._tentar(self.b, 0)
        self.assertEqual(Agendamento.objects.count(), 2)

    def test_cancelados_nao_bloqueiam(self):
        for status in (
            Agendamento.Status.CANCELADO_CLIENTE,
            Agendamento.Status.CANCELADO_SALAO,
            Agendamento.Status.CONCLUIDO,
            Agendamento.Status.FALTA,
        ):
            with self.subTest(status=status):
                self._tentar(self.a, 0, status=status)

    def test_pendente_e_confirmado_bloqueiam(self):
        Agendamento.objects.filter(pk=self.base.pk).update(status=Agendamento.Status.CONFIRMADO)
        with self.assertRaises(IntegrityError):
            self._tentar(self.a, 30)

    def test_cancelar_libera_o_horario(self):
        Agendamento.objects.filter(pk=self.base.pk).update(
            status=Agendamento.Status.CANCELADO_CLIENTE
        )
        self._tentar(self.a, 0)

    def test_reativar_cancelado_em_horario_ocupado_falha(self):
        cancelado = self._tentar(self.a, 0, status=Agendamento.Status.CANCELADO_CLIENTE)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Agendamento.objects.filter(pk=cancelado.pk).update(status=Agendamento.Status.PENDENTE)

    def test_intervalo_entre_atendimentos_colados(self):
        # base termina 11:00 e fica ocupado ate 11:10 (padrao)
        with self.assertRaises(IntegrityError):
            self._tentar(self.a, 60)  # 11:00 colado
        with self.assertRaises(IntegrityError):
            self._tentar(self.a, 65)  # 11:05 dentro do intervalo
        self._tentar(self.a, 70)  # 11:10 exatamente: permitido

    def test_novo_antes_respeita_intervalo_proprio(self):
        # 08:50-09:50 (ocupado ate 10:00): encosta no inicio da base, sem sobrepor
        self._tentar(self.a, -70)
        # 08:55-09:55 ocuparia ate 10:05 e invade a base
        with self.assertRaises(IntegrityError):
            self._tentar(self.a, -65)

    def test_constraint_existe_com_btree_gist(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
            self.assertIsNotNone(cursor.fetchone())
            cursor.execute(
                "SELECT 1 FROM pg_constraint WHERE conname = 'agendamento_sem_sobreposicao' "
                "AND contype = 'x'"
            )
            self.assertIsNotNone(cursor.fetchone())


class SobreposicaoConcorrenteTests(TransactionTestCase):
    def test_duas_insercoes_simultaneas_so_uma_vence(self):
        a = criar_profissional("prof-a")
        clientes = [criar_cliente(a), criar_cliente(a)]
        servico = criar_servico(a)
        barreira = threading.Barrier(2)
        resultados = []

        def inserir(cliente):
            try:
                barreira.wait(timeout=10)
                with transaction.atomic():
                    criar_agendamento(a, inicio_min=0, cliente=cliente, servico=servico)
                resultados.append("ok")
            except IntegrityError:
                resultados.append("conflito")
            except OperationalError as erro:
                # Sem o lock da API, o PostgreSQL pode resolver a corrida abortando uma das
                # transacoes por deadlock; ainda assim so uma insercao vence.
                resultados.append("conflito" if "deadlock" in str(erro).lower() else f"erro: {erro!r}")
            except Exception as erro:  # noqa: BLE001 - qualquer outra falha deve reprovar o teste
                resultados.append(f"erro: {erro!r}")
            finally:
                connection.close()

        threads = [threading.Thread(target=inserir, args=(c,)) for c in clientes]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(sorted(resultados), ["conflito", "ok"], resultados)
        self.assertEqual(Agendamento.objects.count(), 1)
        self.assertEqual(
            Agendamento.objects.get().fim_ocupado,
            BASE + timedelta(minutes=70),
        )
