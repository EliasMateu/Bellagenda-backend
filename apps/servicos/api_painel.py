from rest_framework import serializers

from apps.core.tenancy import TenantModelViewSet, TenantSerializer

from .models import Servico


class ServicoSerializer(TenantSerializer):
    class Meta:
        model = Servico
        fields = ["id", "nome", "duracao_min", "preco", "ativo"]

    def validate_nome(self, valor):
        valor = valor.strip()
        duplicados = Servico.objects.do_profissional(self._profissional()).filter(nome=valor)
        if self.instance is not None:
            duplicados = duplicados.exclude(pk=self.instance.pk)
        if duplicados.exists():
            raise serializers.ValidationError("Ja existe um servico com este nome.")
        return valor


class ServicoViewSet(TenantModelViewSet):
    """CRUD de servicos. Apagar um servico ja usado em agendamentos devolve 409 (use ativo=false)."""

    queryset = Servico.objects.order_by("nome")
    serializer_class = ServicoSerializer
