from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/publico/<str:slug>/", include("apps.agenda.urls_publico")),
    path("api/v1/auth/", include("apps.contas.urls")),
    path("api/v1/painel/", include("config.painel_urls")),
]
