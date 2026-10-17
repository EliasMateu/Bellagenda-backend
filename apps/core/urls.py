from django.urls import path

from .views import SaudeView

urlpatterns = [
    path("saude/", SaudeView.as_view(), name="saude"),
]
