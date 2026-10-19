from django.db import models

from apps.core.models import TenantModel

from .telefone import normalizar_telefone_br, validar_telefone_e164


class Cliente(TenantModel):
    nome = models.CharField(max_length=100)
    telefone = models.CharField(max_length=16, validators=[validar_telefone_e164])
    observacoes = models.TextField(blank=True)
    consentimento_em = models.DateTimeField(null=True, blank=True)
    anonimizado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profissional", "telefone"], name="cliente_telefone_unico_por_profissional"
            ),
        ]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        """Normaliza o telefone para E.164 antes de gravar (levanta ValidationError se invalido)."""
        self.telefone = normalizar_telefone_br(self.telefone)
        super().save(*args, **kwargs)
