import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import serializers

from apps.core.tenancy import TenantModelViewSet, TenantSerializer

from .models import Cliente
from .telefone import normalizar_telefone_br


class ClienteSerializer(TenantSerializer):
    # Declarado a mao: o validador E.164 do modelo recusaria a entrada antes da normalizacao.
    telefone = serializers.CharField(max_length=30)

    class Meta:
        model = Cliente
        fields = ["id", "nome", "telefone", "observacoes", "consentimento_em", "anonimizado_em"]
        read_only_fields = ["consentimento_em", "anonimizado_em"]

    def validate_telefone(self, valor):
        try:
            telefone = normalizar_telefone_br(valor)
        except DjangoValidationError:
            raise serializers.ValidationError("Telefone invalido. Use DDD + numero.")
        duplicados = Cliente.objects.do_profissional(self._profissional()).filter(telefone=telefone)
        if self.instance is not None:
            duplicados = duplicados.exclude(pk=self.instance.pk)
        if duplicados.exists():
            raise serializers.ValidationError("Ja existe um cliente com este telefone.")
        return telefone


class ClienteViewSet(TenantModelViewSet):
    """Clientes da profissional: listar (com ?busca=), criar, ver e editar. Nao apaga."""

    queryset = Cliente.objects.order_by("nome")
    serializer_class = ClienteSerializer
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        busca = self.request.query_params.get("busca", "").strip()
        if busca and self.action == "list":
            digitos = re.sub(r"\D", "", busca)
            filtro = Q(nome__icontains=busca)
            if digitos:
                filtro |= Q(telefone__contains=digitos)
            queryset = queryset.filter(filtro)
        return queryset
