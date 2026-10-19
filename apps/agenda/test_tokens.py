import re

from django.test import SimpleTestCase

from .tokens import ALFABETO_CODIGO, gerar_codigo_curto, gerar_token, gerar_token_e_hash, hash_token


class TokensTests(SimpleTestCase):
    def test_token_unico_e_urlsafe(self):
        tokens = {gerar_token() for _ in range(200)}
        self.assertEqual(len(tokens), 200)
        for token in tokens:
            self.assertGreaterEqual(len(token), 43)
            self.assertRegex(token, r"^[A-Za-z0-9_-]+$")

    def test_hash_e_sha256_hex_deterministico(self):
        h = hash_token("abc")
        self.assertEqual(
            h, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )
        self.assertEqual(hash_token("abc"), h)

    def test_token_e_hash_consistentes_e_token_nao_e_o_hash(self):
        token, h = gerar_token_e_hash()
        self.assertEqual(hash_token(token), h)
        self.assertNotIn(token, h)
        self.assertEqual(len(h), 64)

    def test_codigo_curto_8_caracteres_sem_ambiguos(self):
        for _ in range(500):
            codigo = gerar_codigo_curto()
            self.assertEqual(len(codigo), 8)
            self.assertRegex(codigo, r"^[A-Z2-9]{8}$")
            self.assertFalse(re.search(r"[ILO01]", codigo))
        self.assertFalse(set("ILO01") & set(ALFABETO_CODIGO))
