"""Correcoes da revisao do Sentinela (parte 2c): matriz unica, fim_ocupado estavel, concorrencia."""

import threading
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest import mock

from django.db import IntegrityError, OperationalError, connection
from django.test import TestCase, TransactionTestCase, override_settings
from psycopg import errors as pg_errors
from rest_framework.test import APIClient

from apps.core.exceptions import tratar_excecao
from apps.core.testutils import (
    LIMITES_ALTOS,
    SP,
    cliente_logado,
    configurar_expediente,
    criar_agendamento,
    criar_cliente,
    criar_profissional,
    criar_servico,
    dia_futuro,
)

from .models import Agendamento, Configuracao
from .tokens import hash_token

P = "/api/v1/painel"
PUB = "/api/v1/publico/calita"


def local(dia, h, m=0):
    return datetime.combine(dia, time(h, m), tzinfo=SP)


def criar_direto(profissional, servico, inicio, token, status="pendente", minutos=60):
    return Agendamento.objects.create(
        profissional=profissional, cliente=criar_cliente(profissional), servico=servico,
        inicio=inicio, fim=inicio + timedelta(minutes=minutos), preco_cobrado=50,
        token_hash=hash_token(token), status=status,
    )


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class MatrizUnicaETransicoesTests(TestCase):
    def setUp(self):
        self.a = criar_profissional("calita")
        configurar_expediente(self.a)
        self.servico = criar_servico(self.a, "Esmaltacao")
        self.dia = dia_futuro(7)
        self.painel = cliente_logado(self.a.user)
        self.publico = APIClient()

    def cancelar_publico(self, token):
        return self.publico.post(f"{PUB}/agendamento/cancelar/", {"token": token}, format="json")

    def test_pendente_pode_virar_cancelado_cliente_na_matriz(self):
        permitidos = Agendamento.TRANSICOES[Agendamento.Status.PENDENTE]
        self.assertIn(Agendamento.Status.CANCELADO_CLIENTE, permitidos)

    def test_cancelamento_publico_usa_a_matriz(self):
        ag = criar_direto(self.a, self.servico, local(self.dia, 10), "tok-pub")
        sem_regra = {Agendamento.Status.PENDENTE: {Agendamento.Status.CONFIRMADO}}
        with mock.patch.dict(Agendamento.TRANSICOES, sem_regra):
            resposta = self.cancelar_publico("tok-pub")
        self.assertEqual(resposta.status_code, 409)
        self.assertEqual(resposta.json()["code"], "nao_cancelavel")
        ag.refresh_from_db()
        self.assertEqual(ag.status, "pendente")
        self.assertEqual(self.cancelar_publico("tok-pub").status_code, 200)
        ag.refresh_from_db()
        self.assertEqual(ag.status, "cancelado_cliente")

    def test_painel_cancelar_origem_cliente_em_pendente_e_confirmado(self):
        for inicial in ("pendente", "confirmado"):
            with self.subTest(inicial=inicial):
                ag = criar_direto(self.a, self.servico, local(self.dia, 10), f"tok-{inicial}", inicial)
                r = self.painel.post(f"{P}/agendamentos/{ag.pk}/cancelar/", {"origem": "cliente"}, format="json")
                self.assertEqual(r.status_code, 200, r.content)
                self.assertEqual(r.json()["status"], "cancelado_cliente")
                ag.delete()


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class FimOcupadoEstavelTests(TestCase):
    def setUp(self):
        self.a = criar_profissional("calita")
        configurar_expediente(self.a)
        self.servico = criar_servico(self.a, "Esmaltacao")
        self.dia = dia_futuro(7)
        self.painel = cliente_logado(self.a.user)

    def prepara_dois_colados(self):
        """ag1 10:00-11:00 (ocupado ate 11:10) e ag2 pendente a partir de 11:10, com intervalo de 10 min."""
        ag1 = criar_direto(self.a, self.servico, local(self.dia, 10), "tok-1")
        ag2 = criar_direto(self.a, self.servico, local(self.dia, 11, 10), "tok-2")
        return ag1, ag2

    def test_mudar_intervalo_e_confirmar_nao_altera_a_faixa_nem_da_409(self):
        ag1, ag2 = self.prepara_dois_colados()
        antes = Agendamento.objects.values_list("fim_ocupado", flat=True).get(pk=ag1.pk)
        self.painel.patch(f"{P}/configuracao/", {"intervalo_entre_atendimentos_min": 30}, format="json")
        # recalcular com 30 min invadiria o ag2 (11:10); o status nao pode mexer na faixa
        r = self.painel.post(f"{P}/agendamentos/{ag1.pk}/confirmar/")
        self.assertEqual(r.status_code, 200, r.content)
        ag1.refresh_from_db()
        self.assertEqual(ag1.status, "confirmado")
        self.assertEqual(ag1.fim_ocupado, antes)
        self.assertEqual(self.painel.post(f"{P}/agendamentos/{ag2.pk}/confirmar/").status_code, 200)

    def test_todas_as_transicoes_preservam_fim_ocupado(self):
        for acao, inicial in (("confirmar", "pendente"), ("recusar", "pendente"), ("cancelar", "confirmado"),
                              ("concluir", "confirmado"), ("faltou", "confirmado")):
            with self.subTest(acao=acao):
                ag = criar_direto(self.a, self.servico, local(self.dia, 14), f"tok-{acao}", inicial)
                Configuracao.objects.update_or_create(
                    profissional=self.a, defaults={"intervalo_entre_atendimentos_min": 25}
                )
                antes = ag.fim_ocupado
                self.assertEqual(self.painel.post(f"{P}/agendamentos/{ag.pk}/{acao}/").status_code, 200)
                ag.refresh_from_db()
                self.assertEqual(ag.fim_ocupado, antes)
                ag.delete()

    def test_patch_de_observacao_preserva_fim_ocupado(self):
        ag1, _ = self.prepara_dois_colados()
        self.painel.patch(f"{P}/configuracao/", {"intervalo_entre_atendimentos_min": 30}, format="json")
        antes = ag1.fim_ocupado
        r = self.painel.patch(f"{P}/agendamentos/{ag1.pk}/", {"observacao_cliente": "oi"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        ag1.refresh_from_db()
        self.assertEqual(ag1.fim_ocupado, antes)

    def test_remarcar_recalcula_com_o_intervalo_atual(self):
        ag1 = criar_direto(self.a, self.servico, local(self.dia, 10), "tok-1")
        self.painel.patch(f"{P}/configuracao/", {"intervalo_entre_atendimentos_min": 30}, format="json")
        r = self.painel.post(
            f"{P}/agendamentos/{ag1.pk}/remarcar/", {"inicio": local(self.dia, 15).isoformat()}, format="json"
        )
        self.assertEqual(r.status_code, 200, r.content)
        ag1.refresh_from_db()
        self.assertEqual(ag1.fim_ocupado - ag1.fim, timedelta(minutes=30))

    def test_save_direto_sem_mexer_no_horario_tambem_preserva(self):
        ag1, _ = self.prepara_dois_colados()
        Configuracao.objects.create(profissional=self.a, intervalo_entre_atendimentos_min=45)
        antes = ag1.fim_ocupado
        ag1.observacao_cliente = "x"
        ag1.save()
        ag1.refresh_from_db()
        self.assertEqual(ag1.fim_ocupado, antes)


def integridade(constraint):
    causa = Exception("causa")
    causa.diag = SimpleNamespace(constraint_name=constraint)
    erro = IntegrityError("x")
    erro.__cause__ = causa
    return erro


class ErrosDeBancoNoHandlerTests(TestCase):
    def test_deadlock_e_serialization_failure_viram_409_conflito_concorrente(self):
        for tipo in (pg_errors.DeadlockDetected, pg_errors.SerializationFailure):
            with self.subTest(tipo=tipo.__name__):
                erro = OperationalError("falha")
                erro.__cause__ = tipo("falha")
                resposta = tratar_excecao(erro, {})
                self.assertEqual(resposta.status_code, 409)
                self.assertEqual(resposta.data["code"], "conflito_concorrente")
                self.assertEqual(set(resposta.data), {"code", "message", "fields"})

    def test_outros_erros_operacionais_continuam_sendo_500(self):
        erro = OperationalError("conexao perdida")
        erro.__cause__ = pg_errors.AdminShutdown("fora")
        self.assertIsNone(tratar_excecao(erro, {}))
        self.assertIsNone(tratar_excecao(OperationalError("sem causa"), {}))

    def test_constraints_de_codigo_e_token_sao_controladas(self):
        for nome in ("agendamento_codigo_unico_por_profissional",
                     "agenda_agendamento_token_hash_4f1b2c3d_uniq"):
            with self.subTest(nome=nome):
                resposta = tratar_excecao(integridade(nome), {})
                self.assertEqual(resposta.status_code, 409)
                self.assertEqual(resposta.data["code"], "tente_novamente")

    def test_constraint_desconhecida_segue_sem_mapeamento(self):
        self.assertIsNone(tratar_excecao(integridade("qualquer_outra"), {}))

    @override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
    def test_deadlock_no_endpoint_vira_409(self):
        a = criar_profissional("calita")
        configurar_expediente(a)
        servico = criar_servico(a, "Esmaltacao")
        painel = cliente_logado(a.user)
        erro = OperationalError("deadlock detected")
        erro.__cause__ = pg_errors.DeadlockDetected("deadlock detected")
        corpo = {
            "servico": servico.pk, "inicio": local(dia_futuro(7), 10).isoformat(),
            "cliente_nome": "Maria", "cliente_telefone": "11998765432",
        }
        with mock.patch.object(Agendamento, "save", side_effect=erro):
            resposta = painel.post(f"{P}/agendamentos/", corpo, format="json")
        self.assertEqual(resposta.status_code, 409)
        self.assertEqual(resposta.json()["code"], "conflito_concorrente")
        self.assertFalse(Agendamento.objects.exists())


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class ConcorrenciaApiTests(TransactionTestCase):
    def setUp(self):
        self.a = criar_profissional("calita")
        configurar_expediente(self.a)
        self.servico = criar_servico(self.a, "Esmaltacao", duracao_min=60)
        self.dia = dia_futuro(7)

    def disparar(self, tarefas):
        """Roda as tarefas (callables sem argumentos) ao mesmo tempo; devolve os status HTTP."""
        barreira = threading.Barrier(len(tarefas))
        resultados = [None] * len(tarefas)

        def rodar(i, tarefa):
            try:
                barreira.wait(timeout=10)
                resultados[i] = tarefa()
            except Exception as erro:  # noqa: BLE001
                resultados[i] = repr(erro)
            finally:
                connection.close()

        threads = [threading.Thread(target=rodar, args=(i, t)) for i, t in enumerate(tarefas)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        return resultados

    def test_balcao_publico_e_remarcar_no_mesmo_horario_so_um_vence(self):
        existente = criar_direto(self.a, self.servico, local(self.dia, 14), "tok-x", "confirmado")
        alvo = local(self.dia, 10)
        painel_1, painel_2 = cliente_logado(self.a.user), cliente_logado(self.a.user)
        publico = APIClient()

        def balcao():
            return painel_1.post(f"{P}/agendamentos/", {
                "servico": self.servico.pk, "inicio": alvo.isoformat(),
                "cliente_nome": "Balcao", "cliente_telefone": "11911112222",
            }, format="json").status_code

        def online():
            return publico.post(f"{PUB}/agendamentos/", {
                "servico": self.servico.pk, "inicio": alvo.isoformat(), "nome": "Online",
                "telefone": "11933334444", "consentimento": True,
            }, format="json").status_code

        def remarcar():
            return painel_2.post(
                f"{P}/agendamentos/{existente.pk}/remarcar/", {"inicio": alvo.isoformat()}, format="json"
            ).status_code

        statuses = self.disparar([balcao, online, remarcar])
        self.assertTrue(all(isinstance(s, int) for s in statuses), statuses)
        self.assertFalse([s for s in statuses if s >= 500], statuses)
        vencedores = [s for s in statuses if s in (200, 201)]
        self.assertEqual(len(vencedores), 1, statuses)
        self.assertEqual(sorted(s for s in statuses if s not in (200, 201)), [409, 409], statuses)
        ocupando = Agendamento.objects.filter(inicio=alvo, status__in=["pendente", "confirmado"]).count()
        self.assertEqual(ocupando, 1)

    def test_primeiro_get_de_configuracao_em_corrida(self):
        self.assertFalse(Configuracao.objects.filter(profissional=self.a).exists())
        clientes = [cliente_logado(self.a.user) for _ in range(3)]
        statuses = self.disparar([lambda c=c: c.get(f"{P}/configuracao/").status_code for c in clientes])
        self.assertEqual(statuses, [200, 200, 200])
        self.assertEqual(Configuracao.objects.filter(profissional=self.a).count(), 1)
