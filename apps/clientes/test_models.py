from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from apps.core.testutils import criar_profissional

from .models import Cliente
from .telefone import normalizar_telefone_br


class TelefoneTests(SimpleTestCase):
    def test_normaliza_formatos_validos(self):
        casos = {
            "(11) 99876-5432": "+5511998765432",
            "11998765432": "+5511998765432",
            "+55 11 99876-5432": "+5511998765432",
            "5511998765432": "+5511998765432",
            "(21) 2345-6789": "+552123456789",
            "55 99999-9999": "+5555999999999",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(normalizar_telefone_br(entrada), esperado)

    def test_recusa_invalidos(self):
        for entrada in ["", "123", "(11) 88876-5432", "(01) 99876-5432", "(11) 1234-5678", "abc",
                        "+1 415 555 2671", "119987654321"]:
            with self.subTest(entrada=entrada):
                with self.assertRaises(ValidationError):
                    normalizar_telefone_br(entrada)


class ClienteTests(TestCase):
    def setUp(self):
        self.a = criar_profissional("prof-a")
        self.b = criar_profissional("prof-b")

    def test_save_normaliza_telefone(self):
        c = Cliente.objects.create(profissional=self.a, nome="Ana", telefone="(11) 99876-5432")
        self.assertEqual(Cliente.objects.get(pk=c.pk).telefone, "+5511998765432")

    def test_save_recusa_telefone_invalido(self):
        with self.assertRaises(ValidationError):
            Cliente.objects.create(profissional=self.a, nome="Ana", telefone="123")

    def test_telefone_unico_por_profissional(self):
        Cliente.objects.create(profissional=self.a, nome="Ana", telefone="11998765432")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cliente.objects.create(profissional=self.a, nome="Ana 2", telefone="(11) 99876-5432")

    def test_mesmo_telefone_em_profissionais_diferentes(self):
        Cliente.objects.create(profissional=self.a, nome="Ana", telefone="11998765432")
        Cliente.objects.create(profissional=self.b, nome="Ana", telefone="11998765432")
        self.assertEqual(Cliente.objects.count(), 2)

    def test_do_profissional(self):
        Cliente.objects.create(profissional=self.a, nome="Ana", telefone="11998765432")
        Cliente.objects.create(profissional=self.b, nome="Bia", telefone="11998765433")
        self.assertEqual(
            list(Cliente.objects.do_profissional(self.a).values_list("nome", flat=True)), ["Ana"]
        )
