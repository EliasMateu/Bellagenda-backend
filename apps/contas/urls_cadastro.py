from django.urls import path

from . import api_cadastro as v

urlpatterns = [
    path("config/", v.CadastroConfigView.as_view(), name="cadastro-config"),
    path("slug/", v.CadastroSlugView.as_view(), name="cadastro-slug"),
    path("", v.CadastroView.as_view(), name="cadastro"),
]
