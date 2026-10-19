"""Cadastro aberto de profissionais (D-19/D-21/D-33): /api/v1/cadastro/{config,slug,}/

A conta nasce `pendente` e sem login automatico; so passa a valer apos a aprovacao do backoffice.
"""

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clientes.telefone import normalizar_telefone_br
from apps.core.exceptions import ErroApi
from apps.core.throttles import LimiteCadastroPorIP, LimiteChecagemSlugPorIP

from .models import Profissional
from .servicos import criar_horarios_padrao
from .validators import validar_slug

User = get_user_model()

USERNAME_REGEX = re.compile(r"^[a-z0-9._-]{3,30}$")
CONSTRAINT_SLUG = "profissional_slug_ci_unico"
CONSTRAINT_USERNAME = "auth_user_username"  # prefixo do nome gerado pelo Django


class CadastroFechado(ErroApi):
    status_code = status.HTTP_403_FORBIDDEN
    code = "cadastro_fechado"
    default_detail = "O cadastro nao esta aberto no momento."


class LimiteContasPendentes(ErroApi):
    status_code = status.HTTP_409_CONFLICT
    code = "limite_contas_pendentes"
    default_detail = "Ha muitas contas aguardando aprovacao. Tente novamente mais tarde."


class DadosInvalidos(ErroApi):
    """400 generico (isca preenchida): nao diz o que falhou."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "validacao"
    default_detail = "Dados invalidos."


class TenteNovamente(ErroApi):
    status_code = status.HTTP_409_CONFLICT
    code = "tente_novamente"
    default_detail = "Nao foi possivel concluir agora. Tente novamente."


def motivo_slug(valor):
    """None se o slug estiver livre; senao invalido | reservado | em_uso."""
    slug = (valor or "").strip().lower()
    try:
        validar_slug(slug)
    except DjangoValidationError as erro:
        return "reservado" if erro.code == "slug_reservado" else "invalido"
    if Profissional.objects.filter(slug__iexact=slug).exists():
        return "em_uso"
    return None


class CadastroSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=60)
    password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)
    nome_exibicao = serializers.CharField(max_length=100)
    telefone = serializers.CharField(max_length=30)
    slug = serializers.CharField(max_length=60)
    aceite_termos = serializers.BooleanField()
    termos_versao = serializers.CharField(max_length=40)
    website = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_username(self, valor):
        valor = valor.strip().lower()
        if not USERNAME_REGEX.match(valor):
            raise serializers.ValidationError(
                "Use de 3 a 30 caracteres: letras minusculas, numeros, ponto, hifen e sublinhado."
            )
        if User.objects.filter(username__iexact=valor).exists():
            raise serializers.ValidationError("Este usuario ja esta em uso.")
        return valor

    def validate_nome_exibicao(self, valor):
        valor = valor.strip()
        if not 2 <= len(valor) <= 100:
            raise serializers.ValidationError("Use de 2 a 100 caracteres.")
        return valor

    def validate_telefone(self, valor):
        try:
            return normalizar_telefone_br(valor)
        except DjangoValidationError:
            raise serializers.ValidationError("Telefone invalido. Informe DDD + numero.")

    def validate_slug(self, valor):
        motivo = motivo_slug(valor)
        if motivo == "invalido":
            raise serializers.ValidationError(
                "Use de 3 a 40 caracteres: letras minusculas, numeros e hifens "
                "(sem hifens duplos, nem no inicio ou no fim)."
            )
        if motivo == "reservado":
            raise serializers.ValidationError("Este endereco e reservado.")
        if motivo == "em_uso":
            raise serializers.ValidationError("Este endereco ja esta em uso.")
        return valor.strip().lower()

    def validate_aceite_termos(self, valor):
        if valor is not True:
            raise serializers.ValidationError("E preciso aceitar os termos de uso.")
        return valor

    def validate_termos_versao(self, valor):
        if valor != settings.TERMOS_VERSAO_ATUAL:
            raise serializers.ValidationError("Versao dos termos desatualizada. Recarregue a pagina.")
        return valor

    def validate(self, attrs):
        senha, username = attrs["password"], attrs.get("username", "")
        erros = []
        if username and username in senha.lower():
            erros.append("A senha nao pode conter o nome de usuario.")
        try:
            validate_password(senha, User(username=username))
        except DjangoValidationError as erro:
            erros.extend(erro.messages)
        if erros:
            raise serializers.ValidationError({"password": erros})
        return attrs


class CadastroConfigView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {"aberto": settings.CADASTRO_ABERTO, "termos_versao": settings.TERMOS_VERSAO_ATUAL}
        )


class CadastroSlugView(APIView):
    """Sempre 200. Nao diz de quem e o slug nem o status da conta: so em_uso."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LimiteChecagemSlugPorIP]

    def get(self, request):
        motivo = motivo_slug(request.query_params.get("slug", ""))
        return Response({"disponivel": motivo is None, "motivo": motivo})


class CadastroView(APIView):
    authentication_classes = []  # sem sessao => sem CSRF; a defesa e o limite por IP + isca
    permission_classes = [AllowAny]
    throttle_classes = [LimiteCadastroPorIP]

    def post(self, request):
        if not settings.CADASTRO_ABERTO:
            raise CadastroFechado()
        dados_brutos = request.data
        isca = dados_brutos.get("website") if hasattr(dados_brutos, "get") else None
        if isca not in (None, ""):
            raise DadosInvalidos()
        entrada = CadastroSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        dados = entrada.validated_data
        try:
            with transaction.atomic():
                pendentes = Profissional.objects.filter(status=Profissional.Status.PENDENTE).count()
                if pendentes >= settings.CADASTRO_MAX_PENDENTES:
                    raise LimiteContasPendentes()
                user = User(username=dados["username"], is_staff=False, is_superuser=False)
                user.set_password(dados["password"])
                user.save()
                profissional = Profissional.objects.create(
                    user=user,
                    nome_exibicao=dados["nome_exibicao"],
                    telefone=dados["telefone"],
                    slug=dados["slug"],
                    termos_versao=dados["termos_versao"],
                    termos_aceitos_em=timezone.now(),
                )
                criar_horarios_padrao(profissional)
        except IntegrityError as erro:
            # Corrida: outro cadastro levou o usuario ou o slug entre a validacao e o INSERT.
            nome = getattr(getattr(erro.__cause__, "diag", None), "constraint_name", None) or ""
            if nome == CONSTRAINT_SLUG:
                raise serializers.ValidationError({"slug": ["Este endereco ja esta em uso."]})
            if nome.startswith(CONSTRAINT_USERNAME):
                raise serializers.ValidationError({"username": ["Este usuario ja esta em uso."]})
            raise TenteNovamente()
        return Response({"status": Profissional.Status.PENDENTE}, status=status.HTTP_201_CREATED)
