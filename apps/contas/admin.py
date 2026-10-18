"""Backoffice: SOMENTE o modelo Profissional e registrado.

Cliente, Agendamento, Servico etc. ficam de fora de proposito (LGPD / menor privilegio): o admin
da plataforma aprova contas, mas nao navega pelos dados dos clientes das manicures.
"""

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.template.response import TemplateResponse
from django.urls import reverse

from .admin_site import backoffice_site
from .models import Profissional, TransicaoInvalidaError


class MotivoForm(forms.Form):
    _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
    action = forms.CharField(widget=forms.HiddenInput)
    motivo = forms.CharField(
        label="Motivo", min_length=3, max_length=500, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Fica registrado no historico da conta.",
    )


class ProfissionalAdmin(admin.ModelAdmin):
    list_display = ("nome_exibicao", "slug", "telefone", "status", "aprovado_por", "aprovado_em")
    list_filter = ("status",)
    search_fields = ("nome_exibicao", "slug", "telefone")
    list_select_related = ("aprovado_por",)
    ordering = ("nome_exibicao",)
    actions = ["aprovar", "rejeitar", "suspender", "reativar"]
    readonly_fields = (
        "user", "nome_exibicao", "telefone", "slug", "status", "aprovado_por", "aprovado_em",
        "motivo_status", "termos_versao", "termos_aceitos_em",
    )
    fields = readonly_fields

    # Somente leitura: o status so muda pelas acoes (que respeitam a matriz de transicoes).
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        raise PermissionDenied

    def get_actions(self, request):
        acoes = super().get_actions(request)
        acoes.pop("delete_selected", None)
        return acoes

    # ---- aplicacao das transicoes
    def _aplicar(self, request, queryset, novo_status, motivo, rotulo, so_de=None):
        aplicadas = 0
        for pk in queryset.values_list("pk", flat=True):
            with transaction.atomic():
                profissional = Profissional.objects.select_for_update().get(pk=pk)
                antes = profissional.status
                try:
                    if so_de is not None and antes != so_de:
                        # ex.: "aprovar" so vale para pendente e "reativar" so para suspensa
                        raise TransicaoInvalidaError("acao nao se aplica ao status atual")
                    profissional.mudar_status(novo_status, request.user, motivo)
                except TransicaoInvalidaError:
                    self.message_user(
                        request,
                        f"{profissional.nome_exibicao}: transicao invalida "
                        f"({antes} -> {novo_status}); nada foi alterado.",
                        messages.ERROR,
                    )
                    continue
                mensagem = f"Status: {antes} -> {novo_status}."
                if motivo:
                    mensagem += f" Motivo: {motivo}"
                self.log_change(request, profissional, mensagem)
                aplicadas += 1
        if aplicadas:
            self.message_user(request, f"{aplicadas} conta(s) {rotulo}.", messages.SUCCESS)

    def _com_motivo(self, request, queryset, novo_status, titulo, botao, rotulo):
        if "aplicar" in request.POST:
            form = MotivoForm(request.POST)
            if form.is_valid():
                self._aplicar(request, queryset, novo_status, form.cleaned_data["motivo"], rotulo)
                return None
        else:
            form = MotivoForm(
                initial={
                    "_selected_action": request.POST.getlist(ACTION_CHECKBOX_NAME),
                    "action": request.POST["action"],
                }
            )
        contexto = {
            **self.admin_site.each_context(request),
            "title": titulo,
            "form": form,
            "profissionais": queryset,
            "botao": botao,
            "opts": self.model._meta,
            "voltar": reverse(f"{self.admin_site.name}:contas_profissional_changelist"),
        }
        return TemplateResponse(request, "admin/contas/profissional/motivo.html", contexto)

    @admin.action(description="Aprovar contas selecionadas")
    def aprovar(self, request, queryset):
        self._aplicar(
            request, queryset, Profissional.Status.ATIVA, "", "aprovada(s)", so_de=Profissional.Status.PENDENTE
        )

    @admin.action(description="Rejeitar contas selecionadas (pede motivo)")
    def rejeitar(self, request, queryset):
        return self._com_motivo(
            request, queryset, Profissional.Status.REJEITADA,
            "Rejeitar contas", "Rejeitar", "rejeitada(s)",
        )

    @admin.action(description="Suspender contas selecionadas (pede motivo)")
    def suspender(self, request, queryset):
        return self._com_motivo(
            request, queryset, Profissional.Status.SUSPENSA,
            "Suspender contas", "Suspender", "suspensa(s)",
        )

    @admin.action(description="Reativar contas suspensas")
    def reativar(self, request, queryset):
        self._aplicar(
            request, queryset, Profissional.Status.ATIVA, "", "reativada(s)", so_de=Profissional.Status.SUSPENSA
        )


backoffice_site.register(Profissional, ProfissionalAdmin)
