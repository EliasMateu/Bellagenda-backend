"""Isolamento por profissional (tenant) para as APIs do painel."""

from django.core.exceptions import ImproperlyConfigured
from django.db import models
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


class TenantSerializer(serializers.ModelSerializer):
    """Serializer base: FKs recebidas devem pertencer a mesma profissional da requisicao.

    Nao inclua `profissional` em `fields`: ele e injetado pelo ViewSet.
    """

    def _profissional(self):
        request = self.context.get("request")
        return request.user.profissional if request else None

    def get_fields(self):
        fields = super().get_fields()
        profissional = self._profissional()
        if profissional is not None:
            # Reduz as opcoes validas das FKs ao tenant: id de outro tenant vira "nao existe".
            for field in fields.values():
                queryset = getattr(field, "queryset", None)
                if isinstance(queryset, models.QuerySet) and hasattr(queryset, "do_profissional"):
                    field.queryset = queryset.do_profissional(profissional)
        return fields

    def validate(self, attrs):
        attrs = super().validate(attrs)
        profissional = self._profissional()
        if profissional is not None:
            for nome, valor in attrs.items():
                if (
                    isinstance(valor, models.Model)
                    and hasattr(valor, "profissional_id")
                    and valor.profissional_id != profissional.pk
                ):
                    raise serializers.ValidationError(
                        {nome: "Registro nao pertence a esta profissional."}
                    )
        return attrs


class TenantModelViewSet(viewsets.ModelViewSet):
    """ViewSet base do painel: sempre filtrado pela profissional logada.

    Objeto de outro tenant nao e encontrado no queryset, entao a resposta e 404 (nunca 403).
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
        serializer.save(profissional=self.request.user.profissional)
