import getpass
import os
import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.clientes.telefone import normalizar_telefone_br
from apps.contas.models import Profissional
from apps.contas.servicos import criar_horarios_padrao
from apps.contas.validators import validar_slug

SENHA_ENV = "DJANGO_PROFISSIONAL_PASSWORD"


def _interativo():
    return sys.stdin.isatty()


class Command(BaseCommand):
    help = (
        "Cria o usuario e a Profissional (status pendente por padrao). A senha NUNCA vai por "
        f"argumento: e pedida no terminal ou lida da variavel de ambiente {SENHA_ENV}."
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--nome", required=True, help="Nome de exibicao")
        parser.add_argument("--telefone", required=True)
        parser.add_argument("--slug", required=True)
        parser.add_argument("--ativa", action="store_true", help="Ativa a conta ja na criacao")
        parser.add_argument("--aprovado-por", help="Superusuario que aprova (obrigatorio com --ativa)")
        parser.add_argument(
            "--horarios-padrao", action="store_true",
            help="Cria horarios seg-sex 09:00-18:00 e a Configuracao com padroes",
        )

    def _ler_senha(self):
        if _interativo():
            senha = getpass.getpass("Senha: ")
            if senha != getpass.getpass("Senha (novamente): "):
                raise CommandError("As senhas nao conferem.")
            return senha
        senha = os.environ.get(SENHA_ENV)
        if not senha:
            raise CommandError(
                f"Sem terminal interativo: defina a variavel de ambiente {SENHA_ENV}."
            )
        return senha

    def handle(self, *args, **opts):
        User = get_user_model()
        username, slug = opts["username"].strip(), opts["slug"].strip().lower()

        try:
            validar_slug(slug)
        except ValidationError as erro:
            raise CommandError(f"Slug invalido: {' '.join(erro.messages)}")
        try:
            telefone = normalizar_telefone_br(opts["telefone"])
        except ValidationError:
            raise CommandError("Telefone invalido. Use DDD + numero.")

        if opts["ativa"] and not opts["aprovado_por"]:
            raise CommandError("--ativa exige --aprovado-por (um superusuario).")
        if opts["aprovado_por"] and not opts["ativa"]:
            raise CommandError("--aprovado-por so faz sentido com --ativa.")
        aprovador = None
        if opts["ativa"]:
            aprovador = User.objects.filter(
                username=opts["aprovado_por"], is_superuser=True, is_active=True
            ).first()
            if aprovador is None:
                raise CommandError("--aprovado-por deve ser um superusuario ativo existente.")

        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"O usuario '{username}' ja existe.")
        if Profissional.objects.filter(slug__iexact=slug).exists():
            raise CommandError(f"O slug '{slug}' ja esta em uso.")

        senha = self._ler_senha()
        user_novo = User(username=username)
        try:
            validate_password(senha, user_novo)
        except ValidationError as erro:
            raise CommandError("Senha fraca: " + " ".join(erro.messages))

        with transaction.atomic():
            user_novo.set_password(senha)
            user_novo.save()
            profissional = Profissional.objects.create(
                user=user_novo, nome_exibicao=opts["nome"].strip(), telefone=telefone, slug=slug
            )
            if aprovador is not None:
                profissional.mudar_status("ativa", aprovador, "Criada pelo comando criar_profissional")
            if opts["horarios_padrao"]:
                criar_horarios_padrao(profissional)

        self.stdout.write(
            self.style.SUCCESS(
                f"Profissional '{profissional.nome_exibicao}' criada (slug: {slug}, "
                f"status: {profissional.status})."
            )
        )
