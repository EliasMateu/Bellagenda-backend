from django.test import TestCase


class SaudeTests(TestCase):
    def test_saude_retorna_ok_sem_autenticacao(self):
        resposta = self.client.get("/api/v1/saude/")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"status": "ok"})
