"""Mudanca de status de contas com auditoria unica (admin e API do backoffice usam o mesmo caminho)."""

from django.contrib.admin.models import CHANGE, LogEntry
from django.db import transaction

from .models import Profissional, TransicaoInvalidaError


def mudar_status_auditado(pk, novo_status, por, motivo="", so_de=None):
    """Aplica `Profissional.mudar_status` sob lock de linha e registra o LogEntry do admin.

    `so_de` restringe o status de origem (ex.: aprovar so vale para pendente, reativar so para suspensa).
    Levanta Profissional.DoesNotExist e TransicaoInvalidaError (com `.profissional` e `.antes`).
    """
    with transaction.atomic():
        profissional = Profissional.objects.select_for_update().get(pk=pk)
        antes = profissional.status
        try:
            if so_de is not None and antes != so_de:
                raise TransicaoInvalidaError("acao nao se aplica ao status atual", code="transicao_invalida")
            profissional.mudar_status(novo_status, por, motivo)
        except TransicaoInvalidaError as erro:
            erro.profissional, erro.antes = profissional, antes
            raise
        mensagem = f"Status: {antes} -> {novo_status}."
        if motivo:
            mensagem += f" Motivo: {motivo}"
        LogEntry.objects.log_actions(
            user_id=por.pk, queryset=[profissional], action_flag=CHANGE,
            change_message=mensagem, single_object=True,
        )
    return profissional, antes
