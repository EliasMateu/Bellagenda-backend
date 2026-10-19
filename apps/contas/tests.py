import os

import psycopg
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from .models import Profissional, SlugImutavelError, TransicaoInvalidaError
from .validators import validar_slug

User = get_user_model()


def criar_profissional(slug="calita", username=None):
    user = User.objects.create_user(username or slug, password="x")
    return Profissional.objects.create(user=user, nome_exibicao="Calita", slug=slug)


class SlugValidadorTests(SimpleTestCase):
    def test_slugs_validos(self):
        for slug in ["abc", "calita", "maria-silva", "a1b", "x" * 40, "123"]:
            with self.subTest(slug=slug):
                validar_slug(slug)

    def test_slugs_invalidos(self):
        for slug in ["", "p", "ab", "x" * 41, "-abc", "abc-", "a--b", "Abc", "a_b", "a b", "açai"]:
            with self.subTest(slug=slug):
                with self.assertRaises(ValidationError):
                    validar_slug(slug)

    def test_slugs_reservados(self):
        for slug in ["admin", "api", "login", "cadastro", "painel", "backoffice", "suporte",
                     "ajuda", "www", "bela", "belaagenda", "static", "media"]:
            with self.subTest(slug=slug):
                with self.assertRaises(ValidationError) as ctx:
                    validar_slug(slug)
                self.assertEqual(ctx.exception.code, "slug_reservado")


class SlugModeloTests(TestCase):
    def test_save_recusa_slug_reservado(self):
        user = User.objects.create_user("u1", password="x")
        with self.assertRaises(ValidationError):
            Profissional.objects.create(user=user, nome_exibicao="X", slug="admin")

    def test_slug_duplicado_com_maiusculas_e_recusado(self):
        criar_profissional("maria")
        user = User.objects.create_user("u2", password="x")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Profissional.objects.create(user=user, nome_exibicao="Outra", slug="MARIA")

    def test_banco_recusa_duplicado_ci_sem_passar_pelo_save(self):
        criar_profissional("joana")
        user = User.objects.create_user("u3", password="x")
        outra = Profissional(user=user, nome_exibicao="Outra", slug="JOANA")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Profissional.objects.bulk_create([outra])

    def test_slug_e_normalizado_para_minusculas(self):
        user = User.objects.create_user("u4", password="x")
        p = Profissional.objects.create(user=user, nome_exibicao="X", slug="Ana-Paula")
        self.assertEqual(Profissional.objects.get(pk=p.pk).slug, "ana-paula")

    def test_slug_imutavel(self):
        p = criar_profissional("calita")
        p.slug = "outro-slug"
        with self.assertRaises(SlugImutavelError):
            p.save()
        self.assertEqual(Profissional.objects.get(pk=p.pk).slug, "calita")

    def test_salvar_outros_campos_mantem_slug(self):
        p = criar_profissional("calita")
        p.nome_exibicao = "Calita Souza"
        p.save()
        self.assertEqual(Profissional.objects.get(pk=p.pk).nome_exibicao, "Calita Souza")

    def test_admin_pode_trocar_slug(self):
        p = criar_profissional("calita")
        admin = User.objects.create_superuser("root", password="x")
        p.alterar_slug_admin("calita-nails", admin)
        self.assertEqual(Profissional.objects.get(pk=p.pk).slug, "calita-nails")
        # a autorizacao nao vaza para saves seguintes
        p.slug = "terceiro"
        with self.assertRaises(SlugImutavelError):
            p.save()

    def test_nao_admin_nao_troca_slug(self):
        p = criar_profissional("calita")
        comum = User.objects.create_user("comum", password="x")
        with self.assertRaises(PermissionDenied):
            p.alterar_slug_admin("novo-slug", comum)
        self.assertEqual(Profissional.objects.get(pk=p.pk).slug, "calita")


class StatusTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("root", password="x")
        self.p = criar_profissional("calita")

    def test_padrao_pendente(self):
        self.assertEqual(self.p.status, Profissional.Status.PENDENTE)
        self.assertIsNone(self.p.aprovado_por)

    def test_aprovar_registra_aprovador(self):
        self.p.mudar_status("ativa", self.admin)
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "ativa")
        self.assertEqual(self.p.aprovado_por, self.admin)
        self.assertIsNotNone(self.p.aprovado_em)

    def test_rejeitar_com_motivo(self):
        self.p.mudar_status("rejeitada", self.admin, "dados incompletos")
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "rejeitada")
        self.assertEqual(self.p.motivo_status, "dados incompletos")

    def test_suspender_e_reativar(self):
        self.p.mudar_status("ativa", self.admin)
        aprovado_em = self.p.aprovado_em
        self.p.mudar_status("suspensa", self.admin, "inadimplencia")
        self.p.mudar_status("ativa", self.admin)
        self.assertEqual(self.p.status, "ativa")
        self.assertEqual(self.p.aprovado_em, aprovado_em)

    def test_transicoes_invalidas(self):
        invalidas = [
            ("pendente", "suspensa"), ("pendente", "pendente"),
            ("ativa", "pendente"), ("ativa", "rejeitada"), ("ativa", "ativa"),
            ("suspensa", "pendente"), ("suspensa", "rejeitada"),
            ("rejeitada", "ativa"), ("rejeitada", "pendente"), ("rejeitada", "suspensa"),
        ]
        for origem, destino in invalidas:
            with self.subTest(origem=origem, destino=destino):
                Profissional.objects.filter(pk=self.p.pk).update(status=origem)
                self.p.refresh_from_db()
                with self.assertRaises(TransicaoInvalidaError):
                    self.p.mudar_status(destino, self.admin)
                self.p.refresh_from_db()
                self.assertEqual(self.p.status, origem)

    def test_somente_superusuario_muda_status(self):
        comum = User.objects.create_user("comum", password="x")
        with self.assertRaises(PermissionDenied):
            self.p.mudar_status("ativa", comum)


class PapelAplicacaoBancoTests(SimpleTestCase):
    """Prova, no PostgreSQL real, que o papel de aplicacao nao tem privilegio de DDL.

    Conecta ao banco de desenvolvimento (ja migrado pelo dono), nao ao banco de teste.
    """

    def _conectar_como_app(self):
        db = settings.DATABASES["default"]
        return psycopg.connect(
            dbname=os.environ["POSTGRES_DB"], host=db["HOST"], port=db["PORT"],
            user=os.environ["POSTGRES_APP_USER"],
            password=os.environ["POSTGRES_APP_PASSWORD"],
            autocommit=True,
        )

    def test_app_nao_cria_tabela(self):
        with self._conectar_como_app() as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("CREATE TABLE tabela_proibida (id int)")

    def test_app_nao_e_superusuario_nem_cria_banco(self):
        with self._conectar_como_app() as conn:
            linha = conn.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles "
                "WHERE rolname = current_user"
            ).fetchone()
        self.assertEqual(linha, (False, False, False))

    def test_app_tem_crud_em_tabelas_do_dono(self):
        with self._conectar_como_app() as conn:
            for privilegio in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                ok = conn.execute(
                    "SELECT has_table_privilege(current_user, 'contas_profissional', %s)",
                    (privilegio,),
                ).fetchone()[0]
                self.assertTrue(ok, privilegio)
