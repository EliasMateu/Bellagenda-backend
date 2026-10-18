from django.urls import path

from . import api_backoffice as v

urlpatterns = [
    path("contas/", v.ContasView.as_view(), name="backoffice-contas"),
    path("contas/<int:pk>/<str:acao>/", v.AcaoContaView.as_view(), name="backoffice-conta-acao"),
]
