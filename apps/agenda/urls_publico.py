from django.urls import path

from . import api_publica as v

urlpatterns = [
    path("servicos/", v.ServicosView.as_view(), name="publico-servicos"),
    path("horarios/", v.HorariosView.as_view(), name="publico-horarios"),
    path("agendamentos/", v.CriarAgendamentoView.as_view(), name="publico-agendar"),
    path("agendamento/consultar/", v.ConsultarAgendamentoView.as_view(), name="publico-consultar"),
    path("agendamento/cancelar/", v.CancelarAgendamentoView.as_view(), name="publico-cancelar"),
]
