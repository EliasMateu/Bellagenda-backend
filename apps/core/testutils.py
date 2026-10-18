"""Fabricas simples usadas apenas pelos testes."""

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.agenda.models import Agendamento
from apps.agenda.tokens import gerar_token_e_hash
from apps.clientes.models import Cliente
from apps.contas.models import Profissional
from apps.servicos.models import Servico

User = get_user_model()

_contador = {"n": 0}


def proximo():
    _contador["n"] += 1
    return _contador["n"]


def admin_plataforma():
    user = User.objects.filter(username="admin-plataforma").first()
    return user or User.objects.create_superuser("admin-plataforma", password="x")


def criar_profissional(slug, ativa=True):
    user = User.objects.create_user(f"user-{slug}", password="senha-forte-123")
    profissional = Profissional.objects.create(user=user, nome_exibicao=slug, slug=slug)
    if ativa:
        profissional.mudar_status("ativa", admin_plataforma())
    return profissional


def criar_servico(profissional, nome=None, duracao_min=60):
    return Servico.objects.create(
        profissional=profissional,
        nome=nome or f"Servico {proximo()}",
        duracao_min=duracao_min,
        preco=Decimal("50.00"),
    )


def criar_cliente(profissional, telefone=None):
    n = proximo()
    return Cliente.objects.create(
        profissional=profissional,
        nome=f"Cliente {n}",
        telefone=telefone or f"119{n:08d}",
    )


# Base fixa para os testes de agenda: 2030-01-07 (segunda-feira) 10:00 UTC.
BASE = datetime(2030, 1, 7, 10, 0, tzinfo=dt_timezone.utc)


def criar_agendamento(profissional, inicio_min=0, duracao_min=60, cliente=None, servico=None, **extra):
    """Cria um agendamento a `inicio_min` minutos de BASE, com a duracao pedida."""
    inicio = BASE + timedelta(minutes=inicio_min)
    extra.setdefault("token_hash", gerar_token_e_hash()[1])
    return Agendamento.objects.create(
        profissional=profissional,
        cliente=cliente or criar_cliente(profissional),
        servico=servico or criar_servico(profissional),
        inicio=inicio,
        fim=inicio + timedelta(minutes=duracao_min),
        preco_cobrado=Decimal("50.00"),
        **extra,
    )
