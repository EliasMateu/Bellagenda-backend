"""Isolamento por profissional (tenant) para as APIs do painel.

Falha fechado: sem request/profissional, campos relacionais nao filtrados ou serializers aninhados
gravaveis, o codigo levanta ImproperlyConfigured em vez de aceitar dados de outro tenant.
"""

from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from rest_framework import permissions, serializers, viewsets
from rest_framework.authentication import SessionAuthentication

from apps.contas.models import Profissional


class ProfissionalAtiva(permissions.BasePermission):
    """Exige usuario autenticado com Profissional de status ativa (senao 403)."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        try:
            profissional = user.profissional
        except Profissional.DoesNotExist:
            return False
        return profissional.status == Profissional.Status.ATIVA


def _valores(valor):
    if isinstance(valor, (list, tuple, set)):
        yield from valor
    else:
        yield valor


class TenantSerializer(serializers.ModelSerializer):
    """Serializer base: FKs recebidas devem pertencer a mesma profissional da requisicao.

    O campo `profissional` nunca e gravavel pelo cliente: e removido dos campos e o ViewSet o
    injeta no save().
    """

    def _profissional(self):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        profissional = None
        if user is not None and user.is_authenticated:
            profissional = getattr(user, "profissional", None)
        if profissional is None:
            raise ImproperlyConfigured(
                f"{type(self).__name__} exige context['request'] de um usuario com Profissional."
            )
        return profissional

    def get_fields(self):
        fields = super().get_fields()
        fields.pop("profissional", None)
        profissional = self._profissional()
        for nome, field in fields.items():
            if isinstance(field, serializers.BaseSerializer) and not field.read_only:
                raise ImproperlyConfigured(
                    f"{type(self).__name__}.{nome}: serializers aninhados gravaveis nao sao suportados."
                )
            relacao = field.child_relation if isinstance(field, serializers.ManyRelatedField) else field
            if not isinstance(relacao, serializers.RelatedField):
                continue
            queryset = relacao.queryset
            if queryset is None:  # somente leitura
                continue
            if hasattr(queryset, "do_profissional"):
                # Reduz as opcoes validas ao tenant: id de outro tenant vira "nao existe".
                relacao.queryset = queryset.do_profissional(profissional)
            elif any(f.name == "profissional" for f in queryset.model._meta.get_fields()):
                raise ImproperlyConfigured(
                    f"{type(self).__name__}.{nome}: o modelo relacionado deve usar TenantQuerySet."
                )
        return fields

    def validate(self, attrs):
        attrs = super().validate(attrs)
        profissional = self._profissional()
        for nome, valor in attrs.items():
            for item in _valores(valor):
                if (
                    isinstance(item, models.Model)
                    and hasattr(item, "profissional_id")
                    and item.profissional_id != profissional.pk
                ):
                    raise serializers.ValidationError(
                        {nome: "Registro nao pertence a esta profissional."}
                    )
        return attrs


class TenantModelViewSet(viewsets.ModelViewSet):
    """ViewSet base do painel: sempre filtrado pela profissional logada.

    Objeto de outro tenant nao e encontrado no queryset, entao a resposta e 404 (nunca 403).
    Escritas rodam em savepoint para que violacoes de constraint virem erros 400/409 padronizados
    (ver apps/core/exceptions.py) em vez de 500.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [ProfissionalAtiva]

    def get_queryset(self):
        queryset = super().get_queryset()
        if not hasattr(queryset, "do_profissional"):
            raise ImproperlyConfigured(
                f"{type(self).__name__}: o modelo deve herdar de TenantModel."
            )
        return queryset.do_profissional(self.request.user.profissional)

    def perform_create(self, serializer):
        with transaction.atomic():
            serializer.save(profissional=self.request.user.profissional)

    def perform_update(self, serializer):
        with transaction.atomic():
            serializer.save(profissional=self.request.user.profissional)

    def perform_destroy(self, instance):
        with transaction.atomic():
            instance.delete()
