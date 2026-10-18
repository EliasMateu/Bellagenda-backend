from django.conf import settings
from django.urls import include, path

from apps.contas import admin as _  # noqa: F401  (registra o Profissional no backoffice)
from apps.contas.admin_site import backoffice_site

urlpatterns = [
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/publico/<str:slug>/", include("apps.agenda.urls_publico")),
    path("api/v1/auth/", include("apps.contas.urls")),
    path("api/v1/painel/", include("config.painel_urls")),
    path(settings.ADMIN_PATH, backoffice_site.urls),
]
