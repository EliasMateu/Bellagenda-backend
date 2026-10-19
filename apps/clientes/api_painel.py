import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.exceptions import ErroApi
from apps.core.tenancy import TenantModelViewSet, TenantSerializer
from apps.core.throttles import LimiteImportarContatos

from . import importacao
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

    @action(
        detail=False, methods=["post"], url_path="importar", url_name="importar",
        throttle_classes=[LimiteImportarContatos],
    )
    def importar(self, request):
        """Importa contatos {nome, telefone} ja escolhidos no navegador. `simular` (padrao true) nunca grava."""
        dados = request.data
        if not hasattr(dados, "get"):
            raise serializers.ValidationError({"contatos": ["Envie um objeto com a lista contatos."]})
        contatos, simular = dados.get("contatos"), dados.get("simular", True)
        if not isinstance(simular, bool):
            raise serializers.ValidationError({"simular": ["Deve ser verdadeiro ou falso."]})
        if not isinstance(contatos, list) or not contatos:
            raise serializers.ValidationError({"contatos": ["Envie uma lista com pelo menos um contato."]})
        if len(contatos) > importacao.MAX_CONTATOS:
            raise LimiteExcedido(
                f"Envie no maximo {importacao.MAX_CONTATOS} contatos por vez.",
            )
        profissional = request.user.profissional
        itens = importacao.classificar(profissional, contatos)
        criados = 0 if simular else importacao.confirmar(profissional, itens)
        return Response(
            {
                "simulacao": simular,
                "resumo": importacao.resumir(itens),
                "itens": itens,
                "criados": criados,
            }
        )


class LimiteExcedido(ErroApi):
    status_code = 400
    code = "limite_excedido"
    default_detail = "Limite de contatos por requisicao excedido."
