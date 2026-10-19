"""IP real do cliente atras do proxy do frontend (X-Bela-Client-Ip + X-Bela-Proxy-Secret)."""

from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.ip import ip_do_cliente
from apps.core.testutils import LIMITES_ALTOS, criar_profissional

SEGREDO = "s" * 40
DADOS_CADASTRO = {
    "username": "maria.silva", "password": "Senha-segura-9271", "nome_exibicao": "Maria Silva",
    "telefone": "(15) 99766-9299", "slug": "maria-silva", "aceite_termos": True,
    "termos_versao": "2026-10-provisorio", "website": "",
}


def requisicao(remoto="203.0.113.9", segredo=None, ip=None, xff=None):
    extra = {"REMOTE_ADDR": remoto}
    if segredo is not None:
        extra["HTTP_X_BELA_PROXY_SECRET"] = segredo
    if ip is not None:
        extra["HTTP_X_BELA_CLIENT_IP"] = ip
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    return RequestFactory().get("/", **extra)


@override_settings(PROXY_SECRET=SEGREDO)
class IpDoClienteTests(SimpleTestCase):
    def test_segredo_correto_usa_o_ip_do_cabecalho(self):
        self.assertEqual(ip_do_cliente(requisicao(segredo=SEGREDO, ip="198.51.100.7")), "198.51.100.7")

    def test_ipv6_e_normalizado(self):
        r = requisicao(segredo=SEGREDO, ip=" 2001:0db8:0000:0000:0000:0000:0000:0001 ")
        self.assertEqual(ip_do_cliente(r), "2001:db8::1")

    def test_sem_segredo_ou_segredo_errado_ignora_o_cabecalho(self):
        self.assertEqual(ip_do_cliente(requisicao(ip="198.51.100.7")), "203.0.113.9")
        self.assertEqual(ip_do_cliente(requisicao(segredo="errado", ip="198.51.100.7")), "203.0.113.9")
        self.assertEqual(ip_do_cliente(requisicao(segredo="", ip="198.51.100.7")), "203.0.113.9")
        self.assertEqual(ip_do_cliente(requisicao(segredo=SEGREDO[:-1], ip="198.51.100.7")), "203.0.113.9")

    def test_segredo_com_caracteres_nao_ascii_nao_quebra(self):
        self.assertEqual(ip_do_cliente(requisicao(segredo="açaí", ip="198.51.100.7")), "203.0.113.9")

    def test_valor_de_ip_invalido_cai_no_remote_addr(self):
        for ruim in ("", "nao-e-ip", "999.1.1.1", "1.2.3", "198.51.100.7, 10.0.0.1", "198.51.100.7:80"):
            with self.subTest(ip=ruim):
                self.assertEqual(ip_do_cliente(requisicao(segredo=SEGREDO, ip=ruim)), "203.0.113.9")
        self.assertEqual(ip_do_cliente(requisicao(segredo=SEGREDO)), "203.0.113.9")  # sem o cabecalho de IP

    def test_x_forwarded_for_nunca_e_usado(self):
        self.assertEqual(ip_do_cliente(requisicao(xff="198.51.100.7")), "203.0.113.9")
        self.assertEqual(ip_do_cliente(requisicao(segredo=SEGREDO, xff="198.51.100.7")), "203.0.113.9")
        r = requisicao(segredo=SEGREDO, ip="192.0.2.5", xff="198.51.100.7")
        self.assertEqual(ip_do_cliente(r), "192.0.2.5")


class SemSegredoConfiguradoTests(SimpleTestCase):
    @override_settings(PROXY_SECRET="")
    def test_sem_proxy_secret_o_comportamento_de_desenvolvimento_permanece(self):
        r = requisicao(segredo="", ip="198.51.100.7")
        self.assertEqual(ip_do_cliente(r), "203.0.113.9")
        self.assertEqual(ip_do_cliente(requisicao(segredo="qualquer", ip="198.51.100.7")), "203.0.113.9")


@override_settings(PROXY_SECRET=SEGREDO)
class ThrottlesUsamAFuncaoUnicaTests(TestCase):
    def cliente(self, ip=None, segredo=SEGREDO):
        extra = {"REMOTE_ADDR": "10.0.0.1"}  # o proxy da Vercel: igual para todo mundo
        if segredo is not None:
            extra["HTTP_X_BELA_PROXY_SECRET"] = segredo
        if ip is not None:
            extra["HTTP_X_BELA_CLIENT_IP"] = ip
        return APIClient(enforce_csrf_checks=True, **extra)

    def login(self, client, username):
        token = client.get("/api/v1/auth/csrf/").json()["csrfToken"]
        return client.post(
            "/api/v1/auth/login/", {"username": username, "password": "errada"},
            format="json", HTTP_X_CSRFTOKEN=token,
        )

    def test_login_conta_por_ip_real_do_cabecalho(self):
        criar_profissional("calita")
        limites = {**LIMITES_ALTOS, "login_ip": (2, 3600)}
        with override_settings(LIMITES_PUBLICOS=limites):
            a, b = self.cliente("198.51.100.1"), self.cliente("198.51.100.2")
            self.assertEqual(self.login(a, "u1").status_code, 401)
            self.assertEqual(self.login(a, "u2").status_code, 401)
            self.assertEqual(self.login(a, "u3").status_code, 429)  # IP real A esgotou
            self.assertEqual(self.login(b, "u3").status_code, 401)  # IP real B segue livre

    def test_login_sem_segredo_ou_com_segredo_errado_junta_todos_no_remote_addr(self):
        limites = {**LIMITES_ALTOS, "login_ip": (2, 3600)}
        with override_settings(LIMITES_PUBLICOS=limites):
            self.assertEqual(self.login(self.cliente("198.51.100.1", segredo=None), "u1").status_code, 401)
            self.assertEqual(self.login(self.cliente("198.51.100.2", segredo="errado"), "u2").status_code, 401)
            # IP "diferente" no cabecalho sem segredo valido nao escapa do limite do REMOTE_ADDR
            self.assertEqual(self.login(self.cliente("198.51.100.3", segredo=None), "u3").status_code, 429)

    def test_cadastro_conta_por_ip_real_do_cabecalho(self):
        limites = {**LIMITES_ALTOS, "cadastro_ip": (1, 3600)}
        with override_settings(LIMITES_PUBLICOS=limites, CADASTRO_ABERTO=True):
            a, b = self.cliente("198.51.100.1"), self.cliente("198.51.100.2")
            self.assertEqual(a.post("/api/v1/cadastro/", {}, format="json").status_code, 400)
            self.assertEqual(a.post("/api/v1/cadastro/", {}, format="json").status_code, 429)
            self.assertEqual(b.post("/api/v1/cadastro/", DADOS_CADASTRO, format="json").status_code, 201)

    def test_checagem_de_slug_conta_por_ip_real(self):
        limites = {**LIMITES_ALTOS, "cadastro_slug": (1, 3600)}
        with override_settings(LIMITES_PUBLICOS=limites):
            a, b = self.cliente("198.51.100.1"), self.cliente("198.51.100.2")
            self.assertEqual(a.get("/api/v1/cadastro/slug/", {"slug": "livre-um"}).status_code, 200)
            self.assertEqual(a.get("/api/v1/cadastro/slug/", {"slug": "livre-dois"}).status_code, 429)
            self.assertEqual(b.get("/api/v1/cadastro/slug/", {"slug": "livre-dois"}).status_code, 200)

    def test_consulta_publica_conta_por_ip_real(self):
        criar_profissional("calita")
        limites = {**LIMITES_ALTOS, "consulta_token": (1, 3600)}
        corpo = {"token": "x"}
        with override_settings(LIMITES_PUBLICOS=limites):
            a, b = self.cliente("198.51.100.1"), self.cliente("198.51.100.2")
            url = "/api/v1/publico/calita/agendamento/consultar/"
            self.assertEqual(a.post(url, corpo, format="json").status_code, 404)
            self.assertEqual(a.post(url, corpo, format="json").status_code, 429)
            self.assertEqual(b.post(url, corpo, format="json").status_code, 404)

    def test_criacao_publica_conta_por_ip_real(self):
        criar_profissional("calita")
        limites = {**LIMITES_ALTOS, "ip_post": (1, 3600)}
        url = "/api/v1/publico/calita/agendamentos/"
        with override_settings(LIMITES_PUBLICOS=limites):
            a, b = self.cliente("198.51.100.1"), self.cliente("198.51.100.2")
            self.assertNotEqual(a.post(url, {}, format="json").status_code, 429)
            self.assertEqual(a.post(url, {}, format="json").status_code, 429)
            self.assertNotEqual(b.post(url, {}, format="json").status_code, 429)


class ConfiguracaoDeProducaoTests(SimpleTestCase):
    """DEBUG=False sem as variaveis obrigatorias falha com erro claro (importa settings num processo novo)."""

    def rodar(self, **env):
        import os
        import subprocess
        import sys
        from pathlib import Path

        base = Path(__file__).resolve().parents[2]
        ambiente = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "POSTGRES_"))}
        ambiente.update({
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "DJANGO_SETTINGS_MODULE": "config.settings",
            "DJANGO_DEBUG": "False", "POSTGRES_DB": "x", "POSTGRES_APP_USER": "x", "POSTGRES_APP_PASSWORD": "x",
        })
        ambiente.update(env)
        return subprocess.run(
            [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); import config.settings" % str(base)],
            capture_output=True, text=True, env=ambiente, cwd=str(base),
        )

    def test_falta_de_variaveis_obrigatorias(self):
        # Valores vazios explicitos: o .env de desenvolvimento (setdefault) nao os preenche.
        resultado = self.rodar(DJANGO_SECRET_KEY="", DJANGO_ALLOWED_HOSTS="", DJANGO_PROXY_SECRET="")
        self.assertNotEqual(resultado.returncode, 0)
        for nome in ("DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS", "DJANGO_PROXY_SECRET"):
            self.assertIn(nome, resultado.stderr)
        self.assertIn("ImproperlyConfigured", resultado.stderr)

    def test_proxy_secret_curto_demais(self):
        resultado = self.rodar(
            DJANGO_SECRET_KEY="k" * 60, DJANGO_ALLOWED_HOSTS="exemplo.test", DJANGO_PROXY_SECRET="curto"
        )
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn("32", resultado.stderr)

    def test_configuracao_completa_sobe(self):
        resultado = self.rodar(
            DJANGO_SECRET_KEY="k" * 60, DJANGO_ALLOWED_HOSTS="exemplo.test", DJANGO_PROXY_SECRET="p" * 40
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
