"""Backoffice (Django admin restrito): acesso, somente leitura e acoes de status."""

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.agenda.models import Agendamento, Bloqueio, Configuracao, HorarioTrabalho
from apps.clientes.models import Cliente
from apps.core.testutils import admin_plataforma, criar_profissional
from apps.servicos.models import Servico

from .admin_site import backoffice_site
from .models import Profissional

User = get_user_model()
RAIZ = "/" + settings.ADMIN_PATH
LISTA = reverse("backoffice:contas_profissional_changelist")


def mensagens(resposta):
    return [str(m) for m in resposta.context["messages"]]


class AcessoTests(TestCase):
    def setUp(self):
        self.p = criar_profissional("calita", ativa=False)
        self.urls = [RAIZ, LISTA, reverse("backoffice:contas_profissional_change", args=[self.p.pk]),
                     reverse("backoffice:login"), reverse("backoffice:logout")]

    def logado(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_caminho_do_admin_vem_do_ambiente_e_padrao_nao_e_admin(self):
        self.assertTrue(RAIZ.endswith("/"))
        self.assertNotEqual(RAIZ, "/admin/")
        self.assertEqual(Client().get("/admin/").status_code, 404)
        self.assertEqual(Client().get("/admin/login/").status_code, 404)

    def test_anonimo_e_redirecionado_ao_login_do_admin(self):
        for url in (RAIZ, LISTA):
            resposta = Client().get(url)
            self.assertEqual(resposta.status_code, 302)
            self.assertIn(reverse("backoffice:login"), resposta["Location"])
        self.assertEqual(Client().get(reverse("backoffice:login")).status_code, 200)

    def test_usuario_comum_recebe_404_em_tudo(self):
        comum = self.logado(User.objects.create_user("comum", password="x"))
        for url in self.urls:
            with self.subTest(url=url):
                self.assertEqual(comum.get(url).status_code, 404)

    def test_profissional_logada_recebe_404(self):
        client = self.logado(criar_profissional("outra-manicure").user)
        self.assertEqual(client.get(LISTA).status_code, 404)

    def test_staff_sem_superuser_recebe_404(self):
        staff = User.objects.create_user("staff", password="x", is_staff=True)
        client = self.logado(staff)
        for url in self.urls:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 404)
        # nem tenta a acao
        resposta = client.post(LISTA, {"action": "aprovar", "_selected_action": [self.p.pk], "index": 0})
        self.assertEqual(resposta.status_code, 404)
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "pendente")

    def test_superusuario_ativo_acessa(self):
        client = self.logado(admin_plataforma())
        for url in (RAIZ, LISTA, self.urls[2]):
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)

    def test_superusuario_inativo_e_tratado_como_anonimo(self):
        admin_inativo = User.objects.create_superuser("inativo", password="x")
        client = self.logado(admin_inativo)
        admin_inativo.is_active = False
        admin_inativo.save()
        self.assertEqual(client.get(LISTA).status_code, 302)

    def test_login_do_admin_so_aceita_superusuario(self):
        User.objects.create_user("staff", password="senha-forte-123", is_staff=True)
        User.objects.create_superuser("chefe", password="senha-forte-123")
        for username, deve_logar in (("staff", False), ("chefe", True)):
            with self.subTest(username=username):
                client = Client()
                resposta = client.post(
                    reverse("backoffice:login"), {"username": username, "password": "senha-forte-123"}
                )
                if deve_logar:
                    self.assertEqual(resposta.status_code, 302)
                    self.assertEqual(client.get(LISTA).status_code, 200)
                else:
                    self.assertEqual(resposta.status_code, 200)  # formulario com erro
                    self.assertEqual(client.get(LISTA).status_code, 302)

    def test_login_do_admin_aceita_superusuario_sem_flag_staff(self):
        User.objects.create_user(
            "chefe2", password="senha-forte-123", is_superuser=True, is_staff=False
        )
        client = Client()
        resposta = client.post(
            reverse("backoffice:login"), {"username": "chefe2", "password": "senha-forte-123"}
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(client.get(LISTA).status_code, 200)


class RegistroEReadOnlyTests(TestCase):
    def setUp(self):
        self.p = criar_profissional("calita", ativa=False)
        self.client = Client()
        self.client.force_login(admin_plataforma())
        self.change = reverse("backoffice:contas_profissional_change", args=[self.p.pk])

    def test_somente_profissional_esta_registrado(self):
        self.assertEqual(set(backoffice_site._registry), {Profissional})
        proibidos = {Cliente, Agendamento, Servico, Configuracao, HorarioTrabalho, Bloqueio, User}
        self.assertFalse(proibidos & set(backoffice_site._registry))
        self.assertFalse(proibidos & set(admin.site._registry) - {User})  # site padrao nao e exposto

    def test_paginas_de_dados_de_clientes_nao_existem(self):
        for caminho in ("clientes/cliente/", "agenda/agendamento/", "servicos/servico/", "auth/user/"):
            with self.subTest(caminho=caminho):
                self.assertEqual(self.client.get(RAIZ + caminho).status_code, 404)

    def test_indice_lista_so_contas(self):
        texto = self.client.get(RAIZ).content.decode()
        self.assertIn("Profission", texto)
        for proibido in ("Clientes", "Agendamentos", "Servi", "Usu"):
            self.assertNotIn(proibido, texto.split("<main")[1] if "<main" in texto else texto)

    def test_sem_adicionar_nem_excluir(self):
        add = reverse("backoffice:contas_profissional_add")
        delete = reverse("backoffice:contas_profissional_delete", args=[self.p.pk])
        self.assertEqual(self.client.get(add).status_code, 403)
        self.assertEqual(self.client.post(add, {"nome_exibicao": "X"}).status_code, 403)
        self.assertEqual(self.client.get(delete).status_code, 403)
        self.assertEqual(self.client.post(delete, {"post": "yes"}).status_code, 403)
        self.assertTrue(Profissional.objects.filter(pk=self.p.pk).exists())

    def test_acao_de_exclusao_em_massa_nao_existe(self):
        resposta = self.client.get(LISTA)
        nomes = [valor for valor, _ in resposta.context["action_form"].fields["action"].choices]
        self.assertEqual(nomes, ["", "aprovar", "rejeitar", "suspender", "reativar"])
        resposta = self.client.post(
            LISTA, {"action": "delete_selected", "_selected_action": [self.p.pk], "index": 0}, follow=True
        )
        self.assertTrue(Profissional.objects.filter(pk=self.p.pk).exists())

    def test_todos_os_campos_sao_somente_leitura(self):
        resposta = self.client.get(self.change)
        self.assertEqual(resposta.status_code, 200)
        html = resposta.content.decode()
        for campo in ("nome_exibicao", "slug", "status", "motivo_status", "telefone", "aprovado_por"):
            self.assertNotIn(f'name="{campo}"', html)
        self.assertNotIn('name="_save"', html)

    def test_post_no_formulario_nao_altera_nada(self):
        original = Profissional.objects.values().get(pk=self.p.pk)
        resposta = self.client.post(
            self.change, {"status": "ativa", "slug": "outro-slug", "nome_exibicao": "Hack", "_save": "1"}
        )
        self.assertNotEqual(resposta.status_code, 302)
        self.assertEqual(Profissional.objects.values().get(pk=self.p.pk), original)

    def test_lista_mostra_colunas_filtros_e_busca(self):
        criar_profissional("maria-lima")
        html = self.client.get(LISTA).content.decode()
        for esperado in ("calita", "maria-lima"):
            self.assertIn(esperado, html)
        filtrado = self.client.get(LISTA, {"status__exact": "ativa"}).content.decode()
        self.assertIn("maria-lima", filtrado)
        self.assertNotIn(">calita<", filtrado)
        achado = self.client.get(LISTA, {"q": "maria"}).content.decode()
        self.assertIn("maria-lima", achado)
        self.assertNotIn(">calita<", achado)


class AcoesTests(TestCase):
    def setUp(self):
        self.admin = admin_plataforma()
        self.client = Client()
        self.client.force_login(self.admin)
        self.p = criar_profissional("calita", ativa=False)

    def acao(self, nome, *profissionais, **extra):
        dados = {"action": nome, "_selected_action": [p.pk for p in profissionais], **extra}
        if "aplicar" not in extra and nome in ("aprovar", "reativar"):
            dados["index"] = 0
        return self.client.post(LISTA, dados, follow=True)

    def log(self, profissional):
        return [e.get_change_message() for e in LogEntry.objects.filter(
            object_id=str(profissional.pk), action_flag=CHANGE, user=self.admin)]

    def test_aprovar_registra_aprovador_e_historico(self):
        resposta = self.acao("aprovar", self.p)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("1 conta(s) aprovada(s).", mensagens(resposta))
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "ativa")
        self.assertEqual(self.p.aprovado_por, self.admin)
        self.assertIsNotNone(self.p.aprovado_em)
        self.assertTrue(any("pendente -> ativa" in m for m in self.log(self.p)))

    def test_rejeitar_pede_motivo(self):
        pagina = self.client.post(LISTA, {"action": "rejeitar", "_selected_action": [self.p.pk]})
        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, 'name="motivo"')
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "pendente")
        # sem motivo: continua na tela de motivo, com erro, sem alterar
        sem = self.acao("rejeitar", self.p, aplicar="1", motivo="")
        self.assertContains(sem, "obrigat")
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "pendente")
        # com motivo
        ok = self.acao("rejeitar", self.p, aplicar="1", motivo="Dados incompletos")
        self.assertIn("1 conta(s) rejeitada(s).", mensagens(ok))
        self.p.refresh_from_db()
        self.assertEqual((self.p.status, self.p.motivo_status), ("rejeitada", "Dados incompletos"))
        self.assertTrue(any("Dados incompletos" in m for m in self.log(self.p)))

    def test_suspender_e_reativar(self):
        self.acao("aprovar", self.p)
        pagina = self.client.post(LISTA, {"action": "suspender", "_selected_action": [self.p.pk]})
        self.assertContains(pagina, 'name="motivo"')
        resposta = self.acao("suspender", self.p, aplicar="1", motivo="Inadimplencia")
        self.assertIn("1 conta(s) suspensa(s).", mensagens(resposta))
        self.p.refresh_from_db()
        self.assertEqual((self.p.status, self.p.motivo_status), ("suspensa", "Inadimplencia"))
        aprovado_em = self.p.aprovado_em
        resposta = self.acao("reativar", self.p)
        self.assertIn("1 conta(s) reativada(s).", mensagens(resposta))
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "ativa")
        self.assertEqual(self.p.aprovado_em, aprovado_em)  # aprovacao original preservada
        self.assertEqual(len(self.log(self.p)), 3)

    def test_transicao_invalida_vira_mensagem_sem_erro_500(self):
        casos = [
            ("reativar", "pendente"), ("aprovar", "ativa"), ("aprovar", "rejeitada"),
            ("aprovar", "suspensa"), ("reativar", "ativa"),
        ]
        for nome, inicial in casos:
            with self.subTest(acao=nome, inicial=inicial):
                Profissional.objects.filter(pk=self.p.pk).update(status=inicial)
                resposta = self.acao(nome, self.p)
                self.assertEqual(resposta.status_code, 200)
                self.assertTrue(any("transicao invalida" in m for m in mensagens(resposta)), mensagens(resposta))
                self.p.refresh_from_db()
                self.assertEqual(self.p.status, inicial)
        for nome, inicial in (("rejeitar", "ativa"), ("suspender", "pendente"), ("suspender", "rejeitada")):
            with self.subTest(acao=nome, inicial=inicial):
                Profissional.objects.filter(pk=self.p.pk).update(status=inicial)
                resposta = self.acao(nome, self.p, aplicar="1", motivo="teste de motivo")
                self.assertEqual(resposta.status_code, 200)
                self.assertTrue(any("transicao invalida" in m for m in mensagens(resposta)))
                self.p.refresh_from_db()
                self.assertEqual(self.p.status, inicial)

    def test_lote_aplica_os_validos_e_avisa_os_invalidos(self):
        ativa = criar_profissional("ja-ativa")
        resposta = self.acao("aprovar", self.p, ativa)
        textos = mensagens(resposta)
        self.assertIn("1 conta(s) aprovada(s).", textos)
        self.assertTrue(any("ja-ativa" in t or "ja-ativa".title() in t or "transicao invalida" in t for t in textos))
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "ativa")

    def test_acao_so_funciona_para_superusuario(self):
        staff = Client()
        staff.force_login(User.objects.create_user("staff", password="x", is_staff=True))
        resposta = staff.post(LISTA, {"action": "aprovar", "_selected_action": [self.p.pk], "index": 0})
        self.assertEqual(resposta.status_code, 404)
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "pendente")
