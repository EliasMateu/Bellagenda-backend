"""Importacao de contatos: POST /api/v1/painel/clientes/importar/ (D-36)."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.core.testutils import (
    LIMITES_ALTOS, admin_plataforma, cliente_logado, criar_profissional,
)

URL = "/api/v1/painel/clientes/importar/"


def contato(nome="Maria", telefone="(15) 99766-9299"):
    return {"nome": nome, "telefone": telefone}


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class ImportacaoBase(TestCase):
    def setUp(self):
        self.p = criar_profissional("calita")
        self.outra = criar_profissional("outra-manicure")
        self.client = cliente_logado(self.p.user)

    def enviar(self, contatos, simular=True, client=None, **extra):
        corpo = {"contatos": contatos, "simular": simular, **extra}
        return (client or self.client).post(URL, corpo, format="json")

    def meus(self):
        return Cliente.objects.filter(profissional=self.p)


class PreviaTests(ImportacaoBase):
    def test_classifica_novo_duplicado_repetido_e_invalido_sem_gravar(self):
        Cliente.objects.create(profissional=self.p, nome="Ana Existente", telefone="+5511988887777")
        resposta = self.enviar([
            contato("Maria", "(15) 99766-9299"),        # novo
            contato("Ana", "11 98888-7777"),            # ja cadastrado
            contato("Maria de novo", "+55 15 99766 9299"),  # repetido na lista
            contato("Fulano", "12345"),                 # telefone invalido
            contato("Beltrano", "+1 415 555 2671"),     # estrangeiro
        ])
        self.assertEqual(resposta.status_code, 200, resposta.content)
        dados = resposta.json()
        self.assertTrue(dados["simulacao"])
        self.assertEqual(dados["criados"], 0)
        self.assertEqual(dados["resumo"], {"total": 5, "novos": 1, "duplicados": 2, "invalidos": 2})
        itens = dados["itens"]
        self.assertEqual([i["indice"] for i in itens], [0, 1, 2, 3, 4])
        self.assertEqual(
            [i["status"] for i in itens], ["novo", "duplicado", "duplicado", "invalido", "invalido"]
        )
        self.assertEqual(itens[0]["telefone"], "+5515997669299")
        self.assertEqual(itens[1]["motivo"], "ja_cadastrado")
        self.assertEqual(itens[1]["cliente_existente"], "Ana Existente")
        self.assertEqual(itens[2]["motivo"], "repetido_na_lista")
        self.assertIsNone(itens[2]["cliente_existente"])
        self.assertEqual(itens[3]["motivo"], "telefone_invalido")
        self.assertIsNone(itens[3]["telefone"])
        self.assertEqual(itens[4]["motivo"], "telefone_invalido")
        self.assertEqual(self.meus().count(), 1)  # a previa nunca grava

    def test_simular_ausente_e_previa(self):
        resposta = self.client.post(URL, {"contatos": [contato()]}, format="json")
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()["simulacao"])
        self.assertEqual(self.meus().count(), 0)

    def test_formatos_de_telefone(self):
        casos = {
            "+5515997669299": "+5515997669299",
            "+55 (15) 99766-9299": "+5515997669299",
            "(15) 99766-9299": "+5515997669299",
            "15997669299": "+5515997669299",
            "015997669299": None,          # 0 inicial de operadora nao e aceito
            "(15) 3234-5678": "+551532345678",   # fixo
            "5515997669299": "+5515997669299",
            "1599766929": None,            # sem o 9o digito (celular de 8 digitos) nao vale
            "+351 912 345 678": None,      # estrangeiro
            "abc": None,
            "": None,
        }
        resposta = self.enviar([contato("Nome", tel) for tel in casos]).json()
        for item, (entrada, esperado) in zip(resposta["itens"], casos.items()):
            with self.subTest(entrada=entrada):
                self.assertEqual(item["telefone"], esperado)
                # formatos diferentes do mesmo numero caem em repetido_na_lista (duplicado), nunca em invalido
                self.assertEqual(item["status"] == "invalido", esperado is None)

    def test_nome_vazio_usa_o_telefone_formatado(self):
        for nome in ("", "   ", None, "\t\n"):
            with self.subTest(nome=nome):
                item = self.enviar([{"nome": nome, "telefone": "15997669299"}]).json()["itens"][0]
                self.assertEqual(item["status"], "novo")
                self.assertEqual(item["nome"], "(15) 99766-9299")
        item = self.enviar([{"telefone": "(15) 3234-5678"}]).json()["itens"][0]  # sem a chave nome
        self.assertEqual(item["nome"], "(15) 3234-5678")

    def test_nome_com_espacos_e_caracteres_de_controle(self):
        item = self.enviar([contato("  Maria \t  da\n Silva\x00\x07 ​ ")]).json()["itens"][0]
        self.assertEqual(item["nome"], "Maria da Silva")
        self.assertEqual(item["status"], "novo")

    def test_nome_fora_de_2_a_100_e_invalido(self):
        for nome, valido in (("A", False), ("Al", True), ("x" * 100, True), ("x" * 101, False), ("x" * 201, False)):
            with self.subTest(tamanho=len(nome)):
                item = self.enviar([contato(nome)]).json()["itens"][0]
                self.assertEqual(item["status"], "novo" if valido else "invalido")
                if not valido:
                    self.assertEqual(item["motivo"], "nome_invalido")
                    self.assertEqual(item["telefone"], "+5515997669299")

    def test_campos_acima_do_tamanho_bruto_sao_invalidos(self):
        item = self.enviar([contato("Maria", "1" * 41)]).json()["itens"][0]
        self.assertEqual((item["status"], item["motivo"]), ("invalido", "telefone_invalido"))
        item = self.enviar([contato("m" * 201, "(15) 99766-9299")]).json()["itens"][0]
        self.assertEqual((item["status"], item["motivo"]), ("invalido", "nome_invalido"))

    def test_itens_malformados_sao_invalidos_sem_derrubar_a_requisicao(self):
        resposta = self.enviar(["texto", 3, None, {"nome": "X"}, {"nome": "Y", "telefone": 15997669299}, []])
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.assertEqual({i["status"] for i in resposta.json()["itens"]}, {"invalido"})

    def test_duplicado_so_conta_se_o_primeiro_valia(self):
        # O 1o tem nome invalido (nao sera criado); o 2o, com o mesmo telefone e nome bom, e novo.
        itens = self.enviar([contato("A"), contato("Maria Boa")]).json()["itens"]
        self.assertEqual([i["status"] for i in itens], ["invalido", "novo"])

    def test_telefone_igual_em_outro_tenant_nao_e_duplicado_e_nao_vaza(self):
        Cliente.objects.create(profissional=self.outra, nome="Cliente Secreta da Outra", telefone="+5515997669299")
        resposta = self.enviar([contato("Maria", "(15) 99766-9299")])
        item = resposta.json()["itens"][0]
        self.assertEqual(item["status"], "novo")
        self.assertIsNone(item["cliente_existente"])
        self.assertNotIn("Secreta", resposta.content.decode())
        self.enviar([contato("Maria", "(15) 99766-9299")], simular=False)
        self.assertEqual(self.meus().count(), 1)
        self.assertEqual(Cliente.objects.filter(profissional=self.outra).count(), 1)  # a outra intacta


class ConfirmacaoTests(ImportacaoBase):
    def test_confirmacao_cria_so_os_novos_e_somente_nome_e_telefone(self):
        Cliente.objects.create(profissional=self.p, nome="Ana Existente", telefone="+5511988887777")
        resposta = self.enviar(
            [contato("Maria"), contato("Ana", "11988887777"), contato("Zé", "123"), contato("Bia", "(21) 98765-4321")],
            simular=False,
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        dados = resposta.json()
        self.assertFalse(dados["simulacao"])
        self.assertEqual(dados["criados"], 2)
        self.assertEqual(dados["resumo"], {"total": 4, "novos": 2, "duplicados": 1, "invalidos": 1})
        self.assertEqual(self.meus().count(), 3)
        maria = self.meus().get(telefone="+5515997669299")
        self.assertEqual(maria.nome, "Maria")
        self.assertIsNone(maria.consentimento_em)
        self.assertIsNone(maria.anonimizado_em)
        self.assertEqual(maria.observacoes, "")
        self.assertEqual(maria.profissional, self.p)

    def test_confirmar_duas_vezes_nao_duplica(self):
        self.assertEqual(self.enviar([contato()], simular=False).json()["criados"], 1)
        segunda = self.enviar([contato()], simular=False).json()
        self.assertEqual(segunda["criados"], 0)
        self.assertEqual(segunda["itens"][0]["motivo"], "ja_cadastrado")
        self.assertEqual(self.meus().count(), 1)

    def test_repetido_na_lista_cria_apenas_uma_vez(self):
        dados = self.enviar([contato("Maria"), contato("Maria 2")], simular=False).json()
        self.assertEqual(dados["criados"], 1)
        self.assertEqual(self.meus().count(), 1)

    def test_atomicidade_falha_no_meio_desfaz_tudo(self):
        real = Cliente.objects.create
        chamadas = {"n": 0}

        def falha_na_terceira(*args, **kwargs):
            chamadas["n"] += 1
            if chamadas["n"] == 3:
                raise RuntimeError("falha simulada")
            return real(*args, **kwargs)

        contatos = [contato(f"Contato {i}", f"(15) 9976{i}-0000") for i in range(5)]
        with mock.patch.object(Cliente.objects, "create", side_effect=falha_na_terceira):
            with self.assertRaises(RuntimeError):
                self.enviar(contatos, simular=False)
        self.assertEqual(self.meus().count(), 0)

    def test_corrida_integrity_error_vira_duplicado_sem_500(self):
        Cliente.objects.create(profissional=self.p, nome="Criada na corrida", telefone="+5515997669299")
        # A classificacao nao enxerga a cliente (corrida), mas o banco recusa no INSERT.
        with mock.patch(
            "apps.clientes.importacao.telefones_existentes",
            side_effect=[{}, {"+5515997669299": "Criada na corrida"}],
        ):
            resposta = self.enviar([contato("Maria"), contato("Bia", "(21) 98765-4321")], simular=False)
        self.assertEqual(resposta.status_code, 200, resposta.content)
        dados = resposta.json()
        self.assertEqual(dados["criados"], 1)
        self.assertEqual(dados["resumo"], {"total": 2, "novos": 1, "duplicados": 1, "invalidos": 0})
        self.assertEqual(dados["itens"][0]["status"], "duplicado")
        self.assertEqual(dados["itens"][0]["motivo"], "ja_cadastrado")
        self.assertEqual(dados["itens"][0]["cliente_existente"], "Criada na corrida")
        self.assertEqual(self.meus().count(), 2)  # a da corrida + a Bia

    def test_resposta_traz_so_dados_do_proprio_tenant(self):
        Cliente.objects.create(profissional=self.outra, nome="Nome Alheio", telefone="+5521987654321")
        texto = self.enviar([contato("Bia", "(21) 98765-4321")], simular=False).content.decode()
        self.assertNotIn("Alheio", texto)


class ValidacaoDoCorpoTests(ImportacaoBase):
    def assertValidacao(self, resposta, campo):
        self.assertEqual(resposta.status_code, 400, resposta.content)
        corpo = resposta.json()
        self.assertEqual(corpo["code"], "validacao")
        self.assertIn(campo, corpo["fields"])

    def test_lista_vazia_ou_formato_invalido(self):
        self.assertValidacao(self.enviar([]), "contatos")
        for ruim in (None, "texto", {"a": 1}, 7):
            with self.subTest(contatos=ruim):
                self.assertValidacao(self.enviar(ruim), "contatos")
        self.assertValidacao(self.client.post(URL, {}, format="json"), "contatos")
        self.assertValidacao(self.client.post(URL, [1, 2], format="json"), "contatos")

    def test_simular_precisa_ser_booleano(self):
        for ruim in ("false", 0, None, "sim"):
            with self.subTest(simular=ruim):
                self.assertValidacao(self.enviar([contato()], simular=ruim), "simular")
        self.assertEqual(self.meus().count(), 0)

    def test_limite_de_300_contatos(self):
        grande = [contato(f"C{i:03d}", f"(15) 99{i:03d}-{i:04d}") for i in range(301)]
        resposta = self.enviar(grande, simular=False)
        self.assertEqual(resposta.status_code, 400)
        corpo = resposta.json()
        self.assertEqual(corpo["code"], "limite_excedido")
        self.assertEqual(set(corpo), {"code", "message", "fields"})
        self.assertEqual(self.meus().count(), 0)  # nada processado
        exato = self.enviar(grande[:300])
        self.assertEqual(exato.status_code, 200)
        self.assertEqual(exato.json()["resumo"]["total"], 300)


class AcessoETenantTests(ImportacaoBase):
    def test_contas_nao_ativas_anonimo_e_sem_perfil_recebem_403(self):
        admin = admin_plataforma()
        pendente = criar_profissional("em-analise", ativa=False)
        suspensa = criar_profissional("suspensa-x")
        suspensa.mudar_status("suspensa", admin)
        rejeitada = criar_profissional("rejeitada-x", ativa=False)
        rejeitada.mudar_status("rejeitada", admin)
        sem_perfil = get_user_model().objects.create_user("sem-perfil", password="x")
        quem = {
            "pendente": cliente_logado(pendente.user),
            "suspensa": cliente_logado(suspensa.user),
            "rejeitada": cliente_logado(rejeitada.user),
            "superusuario sem perfil": cliente_logado(admin),
            "usuario sem perfil": cliente_logado(sem_perfil),
            "anonimo": APIClient(enforce_csrf_checks=True),
        }
        for nome, client in quem.items():
            with self.subTest(quem=nome):
                for simular in (True, False):
                    resposta = self.enviar([contato()], simular=simular, client=client)
                    self.assertEqual(resposta.status_code, 403, resposta.content)
        self.assertEqual(Cliente.objects.count(), 0)

    def test_csrf_e_exigido(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.p.user)
        resposta = self.enviar([contato()], simular=False, client=client)
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(self.meus().count(), 0)
        token = client.get("/api/v1/auth/csrf/").json()["csrfToken"]
        com = client.post(URL, {"contatos": [contato()], "simular": False}, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(com.status_code, 200, com.content)
        self.assertEqual(self.meus().count(), 1)

    def test_a_rota_esta_no_router_do_painel(self):
        self.assertEqual(reverse("painel-cliente-importar"), URL)

    def test_importar_para_a_nao_afeta_b(self):
        b = cliente_logado(self.outra.user)
        self.enviar([contato("Maria")], simular=False)
        self.assertEqual(Cliente.objects.filter(profissional=self.outra).count(), 0)
        dados = self.enviar([contato("Maria")], simular=False, client=b).json()
        self.assertEqual(dados["criados"], 1)  # B nao e "duplicado" por causa do A
        self.assertEqual(Cliente.objects.count(), 2)


class LimiteDeRequisicoesTests(ImportacaoBase):
    def test_limites_padrao(self):
        from config import settings as cfg

        self.assertEqual(cfg.LIMITES_PUBLICOS["importar_simular"], (30, 3600))
        self.assertEqual(cfg.LIMITES_PUBLICOS["importar_confirmar"], (10, 3600))

    def test_previa_e_confirmacao_tem_tetos_separados(self):
        limites = {**LIMITES_ALTOS, "importar_simular": (2, 3600), "importar_confirmar": (1, 3600)}
        with override_settings(LIMITES_PUBLICOS=limites):
            self.assertEqual(self.enviar([contato()]).status_code, 200)
            self.assertEqual(self.enviar([contato()]).status_code, 200)
            resposta = self.enviar([contato()])
            self.assertEqual(resposta.status_code, 429)
            self.assertEqual(resposta.json()["code"], "limite_requisicoes")
            self.assertIn("Retry-After", resposta)
            # a confirmacao tem o proprio contador
            self.assertEqual(self.enviar([contato()], simular=False).status_code, 200)
            segunda = self.enviar([contato("Bia", "(21) 98765-4321")], simular=False)
            self.assertEqual(segunda.status_code, 429)
            self.assertEqual(self.meus().count(), 1)

    def test_limite_e_por_usuario(self):
        limites = {**LIMITES_ALTOS, "importar_simular": (1, 3600)}
        with override_settings(LIMITES_PUBLICOS=limites):
            self.assertEqual(self.enviar([contato()]).status_code, 200)
            self.assertEqual(self.enviar([contato()]).status_code, 429)
            b = cliente_logado(self.outra.user)
            self.assertEqual(self.enviar([contato()], client=b).status_code, 200)

    def test_simular_nao_booleano_usa_o_teto_estrito_da_confirmacao(self):
        limites = {**LIMITES_ALTOS, "importar_simular": (100, 3600), "importar_confirmar": (1, 3600)}
        with override_settings(LIMITES_PUBLICOS=limites):
            self.assertEqual(self.enviar([contato()], simular="false").status_code, 400)
            self.assertEqual(self.enviar([contato()], simular="false").status_code, 429)
