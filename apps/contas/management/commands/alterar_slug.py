from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.contas.models import Profissional
from apps.contas.validators import validar_slug


class Command(BaseCommand):
    help = "Troca o slug de uma profissional (somente o admin da plataforma; D-23)."

    def add_arguments(self, parser):
        parser.add_argument("--slug-atual", required=True)
        parser.add_argument("--novo-slug", required=True)
        parser.add_argument("--por", required=True, help="Superusuario que autoriza a troca")

    def handle(self, *args, **opts):
        atual = opts["slug_atual"].strip().lower()
        novo = opts["novo_slug"].strip().lower()
        admin = get_user_model().objects.filter(
            username=opts["por"], is_superuser=True, is_active=True
        ).first()
        if admin is None:
            raise CommandError("--por deve ser um superusuario ativo existente.")
        try:
            validar_slug(novo)
        except ValidationError as erro:
            raise CommandError(f"Novo slug invalido: {' '.join(erro.messages)}")
        if atual == novo:
            raise CommandError("O novo slug e igual ao atual.")
        if Profissional.objects.filter(slug__iexact=novo).exists():
            raise CommandError(f"O slug '{novo}' ja esta em uso.")

        with transaction.atomic():
            profissional = Profissional.objects.select_for_update().filter(slug__iexact=atual).first()
            if profissional is None:
                raise CommandError(f"Nenhuma profissional com o slug '{atual}'.")
            profissional.alterar_slug_admin(novo, admin)

        self.stdout.write(self.style.SUCCESS(f"Slug alterado: {atual} -> {novo}."))
        self.stdout.write(
            self.style.WARNING(
                "ATENCAO: links de agendamento ja enviados com o slug antigo deixam de funcionar "
                "(404). Reenvie o novo link as clientes."
            )
        )
