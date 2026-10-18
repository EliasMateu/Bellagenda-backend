from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.testutils import LIMITES_ALTOS, admin_plataforma, criar_profissional

SENHA = "senha-forte-123"


def csrf(client):
    token = client.get("/api/v1/auth/csrf/").json()["csrfToken"]
    return {"HTTP_X_CSRFTOKEN": token}


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class LoginTests(TestCase):
    def setUp(self):
        self.a = criar_profissional("calita")
        self.client = APIClient(enforce_csrf_checks=True)

    def login(self, username="user-calita", password=SENHA, client=None, **extra):
        client = client or self.client
        extra = extra or csrf(client)
        return client.post(
            "/api/v1/auth/login/", {"username": username, "password": password},
            format="json", **extra,
        )

    def test_csrf_define_cookie(self):
        resposta = self.client.get("/api/v1/auth/csrf/")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("csrftoken", resposta.cookies)
        self.assertTrue(resposta.json()["csrfToken"])

    def test_login_exige_csrf(self):
        resposta = self.client.post(
            "/api/v1/auth/login/", {"username": "user-calita", "password": SENHA}, format="json"
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 403)

    def test_login_com_sucesso_cria_sessao_httponly(self):
        resposta = self.login()
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertTrue(resposta.cookies["sessionid"]["httponly"])
        self.assertEqual(resposta.cookies["sessionid"]["samesite"], "Lax")
        me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["profissional"]["slug"], "calita")

    def test_falhas_de_login_tem_mensagem_generica_e_identica(self):
        erradas = [self.login(password="errada"), self.login(username="nao-existe")]
        inativo = criar_profissional("inativa-x")
        inativo.user.is_active = False
        inativo.user.save()
        erradas.append(self.login(username="user-inativa-x"))
        for resposta in erradas:
            self.assertEqual(resposta.status_code, 401)
            self.assertEqual(resposta.json(), erradas[0].json())
        self.assertEqual(erradas[0].json()["code"], "credenciais_invalidas")

    def test_login_com_dados_invalidos(self):
        resposta = self.client.post("/api/v1/auth/login/", {}, format="json", **csrf(self.client))
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.json()["code"], "validacao")

    def test_conta_pendente_loga_ve_me_mas_nao_o_painel(self):
        pendente = criar_profissional("em-analise", ativa=False)
        self.assertEqual(self.login(username=pendente.user.username).status_code, 200)
        me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me.json()["profissional"]["status"], "pendente")
        self.assertEqual(self.client.get("/api/v1/painel/servicos/").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/painel/configuracao/").status_code, 403)

    def test_me_sem_profissional_so_traz_flag_de_superusuario(self):
        admin = admin_plataforma()
        admin.set_password(SENHA)
        admin.save()
        self.login(username=admin.username)
        corpo = self.client.get("/api/v1/auth/me/").json()
        self.assertEqual(set(corpo), {"id", "username", "is_superuser"})
        self.assertTrue(corpo["is_superuser"])

    def test_me_nao_expoe_dados_sensiveis(self):
        self.login()
        texto = self.client.get("/api/v1/auth/me/").content.decode()
        for proibido in ("password", "email", "token"):
            self.assertNotIn(proibido, texto)

    def test_me_anonimo_403(self):
        self.assertEqual(APIClient().get("/api/v1/auth/me/").status_code, 403)

    def test_logout_exige_csrf_e_encerra_sessao(self):
        self.login()
        self.assertEqual(self.client.post("/api/v1/auth/logout/").status_code, 403)
        self.assertEqual(self.client.post("/api/v1/auth/logout/", **csrf(self.client)).status_code, 204)
        self.assertEqual(self.client.get("/api/v1/auth/me/").status_code, 403)

    def test_cookies_configurados(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, "Lax")

    def test_flags_secure_vem_do_ambiente(self):
        from config.settings import _booleano

        self.assertTrue(_booleano("True"))
        self.assertTrue(_booleano("1"))
        self.assertFalse(_booleano("false"))
        self.assertFalse(_booleano(""))


class LoginThrottleTests(TestCase):
    """Limite de login: conta SOMENTE falhas de autenticacao (sucesso, CSRF/corpo invalidos nao contam)."""

    def setUp(self):
        criar_profissional("calita")
        self.client = APIClient(enforce_csrf_checks=True)

    def tentar(self, username, senha="errada", client=None):
        client = client or self.client
        return client.post(
            "/api/v1/auth/login/", {"username": username, "password": senha},
            format="json", **csrf(client),
        )

    def de_ip(self, n):
        return APIClient(enforce_csrf_checks=True, REMOTE_ADDR=f"10.0.0.{n}")

    def test_falhas_estouram_a_chave_estrita_usuario_mais_ip(self):
        with override_settings(LIMITES_PUBLICOS={**LIMITES_ALTOS, "login_usuario_ip": (3, 3600)}):
            for _ in range(3):
                self.assertEqual(self.tentar("user-calita").status_code, 401)
            resposta = self.tentar("user-calita")
            self.assertEqual(resposta.status_code, 429)
            self.assertEqual(resposta.json()["code"], "limite_requisicoes")
            self.assertIn("Retry-After", resposta)
            # quem estourou o limite fica bloqueado tambem com a senha certa (so nessa chave)
            self.assertEqual(self.tentar("user-calita", SENHA).status_code, 429)
            # mesmo username vindo de outro IP e outro username do mesmo IP seguem livres
            self.assertEqual(self.tentar("user-calita", SENHA, self.de_ip(9)).status_code, 200)
            self.assertEqual(self.tentar("outro-usuario").status_code, 401)

    def test_dez_falhas_anonimas_nao_impedem_o_login_legitimo_de_outro_ip(self):
        # limites padrao: estrita 5, por username 50, por IP 30
        for n in range(10):
            self.assertEqual(self.tentar("user-calita", client=self.de_ip(n + 1)).status_code, 401)
        legitimo = self.de_ip(200)
        self.assertEqual(self.tentar("user-calita", SENHA, legitimo).status_code, 200)

    def test_cinco_falhas_do_atacante_nao_travam_a_vitima_em_outro_ip(self):
        atacante = self.de_ip(66)
        for _ in range(5):
            self.assertEqual(self.tentar("user-calita", client=atacante).status_code, 401)
        self.assertEqual(self.tentar("user-calita", client=atacante).status_code, 429)
        self.assertEqual(self.tentar("user-calita", SENHA, self.de_ip(77)).status_code, 200)

    def test_sucesso_nao_conta_e_zera_a_chave_estrita(self):
        with override_settings(LIMITES_PUBLICOS={**LIMITES_ALTOS, "login_usuario_ip": (3, 3600),
                                                 "login_usuario": (3, 3600), "login_ip": (3, 3600)}):
            for _ in range(10):  # muitos logins corretos nunca bloqueiam
                self.assertEqual(self.tentar("user-calita", SENHA).status_code, 200)
        with override_settings(LIMITES_PUBLICOS={**LIMITES_ALTOS, "login_usuario_ip": (3, 3600)}):
            self.tentar("user-calita")
            self.tentar("user-calita")
            self.assertEqual(self.tentar("user-calita", SENHA).status_code, 200)  # zera
            self.tentar("user-calita")
            self.tentar("user-calita")
            self.assertEqual(self.tentar("user-calita").status_code, 401)  # ainda abaixo do limite

    def test_requisicoes_sem_csrf_ou_com_corpo_invalido_nao_contam(self):
        with override_settings(LIMITES_PUBLICOS={**LIMITES_ALTOS, "login_usuario_ip": (2, 3600),
                                                 "login_usuario": (2, 3600), "login_ip": (2, 3600)}):
            for _ in range(10):
                sem_csrf = self.client.post(
                    "/api/v1/auth/login/", {"username": "user-calita", "password": "x"}, format="json"
                )
                self.assertEqual(sem_csrf.status_code, 403)
                invalido = self.client.post(
                    "/api/v1/auth/login/", {"username": "user-calita"}, format="json", **csrf(self.client)
                )
                self.assertEqual(invalido.status_code, 400)
            self.assertEqual(self.tentar("user-calita").status_code, 401)  # nenhuma contada antes
            self.assertEqual(self.tentar("user-calita", SENHA).status_code, 200)

    def test_limite_por_username_em_qualquer_ip(self):
        with override_settings(LIMITES_PUBLICOS={**LIMITES_ALTOS, "login_usuario": (3, 3600)}):
            for n in range(3):
                self.assertEqual(self.tentar("USER-calita", client=self.de_ip(n + 1)).status_code, 401)
            self.assertEqual(self.tentar("user-calita", client=self.de_ip(50)).status_code, 429)
            self.assertEqual(self.tentar("user-diferente", client=self.de_ip(50)).status_code, 401)

    def test_limite_por_ip_isolado_com_varios_usernames(self):
        with override_settings(LIMITES_PUBLICOS={**LIMITES_ALTOS, "login_ip": (3, 3600)}):
            for i in range(3):
                self.assertEqual(self.tentar(f"user-{i}").status_code, 401)
            self.assertEqual(self.tentar("novo-usuario").status_code, 429)
            self.assertEqual(self.tentar("novo-usuario", client=self.de_ip(8)).status_code, 401)

    def test_usuario_inativo_conta_como_falha(self):
        inativa = criar_profissional("inativa-x")
        inativa.user.is_active = False
        inativa.user.save()
        with override_settings(LIMITES_PUBLICOS={**LIMITES_ALTOS, "login_usuario_ip": (2, 3600)}):
            self.assertEqual(self.tentar("user-inativa-x", SENHA).status_code, 401)
            self.assertEqual(self.tentar("user-inativa-x", SENHA).status_code, 401)
            self.assertEqual(self.tentar("user-inativa-x", SENHA).status_code, 429)


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class SessaoTests(TestCase):
    def setUp(self):
        criar_profissional("calita")

    def login(self, client):
        token = client.get("/api/v1/auth/csrf/").json()["csrfToken"]
        return client.post(
            "/api/v1/auth/login/", {"username": "user-calita", "password": SENHA},
            format="json", HTTP_X_CSRFTOKEN=token,
        )

    def test_login_gira_a_chave_de_sessao_e_o_csrf(self):
        from importlib import import_module

        client = APIClient(enforce_csrf_checks=True)
        engine = import_module(settings.SESSION_ENGINE)
        antiga = engine.SessionStore()
        antiga["visitante"] = True
        antiga.save()
        chave_antiga = antiga.session_key
        client.cookies[settings.SESSION_COOKIE_NAME] = chave_antiga
        client.get("/api/v1/auth/csrf/")
        csrf_antes = client.cookies["csrftoken"].value

        resposta = self.login(client)
        self.assertEqual(resposta.status_code, 200)
        nova = client.cookies[settings.SESSION_COOKIE_NAME].value
        self.assertNotEqual(nova, chave_antiga)
        self.assertFalse(engine.SessionStore(session_key=chave_antiga).exists(chave_antiga))
        self.assertNotEqual(client.cookies["csrftoken"].value, csrf_antes)
        # a chave antiga (fixada por um atacante, por exemplo) nao da acesso
        intruso = APIClient()
        intruso.cookies[settings.SESSION_COOKIE_NAME] = chave_antiga
        self.assertEqual(intruso.get("/api/v1/auth/me/").status_code, 403)

    def test_logout_invalida_a_sessao_antiga(self):
        client = APIClient(enforce_csrf_checks=True)
        self.login(client)
        sessao = client.cookies[settings.SESSION_COOKIE_NAME].value
        token = client.get("/api/v1/auth/csrf/").json()["csrfToken"]
        self.assertEqual(client.post("/api/v1/auth/logout/", HTTP_X_CSRFTOKEN=token).status_code, 204)
        reaproveitada = APIClient()
        reaproveitada.cookies[settings.SESSION_COOKIE_NAME] = sessao
        self.assertEqual(reaproveitada.get("/api/v1/auth/me/").status_code, 403)
        self.assertEqual(reaproveitada.get("/api/v1/painel/servicos/").status_code, 403)
