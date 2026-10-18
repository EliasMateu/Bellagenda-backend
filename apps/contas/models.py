from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from .validators import validar_slug


class SlugImutavelError(ValidationError):
    """Tentativa de alterar o slug sem o mecanismo do admin da plataforma."""


class TransicaoInvalidaError(ValidationError):
    """Mudanca de status nao permitida."""


class Profissional(models.Model):
    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        ATIVA = "ativa", "Ativa"
        SUSPENSA = "suspensa", "Suspensa"
        REJEITADA = "rejeitada", "Rejeitada"

    TRANSICOES = {
        Status.PENDENTE: {Status.ATIVA, Status.REJEITADA},
        Status.ATIVA: {Status.SUSPENSA},
        Status.SUSPENSA: {Status.ATIVA},
        Status.REJEITADA: set(),
    }

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="profissional"
    )
    nome_exibicao = models.CharField(max_length=100)
    telefone = models.CharField(max_length=20, blank=True)
    slug = models.CharField(max_length=40, validators=[validar_slug])
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDENTE
    )
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="profissionais_aprovadas",
    )
    aprovado_em = models.DateTimeField(null=True, blank=True)
    motivo_status = models.TextField(blank=True)
    termos_versao = models.CharField(max_length=20, null=True, blank=True)
    termos_aceitos_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # Unicidade sem diferenciar maiusculas/minusculas, garantida pelo banco.
            models.UniqueConstraint(Lower("slug"), name="profissional_slug_ci_unico"),
        ]

    def __str__(self):
        return f"{self.nome_exibicao} ({self.slug})"

    def save(self, *args, **kwargs):
        """Normaliza e valida o slug e recusa qualquer mudanca nele depois de criado.

        A troca so e possivel por `alterar_slug_admin`. Observacao: `QuerySet.update()`
        e SQL direto nao passam por aqui.
        """
        self.slug = (self.slug or "").lower()
        validar_slug(self.slug)
        if not self._state.adding:
            atual = (
                type(self).objects.filter(pk=self.pk).values_list("slug", flat=True).first()
            )
            if atual is not None and atual != self.slug and not getattr(
                self, "_troca_slug_autorizada", False
            ):
                raise SlugImutavelError(
                    "O slug e imutavel; somente o admin da plataforma pode altera-lo.",
                    code="slug_imutavel",
                )
        super().save(*args, **kwargs)

    def alterar_slug_admin(self, novo_slug, por):
        """Mecanismo explicito para o admin da plataforma (superusuario) trocar o slug (D-23)."""
        if not (por and por.is_superuser):
            raise PermissionDenied("Somente o admin da plataforma pode alterar o slug.")
        self.slug = novo_slug
        self._troca_slug_autorizada = True
        try:
            self.save()
        finally:
            self._troca_slug_autorizada = False

    def mudar_status(self, novo_status, por, motivo=""):
        """Aplica uma transicao valida de status (apenas superusuario, conforme D-20).

        pendente -> ativa | rejeitada; ativa <-> suspensa. Na aprovacao (pendente -> ativa)
        registra aprovado_por e aprovado_em.
        """
        if not (por and por.is_superuser):
            raise PermissionDenied("Somente o admin da plataforma pode mudar o status.")
        if novo_status not in self.TRANSICOES.get(self.status, set()):
            raise TransicaoInvalidaError(
                f"Transicao invalida: {self.status} -> {novo_status}.", code="transicao_invalida"
            )
        if self.status == self.Status.PENDENTE and novo_status == self.Status.ATIVA:
            self.aprovado_por = por
            self.aprovado_em = timezone.now()
        self.status = novo_status
        self.motivo_status = motivo
        self.save()
