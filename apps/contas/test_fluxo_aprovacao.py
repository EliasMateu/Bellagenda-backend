"""Fluxo ponta a ponta: conta criada pendente -> admin aprova no backoffice -> login, painel e link publico."""

import io
from unittest import mock

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.testutils import LIMITES_ALTOS, admin_plataforma
from apps.servicos.models import Servico

from .models import Profissional

SENHA = "Zebra-Quente-47!"
LISTA = reverse("backoffice:contas_profissional_changelist")


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class FluxoAprovacaoTests(TestCase):
    def setUp(self):
        with mock.patch.dict("os.environ", {"DJANGO_PROFISSIONAL_PASSWORD": SENHA}), mock.patch(
            "apps.contas.management.commands.criar_profissional._interativo", return_value=False
        ):
            call_command(
                "criar_profissional", "--username", "maria", "--nome", "Maria Lima",
                "--telefone", "11998765432", "--slug", "maria-lima", "--horarios-padrao",
                stdout=io.StringIO(),
            )
        self.p = Profissional.objects.get(slug="maria-lima")
        Servico.objects.create(profissional=self.p, nome="Esmaltacao", duracao_min=60, preco=50)
        self.publico = APIClient()
        self.app = APIClient(enforce_csrf_checks=True)
        self.admin = Client()
        self.admin.force_login(admin_plataforma())

    def me(self):
        return self.app.get("/api/v1/auth/me/")

    def login(self):
        token = self.app.get("/api/v1/auth/csrf/").json()["csrfToken"]
        resposta = self.app.post(
            "/api/v1/auth/login/", {"username": "maria", "password": SENHA},
            format="json", HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.app.defaults["HTTP_X_CSRFTOKEN"] = self.app.get("/api/v1/auth/csrf/").json()["csrfToken"]

    def servicos_publicos(self):
        return self.publico.get("/api/v1/publico/maria-lima/servicos/")

    def test_fluxo_completo(self):
        # 1) pendente: loga e ve /me, mas link publico e painel nao funcionam
        self.login()
        self.assertEqual(self.me().json()["profissional"]["status"], "pendente")
        self.assertEqual(self.servicos_publicos().status_code, 404)
        self.assertEqual(self.app.get("/api/v1/painel/servicos/").status_code, 403)

        # 2) admin aprova pelo backoffice
        resposta = self.admin.post(
            LISTA, {"action": "aprovar", "_selected_action": [self.p.pk], "index": 0}, follow=True
        )
        self.assertIn("1 conta(s) aprovada(s).", [str(m) for m in resposta.context["messages"]])

        # 3) a mesma sessao ja ve ativa; link publico e painel funcionam
        self.assertEqual(self.me().json()["profissional"]["status"], "ativa")
        publico = self.servicos_publicos()
        self.assertEqual(publico.status_code, 200)
        self.assertEqual(publico.json()["nome_exibicao"], "Maria Lima")
        self.assertEqual(self.app.get("/api/v1/painel/servicos/").status_code, 200)

        # 4) suspensao volta ao 404 publico e ao 403 no painel (login e /me continuam)
        pagina = self.admin.post(LISTA, {"action": "suspender", "_selected_action": [self.p.pk]})
        self.assertEqual(pagina.status_code, 200)
        self.admin.post(
            LISTA,
            {"action": "suspender", "_selected_action": [self.p.pk], "aplicar": "1", "motivo": "Inadimplencia"},
            follow=True,
        )
        self.assertEqual(self.servicos_publicos().status_code, 404)
        self.assertEqual(self.app.get("/api/v1/painel/servicos/").status_code, 403)
        self.assertEqual(self.me().json()["profissional"]["status"], "suspensa")

        # 5) reativada, tudo volta
        self.admin.post(LISTA, {"action": "reativar", "_selected_action": [self.p.pk], "index": 0}, follow=True)
        self.assertEqual(self.servicos_publicos().status_code, 200)
        self.assertEqual(self.app.get("/api/v1/painel/servicos/").status_code, 200)

    def test_rejeitada_continua_sem_link_publico_nem_painel(self):
        self.login()
        self.admin.post(
            LISTA,
            {"action": "rejeitar", "_selected_action": [self.p.pk], "aplicar": "1", "motivo": "Dados incompletos"},
            follow=True,
        )
        self.assertEqual(self.me().json()["profissional"]["status"], "rejeitada")
        self.assertEqual(self.servicos_publicos().status_code, 404)
        self.assertEqual(self.app.get("/api/v1/painel/servicos/").status_code, 403)

    def test_a_propria_profissional_nao_aprova_a_si_mesma(self):
        self.login()
        # mesma sessao da API tentando o backoffice: 404
        usuario = Client()
        usuario.force_login(self.p.user)
        resposta = usuario.post(LISTA, {"action": "aprovar", "_selected_action": [self.p.pk], "index": 0})
        self.assertEqual(resposta.status_code, 404)
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "pendente")
