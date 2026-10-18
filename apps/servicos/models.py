from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TenantModel


class Servico(TenantModel):
    nome = models.CharField(max_length=100)
    duracao_min = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    preco = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(0)]
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profissional", "nome"], name="servico_nome_unico_por_profissional"
            ),
            models.CheckConstraint(
                condition=models.Q(duracao_min__gt=0), name="servico_duracao_positiva"
            ),
            models.CheckConstraint(
                condition=models.Q(preco__gte=0), name="servico_preco_nao_negativo"
            ),
        ]

    def __str__(self):
        return self.nome
