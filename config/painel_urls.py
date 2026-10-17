"""Rotas do painel (/api/v1/painel/). Tudo aqui usa TenantModelViewSet ou filtra pelo tenant logado."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from apps.agenda.api_painel import (
    AgendamentoViewSet,
    BloqueioViewSet,
    ConfiguracaoView,
    HorarioTrabalhoViewSet,
)
from apps.clientes.api_painel import ClienteViewSet
from apps.servicos.api_painel import ServicoViewSet

router = SimpleRouter()
router.register("servicos", ServicoViewSet, basename="painel-servico")
router.register("clientes", ClienteViewSet, basename="painel-cliente")
router.register("horarios-trabalho", HorarioTrabalhoViewSet, basename="painel-horario")
router.register("bloqueios", BloqueioViewSet, basename="painel-bloqueio")
router.register("agendamentos", AgendamentoViewSet, basename="painel-agendamento")

urlpatterns = [
    path("configuracao/", ConfiguracaoView.as_view(), name="painel-configuracao"),
    *router.urls,
]
