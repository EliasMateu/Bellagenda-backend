from django.db import models


class TenantQuerySet(models.QuerySet):
    def do_profissional(self, profissional):
        """Restringe o queryset aos registros de uma profissional (tenant)."""
        return self.filter(profissional=profissional)


class TenantModel(models.Model):
    """Base de toda tabela de negocio: cada registro pertence a uma profissional (D-18/D-20)."""

    # related_name="+" evita conflito de nomes reversos entre os varios modelos filhos.
    profissional = models.ForeignKey(
        "contas.Profissional",
        on_delete=models.PROTECT,
        db_index=True,
        related_name="+",
    )

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True
