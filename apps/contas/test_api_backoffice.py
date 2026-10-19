"""API do backoffice: /api/v1/backoffice/contas/... (D-33)."""

from datetime import timedelta

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import get_resolver
from django.utils import timezone
from rest_framework.test import APIClient

from apps.contas.models import Profissional
from apps.core.testutils import (
    LIMITES_ALTOS, cliente_logado, criar_agendamento, criar_profissional,
)

User = get_user_model()

BASE_URL = "/api/v1/backoffice/contas/"
CAMPOS = {
    "id", "username", "nome_exibicao", "slug", "telefone", "status", "cadastrado_em",
    "aprovado_por", "aprovado_em", "motivo_status", "termos_versao", "termos_aceitos_em",
}


def rotas_com_prefixo(prefixo):
    """Rotas (padroes de URL) registradas sob um prefixo, para o teste de cobertura."""
    achadas = set()

    def andar(padroes, base):
        for p in padroes:
            rota = base + str(p.pattern)
            if hasattr(p, "url_patterns"):
                andar(p.url_patterns, rota)
            elif rota.startswith(prefixo):
                achadas.add(rota)

    andar(get_resolver().url_patterns, "")
    return achadas


# modelo de rota -> requisicoes (metodo, url) que a exercitam no teste de 404 identico
ROTAS_BACKOFFICE = {
    "api/v1/backoffice/contas/": [
        ("get", BASE_URL),
        ("get", BASE_URL + "?status=pendente"),
        ("get", BASE_URL + "?q=maria"),
    ],
    "api/v1/backoffice/contas/<int:pk>/<str:acao>/": [
        ("post", BASE_URL + "{id}/aprovar/"),
        ("post", BASE_URL + "{id}/rejeitar/"),
        ("post", BASE_URL + "{id}/suspender/"),
        ("post", BASE_URL + "{id}/reativar/"),
        ("post", BASE_URL + "{id}/inexistente/"),
        ("post", BASE_URL + "999999/aprovar/"),
    ],
}


def superusuario(nome="root"):
    return User.objects.create_superuser(nome, f"{nome}@example.com", "senha-forte-123")


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class SemAcessoTests(TestCase):
    def setUp(self):
        self.alvo = criar_profissional("alvo", ativa=False)
        self.ativa = criar_profissional("ativa-bo")
        self.pendente = criar_profissional("pendente-bo", ativa=False)
        staff = User.objects.create_user("staff-bo", password="senha-forte-123", is_staff=True)
        inativo = superusuario("inativo")
        self.quem = {
            "anonimo": APIClient(enforce_csrf_checks=True),
            "profissional ativa": cliente_logado(self.ativa.user),
            "profissional pendente": cliente_logado(self.pendente.user),
            "staff sem superuser": cliente_logado(staff),
            # sem CSRF: continua 404 (nao pode virar um 403 que revele a rota)
            "profissional sem csrf": self._logado_sem_csrf(self.ativa.user),
        }
        self.inativo = inativo

    def _logado_sem_csrf(self, user):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(user)
        return client

    def test_404_identico_em_todas_as_rotas_para_quem_nao_e_superusuario_ativo(self):
        respostas = []
        for modelo, requisicoes in ROTAS_BACKOFFICE.items():
            for metodo, url in requisicoes:
                for quem, client in self.quem.items():
                    with self.subTest(quem=quem, rota=modelo, url=url):
                        resposta = getattr(client, metodo)(url.format(id=self.alvo.pk), format="json")
                        self.assertEqual(resposta.status_code, 404, resposta.content)
                        respostas.append(resposta.json())
        self.assertTrue(all(r == respostas[0] for r in respostas), respostas[0])
        self.assertEqual(respostas[0]["code"], "nao_encontrado")
        self.alvo.refresh_from_db()
        self.assertEqual(self.alvo.status, "pendente")  # nada mudou

    def test_superusuario_inativo_tambem_recebe_404(self):
        self.inativo.is_active = False
        self.inativo.save()
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.inativo)
        self.assertEqual(client.get(BASE_URL).status_code, 404)

    def test_toda_rota_do_backoffice_esta_coberta_pelo_teste_de_404(self):
        """Falha se surgir uma rota nova sem cobertura: acrescente-a em ROTAS_BACKOFFICE."""
        self.assertEqual(rotas_com_prefixo("api/v1/backoffice/"), set(ROTAS_BACKOFFICE))


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class ListagemTests(TestCase):
    def setUp(self):
        self.root = superusuario()
        self.client = cliente_logado(self.root)
        agora = timezone.now()

        def conta(slug, status, dias, telefone=""):
            p = criar_profissional(slug, ativa=False)
            Profissional.objects.filter(pk=p.pk).update(telefone=telefone)
            User.objects.filter(pk=p.user_id).update(date_joined=agora - timedelta(days=dias))
            if status != "pendente":
                p.refresh_from_db()
                if status in ("ativa", "suspensa"):
                    p.mudar_status("ativa", self.root)
                if status == "suspensa":
                    p.mudar_status("suspensa", self.root, "abuso")
                if status == "rejeitada":
                    p.mudar_status("rejeitada", self.root, "dados falsos")
            return p

        self.pend_velha = conta("pend-velha", "pendente", 10, "+5515997669299")
        self.pend_nova = conta("pend-nova", "pendente", 2)
        self.ativa_velha = conta("ativa-velha", "ativa", 30)
        self.ativa_nova = conta("ativa-nova", "ativa", 1)
        self.suspensa = conta("suspensa-1", "suspensa", 5)
        self.rejeitada = conta("rejeitada-1", "rejeitada", 3)

    def listar(self, **parametros):
        resposta = self.client.get(BASE_URL, parametros)
        self.assertEqual(resposta.status_code, 200, resposta.content)
        return resposta.json()

    def test_contagens_e_ordem_pendentes_primeiro_mais_antigos_primeiro(self):
        corpo = self.listar()
        self.assertEqual(
            corpo["contagens"], {"pendente": 2, "ativa": 2, "suspensa": 1, "rejeitada": 1}
        )
        slugs = [c["slug"] for c in corpo["resultados"]]
        self.assertEqual(
            slugs,
            ["pend-velha", "pend-nova", "ativa-nova", "rejeitada-1", "suspensa-1", "ativa-velha"],
        )

    def test_formato_da_conta(self):
        conta = self.listar(q="pend-velha")["resultados"][0]
        self.assertEqual(set(conta), CAMPOS)
        self.assertEqual(conta["telefone"], "+5515997669299")
        self.assertEqual(conta["status"], "pendente")
        self.assertIsNone(conta["aprovado_por"])
        self.assertIsNone(conta["aprovado_em"])
        self.assertIsNone(conta["termos_aceitos_em"])
        self.assertTrue(conta["cadastrado_em"])
        ativa = self.listar(q="ativa-nova")["resultados"][0]
        self.assertEqual(ativa["aprovado_por"], "root")
        self.assertTrue(ativa["aprovado_em"])
        self.assertEqual(self.listar(q="pend-nova")["resultados"][0]["telefone"], None)

    def test_filtro_por_status_mantem_as_contagens_globais(self):
        corpo = self.listar(status="ativa")
        self.assertEqual({c["status"] for c in corpo["resultados"]}, {"ativa"})
        self.assertEqual(len(corpo["resultados"]), 2)
        self.assertEqual(corpo["contagens"]["pendente"], 2)
        self.assertEqual(len(self.listar(status="")["resultados"]), 6)
        resposta = self.client.get(BASE_URL, {"status": "inexistente"})
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.json()["code"], "validacao")

    def test_busca_por_nome_usuario_slug_e_telefone(self):
        self.assertEqual([c["slug"] for c in self.listar(q="SUSPENSA")["resultados"]], ["suspensa-1"])
        self.assertEqual([c["slug"] for c in self.listar(q="user-rejeitada")["resultados"]], ["rejeitada-1"])
        self.assertEqual([c["slug"] for c in self.listar(q="ativa-")["resultados"]], ["ativa-nova", "ativa-velha"])
        for telefone in ("99766", "+5515997669299", "(15) 99766-9299"):
            with self.subTest(telefone=telefone):
                self.assertEqual([c["slug"] for c in self.listar(q=telefone)["resultados"]], ["pend-velha"])
        self.assertEqual(self.listar(q="nao-existe-mesmo")["resultados"], [])
        self.assertEqual(len(self.listar(q="%")["resultados"]), 0)  # curinga do LIKE e escapado

    def test_nao_vaza_senha_hash_clientes_nem_agendamentos(self):
        agendamento = criar_agendamento(self.ativa_nova)
        corpo = self.client.get(BASE_URL).content.decode()
        for proibido in (
            "pbkdf2", "password", "senha-forte", self.root.password,
            agendamento.cliente.nome, agendamento.codigo_curto, "preco_cobrado", "cliente", "agendamento",
        ):
            self.assertNotIn(proibido, corpo)


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class AcoesTests(TestCase):
    def setUp(self):
        self.root = superusuario()
        self.client = cliente_logado(self.root)

    def conta(self, slug, status="pendente"):
        p = criar_profissional(slug, ativa=False)
        if status in ("ativa", "suspensa"):
            p.mudar_status("ativa", self.root)
        if status == "suspensa":
            p.mudar_status("suspensa", self.root, "x")
        if status == "rejeitada":
            p.mudar_status("rejeitada", self.root, "x")
        return p

    def agir(self, p, acao, corpo=None, client=None):
        return (client or self.client).post(f"{BASE_URL}{p.pk}/{acao}/", corpo or {}, format="json")

    def log(self, p):
        return list(
            LogEntry.objects.filter(object_id=str(p.pk), action_flag=CHANGE).values_list("change_message", flat=True)
        )

    def test_aprovar_registra_quem_quando_e_log(self):
        p = self.conta("para-aprovar")
        antes = LogEntry.objects.count()
        resposta = self.agir(p, "aprovar")
        self.assertEqual(resposta.status_code, 200, resposta.content)
        corpo = resposta.json()
        self.assertEqual(set(corpo), CAMPOS)
        self.assertEqual(corpo["status"], "ativa")
        self.assertEqual(corpo["aprovado_por"], "root")
        self.assertTrue(corpo["aprovado_em"])
        p.refresh_from_db()
        self.assertEqual(p.status, "ativa")
        self.assertEqual(LogEntry.objects.count(), antes + 1)
        self.assertIn("Status: pendente -> ativa.", self.log(p))

    def test_rejeitar_e_suspender_exigem_motivo_de_3_caracteres(self):
        for acao, inicial in (("rejeitar", "pendente"), ("suspender", "ativa")):
            p = self.conta(f"motivo-{acao}", inicial)
            for corpo in ({}, {"motivo": ""}, {"motivo": "  "}, {"motivo": "ab"}):
                with self.subTest(acao=acao, corpo=corpo):
                    resposta = self.agir(p, acao, corpo)
                    self.assertEqual(resposta.status_code, 400)
                    self.assertEqual(resposta.json()["code"], "validacao")
                    self.assertIn("motivo", resposta.json()["fields"])
                    p.refresh_from_db()
                    self.assertEqual(p.status, inicial)
            resposta = self.agir(p, acao, {"motivo": "motivo valido"})
            self.assertEqual(resposta.status_code, 200)
            self.assertEqual(resposta.json()["motivo_status"], "motivo valido")
            self.assertIn(f"Status: {inicial} -> ", self.log(p)[-1])
            self.assertTrue(self.log(p)[-1].endswith("Motivo: motivo valido"))

    def test_ciclo_suspender_e_reativar(self):
        p = self.conta("ciclo", "ativa")
        self.assertEqual(self.agir(p, "suspender", {"motivo": "denuncia"}).json()["status"], "suspensa")
        resposta = self.agir(p, "reativar")
        self.assertEqual(resposta.json()["status"], "ativa")
        self.assertEqual(resposta.json()["aprovado_por"], "root")  # aprovacao original preservada

    def test_matriz_de_transicoes(self):
        validas = {
            ("pendente", "aprovar"): "ativa",
            ("pendente", "rejeitar"): "rejeitada",
            ("ativa", "suspender"): "suspensa",
            ("suspensa", "reativar"): "ativa",
        }
        for inicial in ("pendente", "ativa", "suspensa", "rejeitada"):
            for acao in ("aprovar", "rejeitar", "suspender", "reativar"):
                with self.subTest(inicial=inicial, acao=acao):
                    p = self.conta(f"m-{inicial}-{acao}", inicial)
                    resposta = self.agir(p, acao, {"motivo": "motivo valido"})
                    p.refresh_from_db()
                    if (inicial, acao) in validas:
                        self.assertEqual(resposta.status_code, 200, resposta.content)
                        self.assertEqual(p.status, validas[(inicial, acao)])
                    else:
                        self.assertEqual(resposta.status_code, 409, resposta.content)
                        self.assertEqual(resposta.json()["code"], "transicao_invalida")
                        self.assertEqual(p.status, inicial)

    def test_conta_inexistente_e_acao_desconhecida_404(self):
        self.assertEqual(self.client.post(f"{BASE_URL}999999/aprovar/", {}, format="json").status_code, 404)
        p = self.conta("qualquer")
        resposta = self.agir(p, "apagar")
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(self.client.get(f"{BASE_URL}{p.pk}/aprovar/").status_code, 405)

    def test_csrf_e_exigido_do_superusuario(self):
        p = self.conta("sem-csrf")
        sem_token = APIClient(enforce_csrf_checks=True)
        sem_token.force_login(self.root)
        resposta = self.agir(p, "aprovar", client=sem_token)
        self.assertEqual(resposta.status_code, 403)
        p.refresh_from_db()
        self.assertEqual(p.status, "pendente")
        self.assertEqual(self.agir(p, "aprovar").status_code, 200)  # com o cabecalho funciona

    def test_resposta_nao_vaza_dados_sensiveis(self):
        p = self.conta("limpa", "ativa")
        criar_agendamento(p)
        texto = self.agir(p, "suspender", {"motivo": "teste"}).content.decode()
        for proibido in ("pbkdf2", "password", "cliente", "agendamento", "codigo"):
            self.assertNotIn(proibido, texto)

    def test_admin_django_e_api_compartilham_a_auditoria(self):
        """A API usa o mesmo servico do admin: um LogEntry por mudanca, com a mesma mensagem."""
        p = self.conta("auditoria")
        self.agir(p, "rejeitar", {"motivo": "documentos invalidos"})
        self.assertEqual(
            self.log(p), ["Status: pendente -> rejeitada. Motivo: documentos invalidos"]
        )
