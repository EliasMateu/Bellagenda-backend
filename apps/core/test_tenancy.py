"""TEN-01: isolamento entre profissionais.

Os ViewSets e a URLconf abaixo existem somente nos testes (este modulo e a propria URLconf).
"""

from django.test import TestCase, override_settings
from rest_framework import serializers
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.agenda.models import Agendamento
from apps.agenda.tokens import gerar_token_e_hash
from apps.contas.models import Profissional
from apps.servicos.models import Servico

from .tenancy import TenantModelViewSet, TenantSerializer
from .testutils import BASE, admin_plataforma, criar_cliente, criar_profissional, criar_servico


class ServicoSerializer(TenantSerializer):
    class Meta:
        model = Servico
        fields = ["id", "nome", "duracao_min", "preco", "ativo"]


class ServicoViewSet(TenantModelViewSet):
    queryset = Servico.objects.all()
    serializer_class = ServicoSerializer


class AgendamentoSerializer(TenantSerializer):
    class Meta:
        model = Agendamento
        fields = ["id", "cliente", "servico", "inicio", "fim", "preco_cobrado", "token_hash"]


class AgendamentoViewSet(TenantModelViewSet):
    queryset = Agendamento.objects.all()
    serializer_class = AgendamentoSerializer


router = DefaultRouter()
router.register("servicos", ServicoViewSet, basename="servico")
router.register("agendamentos", AgendamentoViewSet, basename="agendamento")
urlpatterns = router.urls


@override_settings(ROOT_URLCONF=__name__)
class IsolamentoTenantTests(TestCase):
    def setUp(self):
        self.a = criar_profissional("prof-a")
        self.b = criar_profissional("prof-b")
        self.servico_a = criar_servico(self.a, "Esmaltacao A")
        self.servico_b = criar_servico(self.b, "Esmaltacao B")
        self.cliente_a = criar_cliente(self.a)
        self.client_b = APIClient()
        self.client_b.force_login(self.b.user)
        self.client_a = APIClient()
        self.client_a.force_login(self.a.user)

    def test_lista_so_do_proprio_tenant(self):
        resposta = self.client_b.get("/servicos/")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual([s["nome"] for s in resposta.json()], ["Esmaltacao B"])

    def test_b_nao_le_objeto_de_a(self):
        self.assertEqual(self.client_b.get(f"/servicos/{self.servico_a.pk}/").status_code, 404)

    def test_b_nao_edita_objeto_de_a(self):
        url = f"/servicos/{self.servico_a.pk}/"
        self.assertEqual(self.client_b.patch(url, {"nome": "Hackeado"}, format="json").status_code, 404)
        self.assertEqual(
            self.client_b.put(
                url, {"nome": "X", "duracao_min": 10, "preco": "1.00"}, format="json"
            ).status_code,
            404,
        )
        self.servico_a.refresh_from_db()
        self.assertEqual(self.servico_a.nome, "Esmaltacao A")

    def test_b_nao_apaga_objeto_de_a(self):
        self.assertEqual(self.client_b.delete(f"/servicos/{self.servico_a.pk}/").status_code, 404)
        self.assertTrue(Servico.objects.filter(pk=self.servico_a.pk).exists())

    def test_dono_acessa_e_edita_o_proprio(self):
        url = f"/servicos/{self.servico_a.pk}/"
        self.assertEqual(self.client_a.get(url).status_code, 200)
        self.assertEqual(self.client_a.patch(url, {"nome": "Novo"}, format="json").status_code, 200)
        self.assertEqual(self.client_a.delete(url).status_code, 204)

    def test_criar_injeta_profissional_e_ignora_o_enviado(self):
        resposta = self.client_b.post(
            "/servicos/",
            {"nome": "Nova", "duracao_min": 30, "preco": "20.00", "profissional": self.a.pk},
            format="json",
        )
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(Servico.objects.get(pk=resposta.json()["id"]).profissional, self.b)

    def test_criar_com_fks_de_outro_tenant_falha(self):
        corpo = {
            "cliente": self.cliente_a.pk,
            "servico": criar_servico(self.b, "Do B").pk,
            "inicio": BASE.isoformat(),
            "fim": BASE.replace(hour=11).isoformat(),
            "preco_cobrado": "50.00",
            "token_hash": gerar_token_e_hash()[1],
        }
        resposta = self.client_b.post("/agendamentos/", corpo, format="json")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("cliente", resposta.json()["fields"])
        self.assertFalse(Agendamento.objects.exists())

        corpo["cliente"] = criar_cliente(self.b).pk
        corpo["servico"] = self.servico_a.pk
        resposta = self.client_b.post("/agendamentos/", corpo, format="json")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("servico", resposta.json()["fields"])

    def test_criar_com_fks_do_proprio_tenant_funciona(self):
        corpo = {
            "cliente": self.cliente_a.pk,
            "servico": self.servico_a.pk,
            "inicio": BASE.isoformat(),
            "fim": BASE.replace(hour=11).isoformat(),
            "preco_cobrado": "50.00",
            "token_hash": gerar_token_e_hash()[1],
        }
        resposta = self.client_a.post("/agendamentos/", corpo, format="json")
        self.assertEqual(resposta.status_code, 201, resposta.content)
        self.assertEqual(Agendamento.objects.get().profissional, self.a)

    def test_validate_recusa_instancia_de_outro_tenant(self):
        """Defesa em profundidade: mesmo que o queryset da FK nao filtre, validate() recusa."""
        request = type("R", (), {"user": self.b.user})()
        serializer = AgendamentoSerializer(context={"request": request})
        with self.assertRaises(serializers.ValidationError):
            serializer.validate({"cliente": self.cliente_a})

    def test_conta_pendente_recebe_403(self):
        pendente = criar_profissional("prof-pendente", ativa=False)
        client = APIClient()
        client.force_login(pendente.user)
        self.assertEqual(client.get("/servicos/").status_code, 403)
        self.assertEqual(
            client.post("/servicos/", {"nome": "x", "duracao_min": 10, "preco": "1"}, format="json").status_code,
            403,
        )

    def test_conta_suspensa_e_rejeitada_recebem_403(self):
        self.a.mudar_status("suspensa", admin_plataforma())
        self.assertEqual(self.client_a.get("/servicos/").status_code, 403)
        rejeitada = criar_profissional("prof-rej", ativa=False)
        rejeitada.mudar_status("rejeitada", admin_plataforma())
        client = APIClient()
        client.force_login(rejeitada.user)
        self.assertEqual(client.get("/servicos/").status_code, 403)

    def test_anonimo_e_usuario_sem_profissional_recebem_403(self):
        self.assertEqual(APIClient().get("/servicos/").status_code, 403)
        from django.contrib.auth import get_user_model

        sem_perfil = get_user_model().objects.create_user("sem-perfil", password="x")
        client = APIClient()
        client.force_login(sem_perfil)
        self.assertEqual(client.get("/servicos/").status_code, 403)
        self.assertFalse(Profissional.objects.filter(user=sem_perfil).exists())
