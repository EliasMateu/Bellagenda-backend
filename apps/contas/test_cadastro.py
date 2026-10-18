"""Cadastro aberto: /api/v1/cadastro/{config,slug,}/ (D-33)."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.contas.api_cadastro import CadastroSerializer
from apps.contas.models import Profissional
from apps.core.testutils import LIMITES_ALTOS, admin_plataforma, criar_profissional

User = get_user_model()

SENHA = "Senha-segura-9271"
VERSAO = "2026-10-provisorio"


def dados(**extra):
    base = {
        "username": "maria.silva",
        "password": SENHA,
        "nome_exibicao": "Maria Silva",
        "telefone": "(15) 99766-9299",
        "slug": "maria-silva",
        "aceite_termos": True,
        "termos_versao": VERSAO,
        "website": "",
    }
    base.update(extra)
    return base


class CadastroBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.cfg = override_settings(CADASTRO_ABERTO=True, LIMITES_PUBLICOS=LIMITES_ALTOS)
        self.cfg.enable()
        self.addCleanup(self.cfg.disable)

    def post(self, **extra):
        return self.client.post("/api/v1/cadastro/", dados(**extra), format="json")

    def contas(self):
        return Profissional.objects.count()


class ConfigTests(CadastroBase):
    def test_config_aberta(self):
        resposta = self.client.get("/api/v1/cadastro/config/")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"aberto": True, "termos_versao": VERSAO})

    @override_settings(CADASTRO_ABERTO=False)
    def test_config_fechada_por_padrao_da_flag(self):
        self.assertEqual(self.client.get("/api/v1/cadastro/config/").json()["aberto"], False)

    @override_settings(CADASTRO_ABERTO=False)
    def test_post_fechado_403_e_nao_cria(self):
        resposta = self.post()
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(resposta.json()["code"], "cadastro_fechado")
        self.assertEqual(self.contas(), 0)
        self.assertFalse(User.objects.filter(username="maria.silva").exists())


class CadastroFelizTests(CadastroBase):
    def test_cria_conta_pendente_sem_login_e_sem_dados_sensiveis(self):
        resposta = self.post()
        self.assertEqual(resposta.status_code, 201, resposta.content)
        self.assertEqual(resposta.json(), {"status": "pendente"})
        user = User.objects.get(username="maria.silva")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertNotEqual(user.password, SENHA)
        self.assertTrue(user.password.startswith(("pbkdf2_", "argon2", "bcrypt", "md5$")))
        self.assertTrue(user.check_password(SENHA))
        p = user.profissional
        self.assertEqual(p.status, "pendente")
        self.assertEqual(p.slug, "maria-silva")
        self.assertEqual(p.telefone, "+5515997669299")
        self.assertEqual(p.nome_exibicao, "Maria Silva")
        self.assertEqual(p.termos_versao, VERSAO)
        self.assertIsNotNone(p.termos_aceitos_em)
        self.assertNotIn("sessionid", resposta.cookies)  # sem login automatico
        self.assertEqual(APIClient().get("/api/v1/auth/me/").status_code, 403)

    def test_conta_nova_ganha_horarios_padrao_e_configuracao_sem_servicos(self):
        from datetime import time

        from apps.agenda.models import Configuracao, HorarioTrabalho
        from apps.servicos.models import Servico

        self.assertEqual(self.post().status_code, 201)
        p = Profissional.objects.get()
        faixas = HorarioTrabalho.objects.filter(profissional=p).order_by("dia_semana")
        self.assertEqual([f.dia_semana for f in faixas], [0, 1, 2, 3, 4])
        self.assertTrue(all(f.inicio == time(9) and f.fim == time(18) for f in faixas))
        self.assertTrue(Configuracao.objects.filter(profissional=p).exists())
        self.assertFalse(Servico.objects.filter(profissional=p).exists())

    def test_falha_ao_criar_horarios_desfaz_tudo(self):
        from apps.agenda.models import Configuracao, HorarioTrabalho

        with mock.patch("apps.contas.api_cadastro.criar_horarios_padrao", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.post()
        self.assertFalse(User.objects.filter(username="maria.silva").exists())
        self.assertEqual(self.contas(), 0)
        self.assertEqual(HorarioTrabalho.objects.count(), 0)
        self.assertEqual(Configuracao.objects.count(), 0)

    def test_username_e_slug_sao_normalizados_para_minusculas(self):
        resposta = self.post(username="Maria.Silva", slug="Maria-Silva")
        self.assertEqual(resposta.status_code, 201, resposta.content)
        self.assertEqual(Profissional.objects.get().slug, "maria-silva")
        self.assertTrue(User.objects.filter(username="maria.silva").exists())

    def test_conta_nova_loga_ve_pendente_mas_nao_tem_link_publico_nem_painel(self):
        self.assertEqual(self.post().status_code, 201)
        client = APIClient(enforce_csrf_checks=True)
        token = client.get("/api/v1/auth/csrf/").json()["csrfToken"]
        login = client.post(
            "/api/v1/auth/login/", {"username": "maria.silva", "password": SENHA},
            format="json", HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(login.status_code, 200)
        me = client.get("/api/v1/auth/me/").json()
        self.assertEqual(me["profissional"]["status"], "pendente")
        self.assertEqual(APIClient().get("/api/v1/publico/maria-silva/servicos/").status_code, 404)
        self.assertEqual(client.get("/api/v1/painel/servicos/").status_code, 403)


class CadastroValidacaoTests(CadastroBase):
    def assertErroNoCampo(self, resposta, campo):
        self.assertEqual(resposta.status_code, 400, resposta.content)
        corpo = resposta.json()
        self.assertEqual(corpo["code"], "validacao")
        self.assertIn(campo, corpo["fields"], corpo)
        self.assertEqual(self.contas(), 0)

    def test_slug_invalido_reservado(self):
        for slug in ("a", "ab", "com espaco", "-inicio", "fim-", "du--plo", "x" * 41):
            with self.subTest(slug=slug):
                self.assertErroNoCampo(self.post(slug=slug), "slug")
        for slug in ("admin", "ADMIN", "api", "cadastro", "backoffice"):
            with self.subTest(slug=slug):
                self.assertErroNoCampo(self.post(slug=slug), "slug")

    def test_slug_duplicado_sem_diferenciar_maiusculas(self):
        criar_profissional("calita")
        resposta = self.post(slug="CALITA")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("slug", resposta.json()["fields"])

    def test_slug_de_conta_pendente_tambem_conta_como_em_uso(self):
        criar_profissional("fila", ativa=False)
        self.assertEqual(self.post(slug="fila").status_code, 400)

    def test_username_invalido_e_duplicado(self):
        for username in ("ab", "x" * 31, "com espaco", "acentuação", "a@b.c"):
            with self.subTest(username=username):
                self.assertErroNoCampo(self.post(username=username), "username")
        criar_profissional("calita")
        resposta = self.post(username="USER-CALITA")
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("username", resposta.json()["fields"])

    def test_telefone_invalido(self):
        for telefone in ("123", "", "(11) 1234-5678", "abc"):
            with self.subTest(telefone=telefone):
                self.assertErroNoCampo(self.post(telefone=telefone), "telefone")

    def test_nome_de_exibicao(self):
        for nome in ("", " ", "A", " A ", "x" * 101):
            with self.subTest(nome=nome):
                self.assertErroNoCampo(self.post(nome_exibicao=nome), "nome_exibicao")
        self.assertEqual(self.post(nome_exibicao="Jo").status_code, 201)

    def test_senha_fraca_e_senha_com_username(self):
        for senha in ("123", "12345678", "password", "abc"):
            with self.subTest(senha=senha):
                self.assertErroNoCampo(self.post(password=senha), "password")
        resposta = self.post(password="xx-maria.silva-9271-zz")
        self.assertErroNoCampo(resposta, "password")
        self.assertNotIn("9271", resposta.content.decode())  # nao devolve a senha

    def test_termos(self):
        corpo = dados()
        for chave in ("aceite_termos", "termos_versao"):
            with self.subTest(ausente=chave):
                parcial = {k: v for k, v in corpo.items() if k != chave}
                resposta = self.client.post("/api/v1/cadastro/", parcial, format="json")
                self.assertErroNoCampo(resposta, chave)
        self.assertErroNoCampo(self.post(aceite_termos=False), "aceite_termos")
        self.assertErroNoCampo(self.post(termos_versao="2020-01-01"), "termos_versao")

    def test_corpo_que_nao_e_objeto(self):
        resposta = self.client.post("/api/v1/cadastro/", [1, 2], format="json")
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(self.contas(), 0)

    def test_isca_preenchida_da_400_generico_sem_criar(self):
        resposta = self.post(website="http://spam.example")
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.json(), {"code": "validacao", "message": "Dados invalidos.", "fields": {}})
        self.assertEqual(self.contas(), 0)
        self.assertFalse(User.objects.filter(username="maria.silva").exists())


class CadastroLimitesTests(CadastroBase):
    def test_teto_de_contas_pendentes(self):
        with override_settings(CADASTRO_MAX_PENDENTES=2):
            criar_profissional("pend-um", ativa=False)
            criar_profissional("pend-dois", ativa=False)
            criar_profissional("ativa-x")  # ativa nao conta no teto
            resposta = self.post()
            self.assertEqual(resposta.status_code, 409)
            self.assertEqual(resposta.json()["code"], "limite_contas_pendentes")
            self.assertFalse(User.objects.filter(username="maria.silva").exists())
        with override_settings(CADASTRO_MAX_PENDENTES=3):
            self.assertEqual(self.post().status_code, 201)

    def test_throttle_do_post_por_ip(self):
        with override_settings(LIMITES_PUBLICOS={**LIMITES_ALTOS, "cadastro_ip": (3, 3600)}):
            for _ in range(3):
                self.assertEqual(self.client.post("/api/v1/cadastro/", {}, format="json").status_code, 400)
            resposta = self.client.post("/api/v1/cadastro/", {}, format="json")
            self.assertEqual(resposta.status_code, 429)
            self.assertEqual(resposta.json()["code"], "limite_requisicoes")
            self.assertIn("Retry-After", resposta)
            outro = APIClient(REMOTE_ADDR="10.8.8.8")
            self.assertEqual(outro.post("/api/v1/cadastro/", {}, format="json").status_code, 400)

    def test_limites_padrao(self):
        from config import settings as cfg

        self.assertEqual(cfg.LIMITES_PUBLICOS["cadastro_ip"], (3, 3600))
        self.assertEqual(cfg.LIMITES_PUBLICOS["cadastro_slug"], (30, 3600))
        self.assertEqual(cfg.CADASTRO_MAX_PENDENTES, 50)

    def test_corrida_de_username_vira_400_padronizado(self):
        criar_profissional("calita")
        # Simula a corrida: a validacao nao enxerga o usuario, mas o banco recusa no INSERT.
        with mock.patch.object(CadastroSerializer, "validate_username", lambda self, v: v.lower()):
            resposta = self.post(username="user-calita")
        self.assertEqual(resposta.status_code, 400, resposta.content)
        self.assertIn("username", resposta.json()["fields"])
        self.assertEqual(self.contas(), 1)

    def test_corrida_de_slug_vira_400_padronizado_e_nao_deixa_usuario_orfao(self):
        criar_profissional("calita")
        with mock.patch.object(CadastroSerializer, "validate_slug", lambda self, v: v.lower()):
            resposta = self.post(slug="calita")
        self.assertEqual(resposta.status_code, 400, resposta.content)
        self.assertIn("slug", resposta.json()["fields"])
        self.assertFalse(User.objects.filter(username="maria.silva").exists())  # transacao atomica


class ChecagemSlugTests(CadastroBase):
    def checar(self, slug=None):
        parametros = {} if slug is None else {"slug": slug}
        return self.client.get("/api/v1/cadastro/slug/", parametros)

    def test_disponivel_invalido_reservado_em_uso(self):
        criar_profissional("calita")
        casos = {
            "livre-agora": (True, None),
            "a": (False, "invalido"),
            "com espaco": (False, "invalido"),
            "admin": (False, "reservado"),
            "ADMIN": (False, "reservado"),
            "calita": (False, "em_uso"),
            "CALITA": (False, "em_uso"),
        }
        for slug, (disponivel, motivo) in casos.items():
            with self.subTest(slug=slug):
                resposta = self.checar(slug)
                self.assertEqual(resposta.status_code, 200)
                self.assertEqual(resposta.json(), {"disponivel": disponivel, "motivo": motivo})
        self.assertEqual(self.checar().json(), {"disponivel": False, "motivo": "invalido"})

    def test_nao_revela_o_status_da_conta_dona_do_slug(self):
        admin = admin_plataforma()
        criar_profissional("ativa-x")
        criar_profissional("pend-x", ativa=False)
        susp = criar_profissional("susp-x")
        susp.mudar_status("suspensa", admin)
        rej = criar_profissional("rej-x", ativa=False)
        rej.mudar_status("rejeitada", admin)
        respostas = {self.checar(s).content for s in ("ativa-x", "pend-x", "susp-x", "rej-x")}
        self.assertEqual(len(respostas), 1)
        corpo = self.checar("pend-x").json()
        self.assertEqual(set(corpo), {"disponivel", "motivo"})
        self.assertEqual(corpo["motivo"], "em_uso")

    def test_throttle_por_ip(self):
        with override_settings(LIMITES_PUBLICOS={**LIMITES_ALTOS, "cadastro_slug": (2, 3600)}):
            self.assertEqual(self.checar("livre-1").status_code, 200)
            self.assertEqual(self.checar("livre-2").status_code, 200)
            resposta = self.checar("livre-3")
            self.assertEqual(resposta.status_code, 429)
            self.assertEqual(resposta.json()["code"], "limite_requisicoes")
            self.assertIn("Retry-After", resposta)


class RotasDoCadastroTests(TestCase):
    ESPERADAS = {
        "api/v1/cadastro/config/",
        "api/v1/cadastro/slug/",
        "api/v1/cadastro/",
    }

    def test_toda_rota_do_cadastro_esta_coberta(self):
        """Falha se surgir uma rota nova sem teste: inclua-a em ESPERADAS e cubra-a nesta suite."""
        from apps.contas.test_api_backoffice import rotas_com_prefixo

        self.assertEqual(rotas_com_prefixo("api/v1/cadastro/"), self.ESPERADAS)
