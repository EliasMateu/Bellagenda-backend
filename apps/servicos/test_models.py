from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.core.testutils import criar_profissional

from .models import Servico


class ServicoTests(TestCase):
    def setUp(self):
        self.a = criar_profissional("prof-a")
        self.b = criar_profissional("prof-b")

    def test_nome_unico_por_profissional(self):
        Servico.objects.create(profissional=self.a, nome="Esmaltacao", duracao_min=60, preco=Decimal("40"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Servico.objects.create(profissional=self.a, nome="Esmaltacao", duracao_min=30, preco=Decimal("10"))

    def test_mesmo_nome_em_profissionais_diferentes(self):
        Servico.objects.create(profissional=self.a, nome="Esmaltacao", duracao_min=60, preco=Decimal("40"))
        Servico.objects.create(profissional=self.b, nome="Esmaltacao", duracao_min=60, preco=Decimal("40"))
        self.assertEqual(Servico.objects.count(), 2)

    def test_duracao_deve_ser_positiva(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Servico.objects.create(profissional=self.a, nome="X", duracao_min=0, preco=Decimal("1"))

    def test_preco_nao_negativo(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Servico.objects.create(profissional=self.a, nome="X", duracao_min=10, preco=Decimal("-1"))
