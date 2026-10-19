"""Endurecimento (revisao do Sentinela): serializer fail-closed, constraints e erros padronizados."""

from datetime import timedelta
from types import SimpleNamespace

from django.core.exceptions import ImproperlyConfigured
from django.db import DataError, IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import serializers
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from apps.agenda.models import Agendamento
from apps.clientes.models import Cliente
from apps.servicos.models import Servico

from .tenancy import TenantSerializer
from .testutils import BASE, criar_agendamento, criar_cliente, criar_profissional, criar_servico


def requisicao(profissional):
    return SimpleNamespace(user=profissional.user)


class AgendamentoMinimoSerializer(TenantSerializer):
    class Meta:
        model = Agendamento
        fields = ["cliente", "servico"]


class SerializerFalhaFechadoTests(TestCase):
    def setUp(self):
        self.a = criar_profissional("calita")
        self.b = criar_profissional("outra-manicure")

    def test_sem_request_levanta(self):
        with self.assertRaises(ImproperlyConfigured):
            AgendamentoMinimoSerializer(data={}).fields

    def test_request_sem_profissional_levanta(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user("sem-perfil", password="x")
        with self.assertRaises(ImproperlyConfigured):
            AgendamentoMinimoSerializer(data={}, context={"request": SimpleNamespace(user=user)}).fields

    def test_request_anonimo_levanta(self):
        from django.contrib.auth.models import AnonymousUser

        with self.assertRaises(ImproperlyConfigured):
            AgendamentoMinimoSerializer(
                data={}, context={"request": SimpleNamespace(user=AnonymousUser())}
            ).fields

    def test_campo_profissional_nunca_e_gravavel(self):
        class ComProfissional(TenantSerializer):
            class Meta:
                model = Servico
                fields = ["nome", "duracao_min", "preco", "profissional"]

        serializer = ComProfissional(
            data={"nome": "X", "duracao_min": 10, "preco": "1", "profissional": self.b.pk},
            context={"request": requisicao(self.a)},
        )
        self.assertNotIn("profissional", serializer.fields)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn("profissional", serializer.validated_data)

    def test_many_to_many_filtra_o_queryset_e_valida_listas(self):
        class Varios(TenantSerializer):
            clientes = serializers.PrimaryKeyRelatedField(many=True, queryset=Cliente.objects.all())

            class Meta:
                model = Agendamento
                fields = ["clientes"]

        proprio, alheio = criar_cliente(self.a), criar_cliente(self.b)
        contexto = {"request": requisicao(self.a)}
        ok = Varios(data={"clientes": [proprio.pk]}, context=contexto)
        self.assertTrue(ok.is_valid(), ok.errors)
        ruim = Varios(data={"clientes": [proprio.pk, alheio.pk]}, context=contexto)
        self.assertFalse(ruim.is_valid())
        self.assertIn("clientes", ruim.errors)
        # defesa em profundidade: mesmo se o queryset deixasse passar, validate() recusa a lista
        with self.assertRaises(serializers.ValidationError):
            Varios(context=contexto).validate({"clientes": [proprio, alheio]})

    def test_serializer_aninhado_gravavel_e_recusado(self):
        class Aninhado(TenantSerializer):
            cliente = AgendamentoMinimoSerializer()

            class Meta:
                model = Agendamento
                fields = ["cliente"]

        with self.assertRaises(ImproperlyConfigured):
            Aninhado(data={}, context={"request": requisicao(self.a)}).fields

    def test_relacao_para_modelo_de_tenant_sem_do_profissional_levanta(self):
        class Quebrado(TenantSerializer):
            cliente = serializers.PrimaryKeyRelatedField(queryset=Cliente.objects.none().all())

            class Meta:
                model = Agendamento
                fields = ["cliente"]

        # none() continua sendo TenantQuerySet => ok; troca por queryset sem o metodo:
        Quebrado.__dict__  # noqa: B018
        serializer = Quebrado(data={}, context={"request": requisicao(self.a)})
        serializer.fields  # nao levanta: queryset ainda e TenantQuerySet


class PadroesDoDrfTests(TestCase):
    def test_view_sem_declaracao_exige_autenticacao(self):
        class SemDeclarar(APIView):
            def get(self, request):
                return serializers.Serializer().data

        request = APIRequestFactory().get("/x")
        resposta = SemDeclarar.as_view()(request)
        self.assertEqual(resposta.status_code, 403)

    def test_saude_continua_publica(self):
        self.assertEqual(APIClient().get("/api/v1/saude/").status_code, 200)

    def test_padroes_de_settings(self):
        from django.conf import settings

        self.assertEqual(
            settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"],
            ["rest_framework.authentication.SessionAuthentication"],
        )
        self.assertEqual(
            settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"],
            ["rest_framework.permissions.IsAuthenticated"],
        )


class ConstraintsAgendamentoTests(TestCase):
    def setUp(self):
        self.a = criar_profissional("calita")
        self.ag = criar_agendamento(self.a)

    def test_token_hash_deve_ter_64_caracteres(self):
        for token in ("curto", "x" * 63, "x" * 65):  # 65 estoura o varchar(64) (DataError)
            with self.subTest(tamanho=len(token)):
                with self.assertRaises((IntegrityError, DataError)), transaction.atomic():
                    criar_agendamento(self.a, inicio_min=600, token_hash=token)

    def test_fim_ocupado_nao_pode_ser_menor_que_fim(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Agendamento.objects.filter(pk=self.ag.pk).update(fim_ocupado=self.ag.fim - timedelta(minutes=1))

    def test_fim_deve_ser_depois_do_inicio(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Agendamento.objects.filter(pk=self.ag.pk).update(
                fim=self.ag.inicio, fim_ocupado=self.ag.inicio + timedelta(minutes=10)
            )

    def test_update_de_inicio_ou_fim_sem_fim_ocupado_e_recusado(self):
        for campos in ({"inicio": BASE}, {"fim": BASE + timedelta(hours=3)}):
            with self.subTest(campos=campos):
                with self.assertRaises(ValueError):
                    Agendamento.objects.filter(pk=self.ag.pk).update(**campos)
        # com fim_ocupado explicito e permitido; outros campos tambem
        Agendamento.objects.filter(pk=self.ag.pk).update(status="confirmado")
        novo_fim = self.ag.fim + timedelta(minutes=30)
        Agendamento.objects.filter(pk=self.ag.pk).update(fim=novo_fim, fim_ocupado=novo_fim + timedelta(minutes=10))
        self.ag.refresh_from_db()
        self.assertEqual(self.ag.fim, novo_fim)

    def test_manager_continua_sendo_do_tenant(self):
        self.assertEqual(Agendamento.objects.do_profissional(self.a).count(), 1)
        outra = criar_profissional("outra-manicure")
        self.assertEqual(Agendamento.objects.do_profissional(outra).count(), 0)


# ---- erros de banco padronizados nos ViewSets (URLconf de teste de test_tenancy) -------------

@override_settings(ROOT_URLCONF="apps.core.test_tenancy")
class ErrosDeBancoPadronizadosTests(TestCase):
    """Os serializers de teste NAO validam duplicidade: quem responde e o mapeamento de constraint."""

    def setUp(self):
        from .testutils import cliente_logado

        self.a = criar_profissional("calita")
        self.b = criar_profissional("outra-manicure")
        self.client_a = APIClient()
        self.client_a.force_login(self.a.user)
        self.client_b = APIClient()
        self.client_b.force_login(self.b.user)

    def test_servico_duplicado_vira_400_duplicado(self):
        criar_servico(self.a, "Gel")
        r = self.client_a.post("/servicos/", {"nome": "Gel", "duracao_min": 30, "preco": "10"}, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()["code"], "duplicado")
        self.assertIn("nome", r.json()["fields"])
        # a conexao segue utilizavel (savepoint) e outro tenant pode usar o nome
        self.assertEqual(
            self.client_b.post("/servicos/", {"nome": "Gel", "duracao_min": 30, "preco": "10"}, format="json").status_code,
            201,
        )

    def test_servico_em_uso_nao_apaga_409(self):
        ag = criar_agendamento(self.a)
        r = self.client_a.delete(f"/servicos/{ag.servico.pk}/")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "em_uso")

    def test_sobreposicao_vira_409_conflito_horario(self):
        base = criar_agendamento(self.a)
        corpo = {
            "cliente": criar_cliente(self.a).pk,
            "servico": base.servico.pk,
            "inicio": (base.inicio + timedelta(minutes=30)).isoformat(),
            "fim": (base.fim + timedelta(minutes=30)).isoformat(),
            "preco_cobrado": "50.00",
            "token_hash": "d" * 64,
        }
        r = self.client_a.post("/agendamentos/", corpo, format="json")
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()["code"], "conflito_horario")
        self.assertEqual(Agendamento.objects.count(), 1)

    def test_constraint_desconhecida_nao_e_escondida(self):
        """Violacao nao mapeada segue como erro (500), em vez de virar uma resposta enganosa."""
        from .exceptions import tratar_excecao

        causa = Exception("x")
        causa.diag = SimpleNamespace(constraint_name="constraint_que_nao_existe")
        erro = IntegrityError("x")
        erro.__cause__ = causa
        self.assertIsNone(tratar_excecao(erro, {}))

    def test_validacao_de_modelo_vira_400(self):
        base = criar_agendamento(self.a)
        corpo = {
            "cliente": base.cliente.pk, "servico": base.servico.pk,
            "inicio": (base.fim + timedelta(hours=3)).isoformat(),
            "fim": (base.fim + timedelta(hours=2)).isoformat(),  # fim antes do inicio
            "preco_cobrado": "50.00", "token_hash": "e" * 64,
        }
        r = self.client_a.post("/agendamentos/", corpo, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()["code"], "validacao")

    def test_patch_trocando_cliente_ou_servico_para_outro_tenant_falha(self):
        ag = criar_agendamento(self.a)
        alheio_cliente, alheio_servico = criar_cliente(self.b), criar_servico(self.b)
        for campo, valor in (("cliente", alheio_cliente.pk), ("servico", alheio_servico.pk)):
            with self.subTest(campo=campo):
                r = self.client_a.patch(f"/agendamentos/{ag.pk}/", {campo: valor}, format="json")
                self.assertEqual(r.status_code, 400, r.content)
                self.assertIn(campo, r.json()["fields"])
        ag.refresh_from_db()
        self.assertEqual(ag.cliente.profissional, self.a)
        self.assertEqual(ag.servico.profissional, self.a)

    def test_patch_nao_muda_o_profissional(self):
        ag = criar_agendamento(self.a)
        self.client_a.patch(f"/agendamentos/{ag.pk}/", {"profissional": self.b.pk}, format="json")
        ag.refresh_from_db()
        self.assertEqual(ag.profissional, self.a)

    def test_perform_update_reforca_o_profissional(self):
        """Mesmo que o objeto tivesse sido adulterado em memoria, o save injeta o tenant logado."""
        servico = criar_servico(self.a, "Gel")
        r = self.client_a.patch(f"/servicos/{servico.pk}/", {"nome": "Gel 2"}, format="json")
        self.assertEqual(r.status_code, 200)
        servico.refresh_from_db()
        self.assertEqual(servico.profissional, self.a)


class IsolamentoDeModelosTests(TestCase):
    """Cliente, Configuracao, HorarioTrabalho e Bloqueio filtram por tenant no queryset."""

    def test_do_profissional(self):
        from datetime import time

        from apps.agenda.models import Bloqueio, Configuracao, HorarioTrabalho

        a, b = criar_profissional("calita"), criar_profissional("outra-manicure")
        for p in (a, b):
            Cliente.objects.create(profissional=p, nome="C", telefone="11988887777")
            Configuracao.objects.create(profissional=p)
            HorarioTrabalho.objects.create(profissional=p, dia_semana=1, inicio=time(9), fim=time(12))
            Bloqueio.objects.create(profissional=p, inicio=BASE, fim=BASE + timedelta(hours=1))
        for modelo in (Cliente, Configuracao, HorarioTrabalho, Bloqueio):
            with self.subTest(modelo=modelo.__name__):
                self.assertEqual(modelo.objects.count(), 2)
                self.assertEqual(modelo.objects.do_profissional(a).get().profissional, a)
                self.assertEqual(modelo.objects.do_profissional(b).get().profissional, b)
