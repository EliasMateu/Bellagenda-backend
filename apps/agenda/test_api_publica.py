"""API publica de agendamento (PostgreSQL real)."""

import hashlib
import threading
from datetime import time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.core.testutils import admin_plataforma, criar_cliente, criar_profissional, criar_servico

from .models import Agendamento, Configuracao, HorarioTrabalho
from .tokens import hash_token

SP = ZoneInfo("America/Sao_Paulo")
LIMITES_ALTOS = {chave: (10_000, 3600) for chave in settings.LIMITES_PUBLICOS}
TEL = "(11) 99876-5432"
TEL_E164 = "+5511998765432"
TEL_2 = "(11) 98765-4321"


def dia_futuro(dias=7):
    return timezone.now().astimezone(SP).date() + timedelta(days=dias)


class PublicoMixin:
    """Dados e utilitarios: profissional ativa `calita` com expediente 09-18 todos os dias."""

    def preparar(self):
        cache.clear()
        self.p = criar_profissional("calita")
        self.servico = criar_servico(self.p, "Esmaltacao", duracao_min=60)
        for dia in range(7):
            HorarioTrabalho.objects.create(
                profissional=self.p, dia_semana=dia, inicio=time(9), fim=time(18)
            )
        self.client = APIClient()
        self.base = "/api/v1/publico/calita"

    def url(self, caminho, slug="calita"):
        return f"/api/v1/publico/{slug}/{caminho}"

    def horarios(self, dias=7, servico=None, client=None, slug="calita"):
        resposta = (client or self.client).get(
            self.url("horarios/", slug),
            {"servico": (servico or self.servico).pk, "data": dia_futuro(dias).isoformat()},
        )
        self.assertEqual(resposta.status_code, 200, resposta.content)
        return resposta.json()["horarios"]

    def corpo(self, inicio, telefone=TEL, **extra):
        corpo = {
            "servico": self.servico.pk,
            "inicio": inicio,
            "nome": "Maria Souza",
            "telefone": telefone,
            "consentimento": True,
        }
        corpo.update(extra)
        return corpo

    def agendar(self, inicio, telefone=TEL, client=None, slug="calita", **extra):
        return (client or self.client).post(
            self.url("agendamentos/", slug), self.corpo(inicio, telefone, **extra), format="json"
        )

    def criar_direto(self, inicio, token="token-direto", profissional=None, **extra):
        """Agendamento criado direto no banco com token conhecido."""
        profissional = profissional or self.p
        return Agendamento.objects.create(
            profissional=profissional,
            cliente=extra.pop("cliente", None) or Cliente.objects.create(
                profissional=profissional, nome="Direta", telefone=TEL_2
            ),
            servico=extra.pop("servico", None) or criar_servico(profissional),
            inicio=inicio,
            fim=inicio + timedelta(minutes=60),
            preco_cobrado=Decimal("50"),
            token_hash=hash_token(token),
            **extra,
        )


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class ServicosHorariosTests(PublicoMixin, TestCase):
    def setUp(self):
        self.preparar()

    def test_lista_servicos_ativos(self):
        criar_servico(self.p, "Inativo").__class__.objects.filter(nome="Inativo").update(ativo=False)
        resposta = self.client.get(self.url("servicos/"))
        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()
        self.assertEqual(dados["nome_exibicao"], "calita")
        self.assertEqual([s["nome"] for s in dados["servicos"]], ["Esmaltacao"])
        self.assertEqual(
            set(dados["servicos"][0]), {"id", "nome", "duracao_min", "preco"}
        )

    def test_horarios_retorna_grade(self):
        horarios = self.horarios()
        self.assertTrue(horarios[0].endswith("-03:00"))
        self.assertIn("T09:00:00", horarios[0])
        self.assertIn("T17:00:00", horarios[-1])

    def test_horarios_parametros_invalidos(self):
        for params in ({}, {"servico": self.servico.pk}, {"servico": "x", "data": "2030-01-01"},
                       {"servico": self.servico.pk, "data": "01/02/2030"}):
            with self.subTest(params=params):
                resposta = self.client.get(self.url("horarios/"), params)
                self.assertEqual(resposta.status_code, 400)
                corpo = resposta.json()
                self.assertEqual(corpo["code"], "validacao")
                self.assertTrue(corpo["fields"])

    def test_horarios_de_servico_de_outro_tenant_ou_inativo_e_404(self):
        outra = criar_profissional("outra-manicure")
        alheio = criar_servico(outra)
        resposta = self.client.get(
            self.url("horarios/"), {"servico": alheio.pk, "data": dia_futuro().isoformat()}
        )
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(resposta.json()["code"], "nao_encontrado")

    def test_horarios_ocupado_some_da_lista(self):
        horarios = self.horarios()
        self.assertEqual(self.agendar(horarios[0]).status_code, 201)
        depois = self.horarios()
        self.assertNotIn(horarios[0], depois)
        self.assertNotIn(horarios[1], depois)  # 09:15 ainda dentro do atendimento
        self.assertEqual(depois[0], horarios[5])  # 10:15 (09:00-10:00 + 10 min de intervalo)


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class CriacaoTests(PublicoMixin, TestCase):
    def setUp(self):
        self.preparar()
        self.inicio = self.horarios()[0]

    def test_cria_pendente_e_devolve_token_e_codigo_uma_vez(self):
        resposta = self.agendar(self.inicio, observacao="Unha em gel")
        self.assertEqual(resposta.status_code, 201, resposta.content)
        dados = resposta.json()["agendamento"]
        self.assertEqual(dados["status"], "pendente")
        self.assertRegex(dados["codigo"], r"^[A-HJKMNP-Z2-9]{8}$")
        self.assertGreaterEqual(len(dados["token"]), 43)
        ag = Agendamento.objects.get()
        self.assertEqual(ag.preco_cobrado, Decimal("50.00"))
        self.assertEqual(ag.origem, "online")
        self.assertEqual(ag.observacao_cliente, "Unha em gel")
        self.assertEqual(ag.fim - ag.inicio, timedelta(minutes=60))

    def test_token_nunca_persistido_em_texto(self):
        token = self.agendar(self.inicio).json()["agendamento"]["token"]
        ag = Agendamento.objects.get()
        self.assertEqual(ag.token_hash, hashlib.sha256(token.encode()).hexdigest())
        with connection.cursor() as cursor:
            for tabela in ("agenda_agendamento", "clientes_cliente", "cache_throttle"):
                cursor.execute(f"SELECT t::text FROM {tabela} t")
                for (linha,) in cursor.fetchall():
                    self.assertNotIn(token, linha, tabela)

    def test_confirmacao_automatica(self):
        Configuracao.objects.create(profissional=self.p, confirmacao_automatica=True)
        resposta = self.agendar(self.inicio)
        self.assertEqual(resposta.json()["agendamento"]["status"], "confirmado")

    def test_cria_cliente_normalizado_com_consentimento(self):
        self.agendar(self.inicio)
        cliente = Cliente.objects.get()
        self.assertEqual(cliente.telefone, TEL_E164)
        self.assertEqual(cliente.profissional, self.p)
        self.assertIsNotNone(cliente.consentimento_em)

    def test_reaproveita_cliente_pelo_telefone_sem_trocar_o_nome(self):
        self.agendar(self.inicio)
        segundo = self.horarios()[8]
        self.agendar(segundo, telefone="11998765432", nome="Outro Nome")
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(Cliente.objects.get().nome, "Maria Souza")
        self.assertEqual(Agendamento.objects.count(), 2)

    def test_consentimento_obrigatorio(self):
        for valor in (False, None):
            with self.subTest(valor=valor):
                resposta = self.agendar(self.inicio, consentimento=valor)
                self.assertEqual(resposta.status_code, 400)
                self.assertIn("consentimento", resposta.json()["fields"])
        corpo = self.corpo(self.inicio)
        del corpo["consentimento"]
        resposta = self.client.post(self.url("agendamentos/"), corpo, format="json")
        self.assertIn("consentimento", resposta.json()["fields"])
        self.assertFalse(Agendamento.objects.exists())

    def test_telefone_invalido(self):
        for telefone in ("123", "(11) 88876-5432", "abc", ""):
            with self.subTest(telefone=telefone):
                resposta = self.agendar(self.inicio, telefone=telefone)
                self.assertEqual(resposta.status_code, 400)
                self.assertIn("telefone", resposta.json()["fields"])

    def test_campo_isca_preenchido_e_recusado(self):
        resposta = self.agendar(self.inicio, website="http://spam.example")
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Agendamento.objects.exists())
        self.assertEqual(self.agendar(self.inicio, website="").status_code, 201)

    def test_horario_nao_oferecido(self):
        base = timezone.datetime.fromisoformat(self.inicio)
        for inicio in (base + timedelta(minutes=7), base.replace(hour=3), base - timedelta(days=30),
                       base + timedelta(days=365)):
            with self.subTest(inicio=inicio):
                resposta = self.agendar(inicio.isoformat())
                self.assertEqual(resposta.status_code, 400)
                self.assertEqual(resposta.json()["code"], "horario_indisponivel")

    def test_servico_de_outro_tenant_ou_inexistente(self):
        outra = criar_profissional("outra-manicure")
        for servico in (criar_servico(outra).pk, 999999):
            with self.subTest(servico=servico):
                resposta = self.client.post(
                    self.url("agendamentos/"),
                    self.corpo(self.inicio, servico=servico),
                    format="json",
                )
                self.assertEqual(resposta.status_code, 400)
                self.assertIn("servico", resposta.json()["fields"])

    def test_nao_exige_csrf_nem_login(self):
        client = APIClient(enforce_csrf_checks=True)
        self.assertEqual(self.agendar(self.inicio, client=client).status_code, 201)

    def test_formato_padrao_de_erro(self):
        corpo = self.agendar(self.inicio, consentimento=False).json()
        self.assertEqual(set(corpo), {"code", "message", "fields"})


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class ConflitoELimiteTests(PublicoMixin, TestCase):
    def setUp(self):
        self.preparar()
        self.horas = self.horarios()

    def test_conflito_409_com_sugestoes(self):
        self.assertEqual(self.agendar(self.horas[0]).status_code, 201)
        resposta = self.agendar(self.horas[2], telefone=TEL_2)  # 09:30 sobrepoe 09:00-10:00
        self.assertEqual(resposta.status_code, 409)
        corpo = resposta.json()
        self.assertEqual(corpo["code"], "conflito_horario")
        self.assertTrue(corpo["sugestoes"])
        self.assertTrue(set(corpo["sugestoes"]) <= set(self.horarios()))
        self.assertEqual(Agendamento.objects.count(), 1)
        self.assertFalse(Cliente.objects.filter(telefone="+5511987654321").exists())

    def test_horario_colado_dentro_do_intervalo_conflita(self):
        self.agendar(self.horas[0])  # 09:00-10:00, ocupado ate 10:10
        resposta = self.agendar(self.horas[4], telefone=TEL_2)  # 10:00
        self.assertEqual(resposta.status_code, 409)
        self.assertEqual(self.agendar(self.horas[5], telefone=TEL_2).status_code, 201)  # 10:15

    def test_limite_de_dois_futuros_por_telefone(self):
        self.assertEqual(self.agendar(self.horas[0]).status_code, 201)
        self.assertEqual(self.agendar(self.horas[8]).status_code, 201)
        resposta = self.agendar(self.horas[16])
        self.assertEqual(resposta.status_code, 409)
        self.assertEqual(resposta.json()["code"], "limite_agendamentos_ativos")
        # outro telefone nao e afetado
        self.assertEqual(self.agendar(self.horas[16], telefone=TEL_2).status_code, 201)

    def test_limite_e_por_tenant(self):
        outra = criar_profissional("outra-manicure")
        servico_outra = criar_servico(outra)
        for dia in range(7):
            HorarioTrabalho.objects.create(
                profissional=outra, dia_semana=dia, inicio=time(9), fim=time(18)
            )
        self.agendar(self.horas[0])
        self.agendar(self.horas[8])
        horas_outra = self.horarios(servico=servico_outra, slug="outra-manicure")
        resposta = self.client.post(
            self.url("agendamentos/", "outra-manicure"),
            self.corpo(horas_outra[0], servico=servico_outra.pk),
            format="json",
        )
        self.assertEqual(resposta.status_code, 201)

    def test_cancelar_libera_vaga_do_limite(self):
        self.agendar(self.horas[0])
        token = self.agendar(self.horas[8]).json()["agendamento"]["token"]
        self.assertEqual(self.agendar(self.horas[16]).status_code, 409)
        self.client.post(self.url("agendamento/cancelar/"), {"token": token}, format="json")
        self.assertEqual(self.agendar(self.horas[16]).status_code, 201)


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class ConcorrenciaApiTests(PublicoMixin, TransactionTestCase):
    def test_duas_requisicoes_simultaneas_no_mesmo_horario(self):
        self.preparar()
        inicio = self.horarios()[0]
        barreira = threading.Barrier(2)
        resultados = []

        def disparar(telefone):
            try:
                client = APIClient()
                barreira.wait(timeout=10)
                resposta = self.agendar(inicio, telefone=telefone, client=client)
                resultados.append(resposta.status_code)
            except Exception as erro:  # noqa: BLE001
                resultados.append(repr(erro))
            finally:
                connection.close()

        threads = [threading.Thread(target=disparar, args=(t,)) for t in (TEL, TEL_2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(sorted(resultados, key=str), [201, 409], resultados)
        self.assertEqual(Agendamento.objects.count(), 1)


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class ConsultaCancelamentoTests(PublicoMixin, TestCase):
    def setUp(self):
        self.preparar()
        self.inicio = self.horarios()[0]
        dados = self.agendar(self.inicio).json()["agendamento"]
        self.token, self.codigo = dados["token"], dados["codigo"]

    def consultar(self, corpo, slug="calita"):
        return self.client.post(self.url("agendamento/consultar/", slug), corpo, format="json")

    def cancelar(self, corpo, slug="calita"):
        return self.client.post(self.url("agendamento/cancelar/", slug), corpo, format="json")

    def test_consultar_por_token(self):
        resposta = self.consultar({"token": self.token})
        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()
        self.assertEqual(dados["codigo"], self.codigo)
        self.assertEqual(dados["status"], "pendente")
        self.assertEqual(dados["servico"]["nome"], "Esmaltacao")
        self.assertTrue(dados["pode_cancelar"])

    def test_consultar_por_codigo_e_telefone_em_qualquer_formato(self):
        for telefone in (TEL, "11998765432", TEL_E164):
            with self.subTest(telefone=telefone):
                resposta = self.consultar({"codigo": self.codigo.lower(), "telefone": telefone})
                self.assertEqual(resposta.status_code, 200)

    def test_telefone_mascarado(self):
        texto = self.consultar({"token": self.token}).content.decode()
        self.assertNotIn("99876", texto)
        self.assertNotIn(TEL_E164, texto)
        self.assertIn("5432", texto)

    def test_profissional_telefone_e164_normalizado(self):
        self.p.telefone = "(15) 99766-9299"
        self.p.save()
        dados = self.consultar({"token": self.token}).json()
        self.assertEqual(dados["profissional_telefone"], "+5515997669299")
        self.assertEqual(dados["profissional"], self.p.nome_exibicao)
        self.assertEqual(dados["cliente"]["telefone"], "(**) *****-5432")  # cliente segue mascarada

    def test_profissional_telefone_vazio_ou_invalido_e_null(self):
        for valor in ("", "123"):
            with self.subTest(valor=valor):
                self.p.telefone = valor
                self.p.save()
                self.assertIsNone(self.consultar({"token": self.token}).json()["profissional_telefone"])

    def test_telefone_da_profissional_so_na_consulta(self):
        self.p.telefone = "+5515997669299"
        self.p.save()
        for resposta in (
            self.client.get(self.url("servicos/")),
            self.client.get(self.url("horarios/") + f"?servico={self.servico.pk}&data={dia_futuro()}"),
            self.agendar(self.horarios()[1], telefone=TEL_2),
        ):
            self.assertNotIn("5515997669299", resposta.content.decode())
            self.assertNotIn("profissional_telefone", resposta.content.decode())

    def test_telefone_da_profissional_nao_vaza_nas_falhas(self):
        self.p.telefone = "+5515997669299"
        self.p.save()
        outra = criar_profissional("outra-manicure")
        criar_servico(outra)
        for corpo, slug in (
            ({"token": "errado"}, "calita"),
            ({"token": self.token}, "outra-manicure"),
            ({"codigo": self.codigo, "telefone": TEL_2}, "calita"),
        ):
            resposta = self.consultar(corpo, slug)
            self.assertEqual(resposta.status_code, 404)
            self.assertNotIn("5515997669299", resposta.content.decode())
            self.assertNotIn("profissional_telefone", resposta.content.decode())

    def test_profissional_suspensa_ou_pendente_continua_404(self):
        self.p.telefone = "+5515997669299"
        self.p.save()
        self.p.mudar_status("suspensa", admin_plataforma())
        resposta = self.consultar({"token": self.token})
        self.assertEqual(resposta.status_code, 404)
        self.assertNotIn("5515997669299", resposta.content.decode())
        em_analise = criar_profissional("em-analise", ativa=False)
        em_analise.telefone = "+5515997669299"
        em_analise.save()
        self.assertEqual(self.consultar({"token": self.token}, "em-analise").status_code, 404)

    def test_falhas_sao_404_identicos(self):
        outra = criar_profissional("outra-manicure")
        criar_servico(outra)
        casos = [
            ({"token": "token-errado"}, "calita"),
            ({"token": self.token}, "outra-manicure"),                       # token de outro tenant
            ({"codigo": self.codigo, "telefone": TEL_2}, "calita"),           # telefone errado
            ({"codigo": "AAAAAAAA", "telefone": TEL}, "calita"),             # codigo errado
            ({"codigo": self.codigo, "telefone": "123"}, "calita"),          # telefone invalido
            ({"codigo": self.codigo, "telefone": TEL}, "outra-manicure"),    # outro tenant
        ]
        respostas = []
        for corpo, slug in casos:
            for chamada in (self.consultar, self.cancelar):
                resposta = chamada(corpo, slug)
                self.assertEqual(resposta.status_code, 404, (corpo, slug))
                respostas.append(resposta.json())
        self.assertTrue(all(r == respostas[0] for r in respostas), respostas)
        self.assertEqual(respostas[0]["code"], "nao_encontrado")

    def test_sem_identificacao_e_400(self):
        resposta = self.consultar({})
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.json()["code"], "validacao")

    def test_agendamento_encerrado_nao_e_encontrado(self):
        passado = timezone.now() - timedelta(days=2)
        self.criar_direto(passado, token="tok-passado")
        self.assertEqual(self.consultar({"token": "tok-passado"}).status_code, 404)
        self.assertEqual(self.cancelar({"token": "tok-passado"}).status_code, 404)

    def test_cancelar_dentro_do_prazo(self):
        resposta = self.cancelar({"token": self.token})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["status"], "cancelado_cliente")
        self.assertEqual(Agendamento.objects.get().status, "cancelado_cliente")
        self.assertIn(self.inicio, self.horarios())  # horario liberado

    def test_cancelar_por_codigo_e_telefone(self):
        resposta = self.cancelar({"codigo": self.codigo, "telefone": TEL})
        self.assertEqual(resposta.status_code, 200)

    def test_cancelar_duas_vezes(self):
        self.cancelar({"token": self.token})
        resposta = self.cancelar({"token": self.token})
        self.assertEqual(resposta.status_code, 409)
        self.assertEqual(resposta.json()["code"], "nao_cancelavel")

    def test_cancelar_fora_do_prazo(self):
        proximo = timezone.now() + timedelta(hours=5)
        ag = self.criar_direto(proximo, token="tok-proximo")
        resposta = self.cancelar({"token": "tok-proximo"})
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(resposta.json()["code"], "fora_do_prazo")
        ag.refresh_from_db()
        self.assertEqual(ag.status, "pendente")
        self.assertFalse(self.consultar({"token": "tok-proximo"}).json()["pode_cancelar"])

    def test_prazo_configuravel(self):
        Configuracao.objects.create(profissional=self.p, antecedencia_min_cancelamento_h=2)
        self.criar_direto(timezone.now() + timedelta(hours=5), token="tok-5h")
        self.assertEqual(self.cancelar({"token": "tok-5h"}).status_code, 200)

    def test_cancelar_token_de_outro_tenant_nao_altera_nada(self):
        criar_profissional("outra-manicure")
        self.assertEqual(self.cancelar({"token": self.token}, "outra-manicure").status_code, 404)
        self.assertEqual(Agendamento.objects.get().status, "pendente")


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class SlugNaoAtivoTests(PublicoMixin, TestCase):
    def test_404_identico_para_inexistente_pendente_suspensa_rejeitada(self):
        self.preparar()
        admin = admin_plataforma()
        criar_profissional("em-analise", ativa=False)
        suspensa = criar_profissional("suspensa-x")
        suspensa.mudar_status("suspensa", admin)
        rejeitada = criar_profissional("rejeitada-x", ativa=False)
        rejeitada.mudar_status("rejeitada", admin)
        slugs = ["nao-existe", "em-analise", "suspensa-x", "rejeitada-x", "NAO-EXISTE"]
        chamadas = [
            lambda s: self.client.get(self.url("servicos/", s)),
            lambda s: self.client.get(self.url("horarios/", s), {"servico": 1, "data": "2030-01-07"}),
            lambda s: self.client.post(self.url("agendamentos/", s), self.corpo("x"), format="json"),
            lambda s: self.client.post(self.url("agendamento/consultar/", s), {"token": "t"}, format="json"),
            lambda s: self.client.post(self.url("agendamento/cancelar/", s), {"token": "t"}, format="json"),
        ]
        for chamada in chamadas:
            respostas = [chamada(slug) for slug in slugs]
            for resposta in respostas:
                self.assertEqual(resposta.status_code, 404)
                self.assertEqual(resposta.content, respostas[0].content)
                self.assertEqual(resposta.json()["code"], "nao_encontrado")

    def test_slug_ativa_e_case_insensitive(self):
        self.preparar()
        self.assertEqual(self.client.get(self.url("servicos/", "CALITA")).status_code, 200)


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class VazamentoEntreTenantsTests(PublicoMixin, TestCase):
    def test_respostas_de_a_nao_contem_dados_de_b(self):
        self.preparar()
        b = criar_profissional("segredo-do-b")
        b.nome_exibicao = "Beatriz Confidencial"
        b.save()
        servico_b = criar_servico(b, "Servico Secreto B")
        cliente_b = criar_cliente(b, "11977776666")
        for dia in range(7):
            HorarioTrabalho.objects.create(profissional=b, dia_semana=dia, inicio=time(9), fim=time(18))
        ag_b = self.criar_direto(
            timezone.now() + timedelta(days=3), token="tok-b", profissional=b,
            cliente=cliente_b, servico=servico_b,
        )
        # A cria e consulta o proprio
        horas = self.horarios()
        a = self.agendar(horas[0]).json()["agendamento"]
        textos = [
            self.client.get(self.url("servicos/")).content,
            self.client.get(self.url("horarios/"), {"servico": self.servico.pk, "data": dia_futuro().isoformat()}).content,
            self.agendar(horas[0], telefone=TEL_2).content,
            self.client.post(self.url("agendamento/consultar/"), {"token": a["token"]}, format="json").content,
            self.client.post(self.url("agendamento/consultar/"), {"token": "tok-b"}, format="json").content,
            self.client.post(self.url("agendamento/cancelar/"), {"token": "tok-b"}, format="json").content,
        ]
        proibidos = ["Beatriz", "Confidencial", "Secreto", "segredo-do-b", "77776666", "11977776666",
                     ag_b.codigo_curto, "tok-b"]
        for texto in textos:
            texto = texto.decode()
            for proibido in proibidos:
                self.assertNotIn(proibido, texto)
        ag_b.refresh_from_db()
        self.assertEqual(ag_b.status, "pendente")


class LimiteDeRequisicoesTests(PublicoMixin, TestCase):
    def setUp(self):
        self.preparar()
        self.horas = self.horarios()

    def _limites(self, **alterados):
        return override_settings(LIMITES_PUBLICOS={**LIMITES_ALTOS, **alterados})

    def test_por_ip(self):
        with self._limites(ip_post=(2, 3600)):
            self.assertEqual(self.agendar(self.horas[0]).status_code, 201)
            self.assertEqual(self.agendar(self.horas[8], telefone=TEL_2).status_code, 201)
            resposta = self.agendar(self.horas[16], telefone="11 97777-1111")
            self.assertEqual(resposta.status_code, 429)
            self.assertEqual(resposta.json()["code"], "limite_requisicoes")
            self.assertEqual(set(resposta.json()), {"code", "message", "fields"})
            self.assertIn("Retry-After", resposta)

    def test_ip_diferente_nao_compartilha_contador(self):
        with self._limites(ip_post=(1, 3600)):
            self.assertEqual(self.agendar(self.horas[0]).status_code, 201)
            self.assertEqual(self.agendar(self.horas[8], telefone=TEL_2).status_code, 429)
            outro = APIClient(REMOTE_ADDR="10.9.9.9")
            self.assertEqual(self.agendar(self.horas[8], telefone=TEL_2, client=outro).status_code, 201)

    def test_por_telefone(self):
        with self._limites(telefone=(1, 3600)):
            self.assertEqual(self.agendar(self.horas[0]).status_code, 201)
            self.assertEqual(self.agendar(self.horas[8]).status_code, 429)
            self.assertEqual(self.agendar(self.horas[8], telefone=TEL_2).status_code, 201)

    def test_por_tenant(self):
        with self._limites(tenant=(2, 3600)):
            self.assertEqual(self.agendar(self.horas[0]).status_code, 201)
            self.assertEqual(self.agendar(self.horas[8], telefone=TEL_2).status_code, 201)
            self.assertEqual(self.agendar(self.horas[16], telefone="11 97777-1111").status_code, 429)

    def _post(self, acao, corpo, client=None):
        return (client or self.client).post(self.url(f"agendamento/{acao}/"), corpo, format="json")

    def _limites_reais(self, **alterados):
        from config import settings as cfg

        return override_settings(LIMITES_PUBLICOS={**cfg.LIMITES_PUBLICOS, **alterados})

    def test_codigo_e_telefone_continua_5_por_hora_por_ip_e_slug(self):
        corpo = {"codigo": "AAAAAAAA", "telefone": TEL}
        with self._limites_reais():
            for _ in range(5):
                self.assertEqual(self._post("consultar", corpo).status_code, 404)
            resposta = self._post("cancelar", corpo)
            self.assertEqual(resposta.status_code, 429)
            self.assertEqual(resposta.json()["code"], "limite_requisicoes")
            outro = APIClient(REMOTE_ADDR="10.9.9.9")
            self.assertEqual(self._post("consultar", corpo, outro).status_code, 404)

    def test_token_valido_consulta_20_vezes_e_cancela_depois(self):
        token = self.agendar(self.horas[0]).json()["agendamento"]["token"]
        with self._limites_reais():
            for _ in range(20):
                self.assertEqual(self._post("consultar", {"token": token}).status_code, 200)
            self.assertEqual(self._post("cancelar", {"token": token}).status_code, 200)

    def test_token_invalido_so_e_limitado_apos_o_novo_limite(self):
        with self._limites_reais(consulta_token=(10, 3600)):
            for _ in range(10):
                self.assertEqual(self._post("consultar", {"token": "x"}).status_code, 404)
            self.assertEqual(self._post("consultar", {"token": "x"}).status_code, 429)
            self.assertEqual(self._post("cancelar", {"token": "x"}).status_code, 429)

    def test_token_e_codigo_juntos_contam_como_token(self):
        corpo = {"token": "x", "codigo": "AAAAAAAA", "telefone": TEL}
        with self._limites_reais():
            for _ in range(6):
                self.assertEqual(self._post("consultar", corpo).status_code, 404)

    def test_token_nao_string_cai_no_limite_estrito(self):
        with self._limites_reais():
            for _ in range(5):
                self._post("consultar", {"token": 123, "codigo": "AAAAAAAA", "telefone": TEL})
            self.assertEqual(
                self._post("consultar", {"token": 123, "codigo": "AAAAAAAA", "telefone": TEL}).status_code, 429
            )

    def test_tenant_consulta_por_token_nao_consome_a_criacao(self):
        with self._limites_reais(tenant=(2, 3600), tenant_consulta_token=(3, 3600), consulta_token=(100, 3600)):
            for _ in range(3):
                self.assertEqual(self._post("consultar", {"token": "x"}).status_code, 404)
            self.assertEqual(self._post("consultar", {"token": "x"}).status_code, 429)
            self.assertEqual(self.agendar(self.horas[0]).status_code, 201)  # criacao segue com o proprio teto

    def test_gets_nao_sao_limitados(self):
        with self._limites(ip_post=(1, 3600), tenant=(1, 3600)):
            for _ in range(5):
                self.assertEqual(self.client.get(self.url("servicos/")).status_code, 200)

    def test_contador_nao_guarda_ip_nem_telefone_em_texto(self):
        with self._limites(telefone=(5, 3600)):
            self.agendar(self.horas[0])
        with connection.cursor() as cursor:
            cursor.execute("SELECT cache_key, value FROM cache_throttle")
            linhas = " ".join(f"{k} {v}" for k, v in cursor.fetchall())
        self.assertNotIn("127.0.0.1", linhas)
        self.assertNotIn("99876", linhas)
