"""Login por sessao: /api/v1/auth/{csrf,login,logout,me}/"""

from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from rest_framework import serializers, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import ErroApi
from apps.core.throttles import LimiteLoginPorIP, LimiteLoginPorUsuario


class CredenciaisInvalidas(ErroApi):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "credenciais_invalidas"
    default_detail = "Usuario ou senha invalidos."


def dados_do_usuario(user):
    dados = {"id": user.pk, "username": user.get_username()}
    profissional = getattr(user, "profissional", None)
    if profissional is None:
        dados["is_superuser"] = user.is_superuser
    else:
        dados["profissional"] = {
            "nome_exibicao": profissional.nome_exibicao,
            "slug": profissional.slug,
            "status": profissional.status,
        }
    return dados


class CsrfView(APIView):
    """Define o cookie csrftoken (o frontend o envia depois no cabecalho X-CSRFToken)."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request._request)})


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=256, trim_whitespace=False)


class LoginView(APIView):
    # Sem autenticacao de sessao para nao exigir login previo; o CSRF e verificado a mao abaixo.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LimiteLoginPorIP, LimiteLoginPorUsuario]

    def post(self, request):
        SessionAuthentication().enforce_csrf(request)
        entrada = LoginSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        user = authenticate(
            request._request,
            username=entrada.validated_data["username"],
            password=entrada.validated_data["password"],
        )
        if user is None or not user.is_active:
            raise CredenciaisInvalidas()
        login(request._request, user)
        return Response(dados_do_usuario(user))


class LogoutView(APIView):
    authentication_classes = [SessionAuthentication]  # exige CSRF para usuario logado
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request._request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(dados_do_usuario(request.user))
