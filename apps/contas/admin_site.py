"""AdminSite do backoffice: somente superusuario ativo; os demais nao sabem que ele existe."""

import functools

from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.http import Http404


class SuperusuarioAuthenticationForm(AdminAuthenticationForm):
    """Login do backoffice: exige superusuario ativo (o padrao do Django exigiria is_staff)."""

    def confirm_login_allowed(self, user):
        AuthenticationForm.confirm_login_allowed(self, user)  # conta ativa
        if not user.is_superuser:
            raise ValidationError(
                self.error_messages["invalid_login"],
                code="invalid_login",
                params={"username": self.username_field.verbose_name},
            )


class BackofficeAdminSite(admin.AdminSite):
    site_header = "Bela Agenda - Backoffice"
    site_title = "Bela Agenda"
    index_title = "Contas de profissionais"
    login_form = SuperusuarioAuthenticationForm

    def has_permission(self, request):
        user = request.user
        return bool(user.is_active and user.is_superuser)

    def _deve_fingir_que_nao_existe(self, request):
        """Logado, mas sem ser superusuario: 404 (anonimo segue para a tela de login)."""
        return request.user.is_authenticated and not self.has_permission(request)

    def admin_view(self, view, cacheable=False):
        protegida = super().admin_view(view, cacheable)

        @functools.wraps(protegida)
        def envelope(request, *args, **kwargs):
            if self._deve_fingir_que_nao_existe(request):
                raise Http404
            return protegida(request, *args, **kwargs)

        return envelope

    def login(self, request, extra_context=None):
        if self._deve_fingir_que_nao_existe(request):
            raise Http404
        return super().login(request, extra_context)


backoffice_site = BackofficeAdminSite(name="backoffice")
