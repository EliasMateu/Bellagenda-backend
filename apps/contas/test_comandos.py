"""Comandos de gestao criar_profissional e alterar_slug."""

import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command, get_commands, load_command_class
from django.core.management.base import CommandError
from django.test import TestCase

from apps.agenda.models import Configuracao, HorarioTrabalho
from apps.core.testutils import admin_plataforma, criar_profissional
from apps.servicos.models import Servico

from .models import Profissional

User = get_user_model()
SENHA = "Zebra-Quente-47!"
ENV = {"DJANGO_PROFISSIONAL_PASSWORD": SENHA}
BASE = ["--username", "maria", "--nome", "Maria Lima", "--telefone", "(11) 99876-5432", "--slug", "maria-lima"]


def criar(*extra, env=ENV, interativo=False, base=BASE):
    saida = io.StringIO()
    with mock.patch.dict("os.environ", env, clear=False), mock.patch(
        "apps.contas.management.commands.criar_profissional._interativo", return_value=interativo
    ):
        call_command("criar_profissional", *base, *extra, stdout=saida, stderr=io.StringIO())
    return saida.getvalue()


class CriarProfissionalTests(TestCase):
    def test_nao_existe_argumento_de_senha(self):
        comando = load_command_class(get_commands()["criar_profissional"], "criar_profissional")
        parser = comando.create_parser("manage.py", "criar_profissional")
        opcoes = [o for acao in parser._actions for o in acao.option_strings]
        self.assertFalse([o for o in opcoes if "pass" in o or "senha" in o])
        for flag in ("--password=Zebra-Quente-47!", "--senha=Zebra-Quente-47!", "--pass=x"):
            with self.subTest(flag=flag):
                with self.assertRaises(CommandError):
                    call_command("criar_profissional", *BASE, flag)
        self.assertFalse(User.objects.filter(username="maria").exists())

    def test_cria_pendente_com_senha_do_ambiente(self):
        saida = criar()
        p = Profissional.objects.get(slug="maria-lima")
        self.assertEqual(p.status, "pendente")
        self.assertEqual(p.telefone, "+5511998765432")
        self.assertEqual(p.nome_exibicao, "Maria Lima")
        self.assertTrue(p.user.check_password(SENHA))
        self.assertNotIn(SENHA, saida)
        self.assertFalse(HorarioTrabalho.objects.exists())
        self.assertFalse(Configuracao.objects.exists())
        self.assertFalse(Servico.objects.exists())

    def test_senha_interativa_via_getpass(self):
        with mock.patch("getpass.getpass", side_effect=[SENHA, SENHA]) as pedir:
            criar(interativo=True, env={})
        self.assertEqual(pedir.call_count, 2)
        self.assertTrue(Profissional.objects.get(slug="maria-lima").user.check_password(SENHA))

    def test_senhas_interativas_diferentes(self):
        with mock.patch("getpass.getpass", side_effect=[SENHA, "outra"]):
            with self.assertRaisesMessage(CommandError, "nao conferem"):
                criar(interativo=True, env={})
        self.assertFalse(User.objects.filter(username="maria").exists())

    def test_nao_interativo_sem_variavel_falha(self):
        with mock.patch.dict("os.environ", {}, clear=False) as ambiente:
            ambiente.pop("DJANGO_PROFISSIONAL_PASSWORD", None)
            with mock.patch(
                "apps.contas.management.commands.criar_profissional._interativo", return_value=False
            ):
                with self.assertRaisesMessage(CommandError, "DJANGO_PROFISSIONAL_PASSWORD"):
                    call_command("criar_profissional", *BASE)
        self.assertFalse(User.objects.filter(username="maria").exists())

    def test_senha_fraca_e_recusada(self):
        with self.assertRaisesMessage(CommandError, "Senha fraca"):
            criar(env={"DJANGO_PROFISSIONAL_PASSWORD": "12345678"})
        self.assertFalse(User.objects.filter(username="maria").exists())

    def test_slug_invalido_reservado_e_duplicado(self):
        def com_slug(slug):
            return ["--username", "maria", "--nome", "M", "--telefone", "11998765432", f"--slug={slug}"]

        for slug in ("ab", "-abc", "a--b", "Slug Com Espaco", "x" * 41):
            with self.subTest(slug=slug):
                with self.assertRaisesMessage(CommandError, "Slug invalido"):
                    criar(base=com_slug(slug))
        for reservado in ("admin", "api", "login", "belaagenda"):
            with self.subTest(reservado=reservado):
                with self.assertRaisesMessage(CommandError, "Slug invalido"):
                    criar(base=com_slug(reservado))
        criar()
        with self.assertRaisesMessage(CommandError, "ja esta em uso"):
            criar(base=["--username", "outra", "--nome", "O", "--telefone", "11998765433", "--slug", "MARIA-LIMA"])
        self.assertEqual(Profissional.objects.count(), 1)
        self.assertFalse(User.objects.filter(username="outra").exists())

    def test_username_duplicado(self):
        criar()
        with self.assertRaisesMessage(CommandError, "ja existe"):
            criar(base=["--username", "MARIA", "--nome", "O", "--telefone", "11998765433", "--slug", "outro-slug"])

    def test_telefone_invalido(self):
        with self.assertRaisesMessage(CommandError, "Telefone invalido"):
            criar(base=["--username", "maria", "--nome", "M", "--telefone", "123", "--slug", "maria-lima"])

    def test_horarios_padrao(self):
        criar("--horarios-padrao")
        p = Profissional.objects.get(slug="maria-lima")
        horarios = HorarioTrabalho.objects.filter(profissional=p).order_by("dia_semana")
        self.assertEqual([h.dia_semana for h in horarios], [0, 1, 2, 3, 4])
        self.assertTrue(all((h.inicio.hour, h.fim.hour) == (9, 18) for h in horarios))
        config = Configuracao.objects.get(profissional=p)
        self.assertEqual(config.intervalo_entre_atendimentos_min, 10)
        self.assertEqual(config.antecedencia_min_cancelamento_h, 24)
        self.assertFalse(Servico.objects.exists())

    def test_ativa_com_aprovador(self):
        admin = admin_plataforma()
        criar("--ativa", "--aprovado-por", admin.username)
        p = Profissional.objects.get(slug="maria-lima")
        self.assertEqual((p.status, p.aprovado_por), ("ativa", admin))
        self.assertIsNotNone(p.aprovado_em)

    def test_ativa_exige_superusuario_existente(self):
        with self.assertRaisesMessage(CommandError, "--aprovado-por"):
            criar("--ativa")
        with self.assertRaisesMessage(CommandError, "superusuario"):
            criar("--ativa", "--aprovado-por", "ninguem")
        comum = User.objects.create_user("comum", password="x")
        with self.assertRaisesMessage(CommandError, "superusuario"):
            criar("--ativa", "--aprovado-por", comum.username)
        with self.assertRaisesMessage(CommandError, "--ativa"):
            criar("--aprovado-por", admin_plataforma().username)
        self.assertFalse(User.objects.filter(username="maria").exists())

    def test_atomico_se_falhar_no_meio_nada_fica(self):
        with mock.patch(
            "apps.contas.management.commands.criar_profissional.HorarioTrabalho.objects.create",
            side_effect=RuntimeError("falha"),
        ):
            with self.assertRaises(RuntimeError):
                criar("--horarios-padrao")
        self.assertFalse(User.objects.filter(username="maria").exists())
        self.assertFalse(Profissional.objects.exists())


class AlterarSlugTests(TestCase):
    def setUp(self):
        self.admin = admin_plataforma()
        self.p = criar_profissional("calita")

    def alterar(self, **kw):
        dados = {"slug_atual": "calita", "novo_slug": "calita-nails", "por": self.admin.username, **kw}
        saida = io.StringIO()
        call_command(
            "alterar_slug", "--slug-atual", dados["slug_atual"], "--novo-slug", dados["novo_slug"],
            "--por", dados["por"], stdout=saida,
        )
        return saida.getvalue()

    def test_altera_e_avisa_sobre_links(self):
        saida = self.alterar()
        self.p.refresh_from_db()
        self.assertEqual(self.p.slug, "calita-nails")
        self.assertIn("deixam de funcionar", saida)

    def test_slug_atual_e_case_insensitive(self):
        self.alterar(slug_atual="CALITA")
        self.p.refresh_from_db()
        self.assertEqual(self.p.slug, "calita-nails")

    def test_recusas(self):
        outra = criar_profissional("maria-lima")
        casos = [
            ({"novo_slug": "admin"}, "invalido"),
            ({"novo_slug": "a--b"}, "invalido"),
            ({"novo_slug": "MARIA-LIMA"}, "em uso"),
            ({"novo_slug": "calita"}, "igual"),
            ({"slug_atual": "nao-existe"}, "Nenhuma profissional"),
            ({"por": "ninguem"}, "superusuario"),
        ]
        for kw, trecho in casos:
            with self.subTest(**kw):
                with self.assertRaisesMessage(CommandError, trecho):
                    self.alterar(**kw)
        comum = User.objects.create_user("comum", password="x")
        with self.assertRaisesMessage(CommandError, "superusuario"):
            self.alterar(por=comum.username)
        self.p.refresh_from_db()
        outra.refresh_from_db()
        self.assertEqual((self.p.slug, outra.slug), ("calita", "maria-lima"))
