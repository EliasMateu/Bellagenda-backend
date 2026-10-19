"""Importacao de contatos (D-36): classifica uma lista {nome, telefone} e, ao confirmar, cria so os novos.

O parsing de .vcf/.csv e o seletor de contatos ficam no navegador; aqui chega apenas a lista ja escolhida.
Nenhum nome nem telefone e registrado em log.
"""

import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction

from .models import Cliente
from .telefone import normalizar_telefone_br

MAX_CONTATOS = 300
MAX_NOME_BRUTO = 200
MAX_TELEFONE_BRUTO = 40
NOME_MIN, NOME_MAX = 2, 100
CONSTRAINT_TELEFONE = "cliente_telefone_unico_por_profissional"

NOVO, DUPLICADO, INVALIDO = "novo", "duplicado", "invalido"

# Controles C0/C1, separadores de linha/paragrafo e marcas invisiveis (zero-width, BOM).
_CONTROLE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏  ‪-‮⁠﻿]")


def limpar_nome(valor):
    """Remove caracteres de controle e espacos extras."""
    if not isinstance(valor, str):
        return ""
    return " ".join(_CONTROLE.sub(" ", valor).split())


def formatar_telefone(e164):
    """+5515997669299 -> (15) 99766-9299 ; fixo: (15) 2345-6789."""
    digitos = e164[3:]
    ddd, numero = digitos[:2], digitos[2:]
    return f"({ddd}) {numero[:-4]}-{numero[-4:]}"


def telefones_existentes(profissional, telefones):
    """{telefone: nome} dos clientes DESTE tenant com esses telefones."""
    if not telefones:
        return {}
    return dict(
        Cliente.objects.do_profissional(profissional)
        .filter(telefone__in=list(telefones))
        .values_list("telefone", "nome")
    )


def _item(indice, status, telefone=None, nome="", motivo=None, existente=None):
    return {
        "indice": indice,
        "status": status,
        "telefone": telefone,
        "nome": nome,
        "motivo": motivo,
        "cliente_existente": existente,
    }


def classificar(profissional, contatos):
    """Lista de itens classificados (novo / duplicado / invalido), sem gravar nada."""
    preliminares = []  # (indice, telefone|None, nome, motivo_invalido|None)
    for indice, bruto in enumerate(contatos):
        if not isinstance(bruto, dict):
            preliminares.append((indice, None, "", "telefone_invalido"))
            continue
        nome_bruto, telefone_bruto = bruto.get("nome"), bruto.get("telefone")
        nome_bruto = nome_bruto if isinstance(nome_bruto, str) else ""
        if (
            not isinstance(telefone_bruto, str)
            or len(telefone_bruto) > MAX_TELEFONE_BRUTO
        ):
            preliminares.append((indice, None, limpar_nome(nome_bruto[:MAX_NOME_BRUTO])[:NOME_MAX], "telefone_invalido"))
            continue
        try:
            telefone = normalizar_telefone_br(telefone_bruto)
        except DjangoValidationError:
            preliminares.append((indice, None, limpar_nome(nome_bruto[:MAX_NOME_BRUTO])[:NOME_MAX], "telefone_invalido"))
            continue
        if len(nome_bruto) > MAX_NOME_BRUTO:
            preliminares.append((indice, telefone, "", "nome_invalido"))
            continue
        nome = limpar_nome(nome_bruto)
        if not nome:  # nunca perder um contato valido por falta de nome
            nome = formatar_telefone(telefone)
        if not NOME_MIN <= len(nome) <= NOME_MAX:
            preliminares.append((indice, telefone, nome[:NOME_MAX], "nome_invalido"))
            continue
        preliminares.append((indice, telefone, nome, None))

    existentes = telefones_existentes(
        profissional, {tel for _, tel, _, motivo in preliminares if tel and motivo is None}
    )
    vistos = set()
    itens = []
    for indice, telefone, nome, motivo in preliminares:
        if motivo is not None:
            itens.append(_item(indice, INVALIDO, telefone, nome, motivo))
        elif telefone in existentes:
            vistos.add(telefone)
            itens.append(_item(indice, DUPLICADO, telefone, nome, "ja_cadastrado", existentes[telefone]))
        elif telefone in vistos:
            itens.append(_item(indice, DUPLICADO, telefone, nome, "repetido_na_lista"))
        else:
            vistos.add(telefone)
            itens.append(_item(indice, NOVO, telefone, nome))
    return itens


def _eh_conflito_de_telefone(erro):
    diag = getattr(erro.__cause__, "diag", None)
    return getattr(diag, "constraint_name", None) == CONSTRAINT_TELEFONE


def confirmar(profissional, itens):
    """Cria os itens `novo` numa unica transacao. Colisao de telefone numa corrida vira `duplicado`.

    Altera `itens` in loco e devolve a quantidade criada. Qualquer outro erro desfaz tudo.
    """
    criados = 0
    with transaction.atomic():
        for item in itens:
            if item["status"] != NOVO:
                continue
            try:
                with transaction.atomic():  # savepoint: a colisao nao envenena a transacao externa
                    Cliente.objects.create(
                        profissional=profissional, nome=item["nome"], telefone=item["telefone"]
                    )
            except IntegrityError as erro:
                if not _eh_conflito_de_telefone(erro):
                    raise
                existente = telefones_existentes(profissional, {item["telefone"]}).get(item["telefone"])
                item.update(
                    status=DUPLICADO, motivo="ja_cadastrado", cliente_existente=existente
                )
                continue
            criados += 1
    return criados


def resumir(itens):
    return {
        "total": len(itens),
        "novos": sum(i["status"] == NOVO for i in itens),
        "duplicados": sum(i["status"] == DUPLICADO for i in itens),
        "invalidos": sum(i["status"] == INVALIDO for i in itens),
    }
