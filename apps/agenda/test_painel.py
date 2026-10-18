"""API do painel: recursos, regras de agendamento e matriz de transicoes (PostgreSQL real)."""

from datetime import datetime, time, timedelta

from django.test import TestCase

from apps.clientes.models import Cliente
from apps.core.testutils import (
    SP,
    cliente_logado,
    configurar_expediente,
    criar_agendamento,
    criar_cliente,
    criar_profissional,
    criar_servico,
    dia_futuro,
)
from apps.servicos.models import Servico

from .models import Agendamento, Bloqueio, Configuracao, HorarioTrabalho

P = "/api/v1/painel"


def local(dia, h, m=0):
    return datetime.combine(dia, time(h, m), tzinfo=SP)


class PainelBase(TestCase):
    def setUp(self):
        self.a = criar_profissional("calita")
        self.b = criar_profissional("outra-manicure")
        configurar_expediente(self.a)
        self.servico = criar_servico(self.a, "Esmaltacao", duracao_min=60)
        self.c = cliente_logado(self.a.user)
        self.dia = dia_futuro(7)

    def post(self, caminho, corpo=None, client=None):
        return (client or self.c).post(f"{P}/{caminho}", corpo or {}, format="json")

    def agendar(self, h=10, m=0, **extra):
        corpo = {
            "servico": self.servico.pk,
            "inicio": local(self.dia, h, m).isoformat(),
            "cliente_nome": "Maria Souza",
            "cliente_telefone": "(11) 99876-5432",
        }
        corpo.update(extra)
        corpo = {k: v for k, v in corpo.items() if v is not None}
        return self.post("agendamentos/", corpo)


class ServicosPainelTests(PainelBase):
    def test_crud(self):
        r = self.post("servicos/", {"nome": "Gel", "duracao_min": 90, "preco": "80.00"})
        self.assertEqual(r.status_code, 201, r.content)
        pk = r.json()["id"]
        self.assertEqual(Servico.objects.get(pk=pk).profissional, self.a)
        self.assertEqual(self.c.patch(f"{P}/servicos/{pk}/", {"preco": "90.00"}, format="json").status_code, 200)
        self.assertEqual(self.c.get(f"{P}/servicos/{pk}/").json()["preco"], "90.00")
        self.assertEqual(self.c.delete(f"{P}/servicos/{pk}/").status_code, 204)

    def test_nome_duplicado_no_tenant_e_400_amigavel(self):
        r = self.post("servicos/", {"nome": "Esmaltacao", "duracao_min": 30, "preco": "10"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("nome", r.json()["fields"])
        # outro tenant pode usar o mesmo nome
        criar_servico(self.b, "Gel")
        self.assertEqual(self.post("servicos/", {"nome": "Gel", "duracao_min": 30, "preco": "10"}).status_code, 201)

    def test_editar_sem_mudar_nome_nao_acusa_duplicado(self):
        r = self.c.patch(f"{P}/servicos/{self.servico.pk}/", {"nome": "Esmaltacao", "ativo": False}, format="json")
        self.assertEqual(r.status_code, 200)
        self.servico.refresh_from_db()
        self.assertFalse(self.servico.ativo)

    def test_valores_invalidos(self):
        for corpo in ({"nome": "X", "duracao_min": 0, "preco": "10"},
                      {"nome": "X", "duracao_min": 10, "preco": "-1"}):
            self.assertEqual(self.post("servicos/", corpo).status_code, 400)

    def test_apagar_servico_com_agendamento_e_409(self):
        criar_agendamento(self.a, servico=self.servico)
        r = self.c.delete(f"{P}/servicos/{self.servico.pk}/")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "em_uso")
        self.assertTrue(Servico.objects.filter(pk=self.servico.pk).exists())


class ClientesPainelTests(PainelBase):
    def test_criar_normaliza_e_valida(self):
        r = self.post("clientes/", {"nome": "Ana", "telefone": "(11) 99876-5432"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["telefone"], "+5511998765432")
        self.assertIn("telefone", self.post("clientes/", {"nome": "X", "telefone": "123"}).json()["fields"])

    def test_telefone_duplicado_no_tenant(self):
        self.post("clientes/", {"nome": "Ana", "telefone": "11998765432"})
        r = self.post("clientes/", {"nome": "Ana 2", "telefone": "(11) 99876-5432"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("telefone", r.json()["fields"])
        Cliente.objects.create(profissional=self.b, nome="Y", telefone="11977776666")
        self.assertEqual(self.post("clientes/", {"nome": "Z", "telefone": "11977776666"}).status_code, 201)

    def test_busca_por_nome_e_telefone(self):
        Cliente.objects.create(profissional=self.a, nome="Beatriz Lima", telefone="11911112222")
        Cliente.objects.create(profissional=self.a, nome="Carla Dias", telefone="11933334444")
        por_nome = self.c.get(f"{P}/clientes/", {"busca": "beat"}).json()
        self.assertEqual([c["nome"] for c in por_nome], ["Beatriz Lima"])
        por_tel = self.c.get(f"{P}/clientes/", {"busca": "(11) 93333"}).json()
        self.assertEqual([c["nome"] for c in por_tel], ["Carla Dias"])

    def test_editar_e_nao_apagar(self):
        cliente = criar_cliente(self.a)
        r = self.c.patch(f"{P}/clientes/{cliente.pk}/", {"observacoes": "alergia a acetona"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.delete(f"{P}/clientes/{cliente.pk}/").status_code, 405)
        self.assertTrue(Cliente.objects.filter(pk=cliente.pk).exists())

    def test_consentimento_nao_e_editavel(self):
        cliente = criar_cliente(self.a)
        self.c.patch(f"{P}/clientes/{cliente.pk}/", {"consentimento_em": "2030-01-01T00:00:00Z"}, format="json")
        cliente.refresh_from_db()
        self.assertIsNone(cliente.consentimento_em)


class HorariosBloqueiosConfigTests(PainelBase):
    def setUp(self):
        super().setUp()
        HorarioTrabalho.objects.filter(profissional=self.a).delete()

    def horario(self, dia, ini, fim, **kw):
        return self.post("horarios-trabalho/", {"dia_semana": dia, "inicio": ini, "fim": fim})

    def test_horario_crud_e_validacoes(self):
        r = self.horario(1, "09:00", "12:00")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.horario(1, "12:00", "18:00").status_code, 201)  # colado, sem sobrepor
        self.assertEqual(self.horario(2, "09:00", "12:00").status_code, 201)  # outro dia
        self.assertEqual(self.horario(1, "18:00", "09:00").status_code, 400)  # fim antes do inicio
        self.assertEqual(self.horario(1, "09:00", "09:00").status_code, 400)
        self.assertEqual(self.horario(7, "09:00", "10:00").status_code, 400)  # dia invalido

    def test_horario_sobreposto_no_mesmo_dia(self):
        self.horario(1, "09:00", "12:00")
        for ini, fim in (("11:00", "13:00"), ("08:00", "09:30"), ("09:00", "12:00"), ("10:00", "11:00")):
            r = self.horario(1, ini, fim)
            self.assertEqual(r.status_code, 400, (ini, fim))
            self.assertIn("inicio", r.json()["fields"])

    def test_editar_horario_nao_conflita_consigo_mesmo(self):
        pk = self.horario(1, "09:00", "12:00").json()["id"]
        r = self.c.patch(f"{P}/horarios-trabalho/{pk}/", {"fim": "13:00"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        outro = self.horario(1, "14:00", "16:00").json()["id"]
        r = self.c.patch(f"{P}/horarios-trabalho/{outro}/", {"inicio": "12:30"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.c.delete(f"{P}/horarios-trabalho/{pk}/").status_code, 204)

    def test_bloqueio_crud(self):
        corpo = {"inicio": local(self.dia, 10).isoformat(), "fim": local(self.dia, 12).isoformat(), "motivo": "Medico"}
        r = self.post("bloqueios/", corpo)
        self.assertEqual(r.status_code, 201, r.content)
        pk = r.json()["id"]
        self.assertEqual(Bloqueio.objects.get(pk=pk).profissional, self.a)
        invalido = {**corpo, "fim": local(self.dia, 9).isoformat()}
        self.assertEqual(self.post("bloqueios/", invalido).status_code, 400)
        self.assertEqual(self.c.patch(f"{P}/bloqueios/{pk}/", {"fim": local(self.dia, 9).isoformat()}, format="json").status_code, 400)
        self.assertEqual(self.c.delete(f"{P}/bloqueios/{pk}/").status_code, 204)

    def test_configuracao_criada_com_padroes_no_primeiro_acesso(self):
        self.assertFalse(Configuracao.objects.filter(profissional=self.a).exists())
        r = self.c.get(f"{P}/configuracao/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["antecedencia_min_cancelamento_h"], 24)
        self.assertEqual(r.json()["intervalo_entre_atendimentos_min"], 10)
        self.c.get(f"{P}/configuracao/")
        self.assertEqual(Configuracao.objects.filter(profissional=self.a).count(), 1)

    def test_configuracao_patch(self):
        r = self.c.patch(f"{P}/configuracao/", {"confirmacao_automatica": True, "intervalo_entre_atendimentos_min": 20}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        cfg = Configuracao.objects.get(profissional=self.a)
        self.assertTrue(cfg.confirmacao_automatica)
        self.assertEqual(cfg.intervalo_entre_atendimentos_min, 20)
        for corpo in ({"timezone": "Marte/Olympus"}, {"janela_max_dias": 0}):
            self.assertEqual(self.c.patch(f"{P}/configuracao/", corpo, format="json").status_code, 400)

    def test_configuracao_nao_aceita_profissional(self):
        self.c.patch(f"{P}/configuracao/", {"profissional": self.b.pk, "janela_max_dias": 30}, format="json")
        self.assertFalse(Configuracao.objects.filter(profissional=self.b).exists())
        self.assertEqual(Configuracao.objects.get(profissional=self.a).janela_max_dias, 30)


class AgendamentoCriarPainelTests(PainelBase):
    def test_cria_no_balcao_com_cliente_novo(self):
        r = self.agendar()
        self.assertEqual(r.status_code, 201, r.content)
        dados = r.json()
        self.assertEqual(dados["status"], "confirmado")
        self.assertEqual(dados["origem"], "balcao")
        self.assertEqual(dados["cliente"]["telefone"], "+5511998765432")
        self.assertNotIn("token", r.content.decode())
        self.assertEqual(Cliente.objects.get().profissional, self.a)
        ag = Agendamento.objects.get()
        self.assertEqual(ag.preco_cobrado, self.servico.preco)
        self.assertEqual(len(ag.token_hash), 64)

    def test_cliente_existente_por_id_e_por_telefone(self):
        cliente = criar_cliente(self.a, "11998765432")
        self.assertEqual(self.agendar(cliente=cliente.pk, cliente_nome=None, cliente_telefone=None).status_code, 201)
        self.assertEqual(self.agendar(h=12).status_code, 201)  # mesmo telefone => reaproveita
        self.assertEqual(Cliente.objects.count(), 1)

    def test_cliente_de_outro_tenant_recusado(self):
        alheio = criar_cliente(self.b)
        r = self.agendar(cliente=alheio.pk, cliente_nome=None, cliente_telefone=None)
        self.assertEqual(r.status_code, 400)
        self.assertIn("cliente", r.json()["fields"])

    def test_servico_de_outro_tenant_ou_inativo_recusado(self):
        r = self.agendar(servico=criar_servico(self.b).pk)
        self.assertEqual(r.status_code, 400)
        self.assertIn("servico", r.json()["fields"])
        Servico.objects.filter(pk=self.servico.pk).update(ativo=False)
        self.assertIn("servico", self.agendar().json()["fields"])

    def test_exige_cliente(self):
        r = self.agendar(cliente_nome=None, cliente_telefone=None)
        self.assertEqual(r.status_code, 400)
        self.assertIn("cliente", r.json()["fields"])
        self.assertIn("cliente_telefone", self.agendar(cliente_telefone="123").json()["fields"])

    def test_fora_da_grade_exige_override(self):
        r = self.agendar(h=3)
        self.assertEqual(r.status_code, 400)
        self.assertIn("inicio", r.json()["fields"])
        self.assertEqual(self.agendar(h=3, override_grade=True).status_code, 201)

    def test_horario_que_nao_e_multiplo_de_15_e_fora_da_grade(self):
        self.assertEqual(self.agendar(h=10, m=7).status_code, 400)

    def test_bloqueio_exige_override(self):
        Bloqueio.objects.create(profissional=self.a, inicio=local(self.dia, 10), fim=local(self.dia, 12))
        self.assertEqual(self.agendar(h=10).status_code, 400)
        self.assertEqual(self.agendar(h=10, override_grade=True).status_code, 201)

    def test_aceita_data_passada_e_sem_antecedencia(self):
        self.dia = dia_futuro(-3)
        self.assertEqual(self.agendar().status_code, 201)

    def test_conflito_409_mesmo_com_override(self):
        self.assertEqual(self.agendar().status_code, 201)
        for extra in ({}, {"override_grade": True}):
            r = self.agendar(h=10, m=30, cliente_telefone="(11) 98765-4321", **extra)
            self.assertEqual(r.status_code, 409, r.content)
            self.assertEqual(r.json()["code"], "conflito_horario")
        self.assertEqual(Agendamento.objects.count(), 1)

    def test_campos_sensiveis_na_criacao_sao_ignorados(self):
        r = self.agendar(status="concluido", preco_cobrado="1.00", token_hash="x", origem="online", codigo_curto="AAAAAAAA")
        self.assertEqual(r.status_code, 201)
        ag = Agendamento.objects.get()
        self.assertEqual((ag.status, ag.origem), ("confirmado", "balcao"))
        self.assertEqual(ag.preco_cobrado, self.servico.preco)
        self.assertNotEqual(ag.codigo_curto, "AAAAAAAA")


class AgendamentoListarEditarTests(PainelBase):
    def test_filtros_de_ate_status_no_fuso_da_profissional(self):
        def crie(h, m=0, dia=None, **extra):
            inicio = local(dia or self.dia, h, m)
            return Agendamento.objects.create(
                profissional=self.a, cliente=criar_cliente(self.a), servico=self.servico,
                inicio=inicio, fim=inicio + timedelta(minutes=30), preco_cobrado=10,
                token_hash=f"{h}{m}{(dia or self.dia).day}".ljust(64, "0"), **extra,
            )

        antes = crie(23, 30, dia=self.dia - timedelta(days=1))
        manha = crie(0, 30)            # 00:30 local do dia
        noite = crie(23, 30, status="confirmado")  # 23:30 local do dia (ja e o dia seguinte em UTC)
        depois = crie(0, 30, dia=self.dia + timedelta(days=1))
        d = self.dia.isoformat()

        ids = lambda r: {a["id"] for a in r.json()}  # noqa: E731
        r = self.c.get(f"{P}/agendamentos/", {"de": d, "ate": d})
        self.assertEqual(ids(r), {manha.pk, noite.pk})
        self.assertEqual(ids(self.c.get(f"{P}/agendamentos/")), {antes.pk, manha.pk, noite.pk, depois.pk})
        self.assertEqual(ids(self.c.get(f"{P}/agendamentos/", {"de": d})), {manha.pk, noite.pk, depois.pk})
        self.assertEqual(ids(self.c.get(f"{P}/agendamentos/", {"ate": d})), {antes.pk, manha.pk, noite.pk})
        self.assertEqual(ids(self.c.get(f"{P}/agendamentos/", {"status": "confirmado"})), {noite.pk})
        self.assertEqual(ids(self.c.get(f"{P}/agendamentos/", {"status": "confirmado,pendente", "de": d, "ate": d})), {manha.pk, noite.pk})

    def test_filtros_invalidos(self):
        for params in ({"de": "07/01/2030"}, {"ate": "x"}, {"status": "inexistente"}):
            r = self.c.get(f"{P}/agendamentos/", params)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.json()["code"], "validacao")

    def test_detalhe_nao_expoe_token(self):
        ag = criar_agendamento(self.a, servico=self.servico)
        texto = self.c.get(f"{P}/agendamentos/{ag.pk}/").content.decode()
        self.assertNotIn(ag.token_hash, texto)
        self.assertNotIn("token", texto)
        self.assertIn(ag.codigo_curto, texto)

    def test_patch_so_observacao(self):
        ag = criar_agendamento(self.a, servico=self.servico)
        r = self.c.patch(f"{P}/agendamentos/{ag.pk}/", {"observacao_cliente": "trazer foto"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["observacao_cliente"], "trazer foto")

    def test_patch_de_campos_protegidos_e_recusado(self):
        ag = criar_agendamento(self.a, servico=self.servico)
        original = Agendamento.objects.values().get(pk=ag.pk)
        for campo, valor in (("status", "concluido"), ("token_hash", "c" * 64), ("codigo_curto", "ZZZZZZZZ"),
                             ("preco_cobrado", "1.00"), ("cliente", criar_cliente(self.a).pk),
                             ("servico", criar_servico(self.a).pk), ("profissional", self.b.pk),
                             ("inicio", "2031-01-01T10:00:00Z")):
            with self.subTest(campo=campo):
                r = self.c.patch(f"{P}/agendamentos/{ag.pk}/", {campo: valor}, format="json")
                self.assertEqual(r.status_code, 400)
                self.assertIn(campo, r.json()["fields"])
        self.assertEqual(Agendamento.objects.values().get(pk=ag.pk), original)

    def test_put_e_delete_nao_existem(self):
        ag = criar_agendamento(self.a, servico=self.servico)
        self.assertEqual(self.c.put(f"{P}/agendamentos/{ag.pk}/", {}, format="json").status_code, 405)
        self.assertEqual(self.c.delete(f"{P}/agendamentos/{ag.pk}/").status_code, 405)
        self.assertTrue(Agendamento.objects.filter(pk=ag.pk).exists())


class MatrizTransicoesTests(PainelBase):
    # (status inicial, acao, corpo) -> status final, ou None se deve ser 409 sem mudar
    CASOS = [
        ("pendente", "confirmar", {}, "confirmado"),
        ("pendente", "recusar", {}, "cancelado_salao"),
        ("pendente", "cancelar", {}, "cancelado_salao"),
        ("pendente", "cancelar", {"origem": "cliente"}, "cancelado_cliente"),
        ("pendente", "concluir", {}, None),
        ("pendente", "faltou", {}, None),
        ("confirmado", "confirmar", {}, None),
        ("confirmado", "recusar", {}, None),
        ("confirmado", "cancelar", {}, "cancelado_salao"),
        ("confirmado", "cancelar", {"origem": "cliente"}, "cancelado_cliente"),
        ("confirmado", "concluir", {}, "concluido"),
        ("confirmado", "faltou", {}, "falta"),
    ] + [
        (final, acao, {}, None)
        for final in ("cancelado_salao", "cancelado_cliente", "concluido", "falta")
        for acao in ("confirmar", "recusar", "cancelar", "concluir", "faltou")
    ]

    def test_matriz(self):
        for inicial, acao, corpo, final in self.CASOS:
            with self.subTest(inicial=inicial, acao=acao, corpo=corpo):
                ag = criar_agendamento(self.a, inicio_min=0, servico=self.servico)
                Agendamento.objects.filter(pk=ag.pk).update(status=inicial)
                r = self.post(f"agendamentos/{ag.pk}/{acao}/", corpo)
                ag.refresh_from_db()
                if final:
                    self.assertEqual(r.status_code, 200, r.content)
                    self.assertEqual(r.json()["status"], final)
                    self.assertEqual(ag.status, final)
                else:
                    self.assertEqual(r.status_code, 409, r.content)
                    self.assertEqual(r.json()["code"], "transicao_invalida")
                    self.assertEqual(ag.status, inicial)
                ag.delete()

    def test_cancelar_libera_o_horario(self):
        self.assertEqual(self.agendar().status_code, 201)
        pk = Agendamento.objects.get().pk
        self.assertEqual(self.agendar(h=10, cliente_telefone="(11) 98765-4321").status_code, 409)
        self.post(f"agendamentos/{pk}/cancelar/")
        self.assertEqual(self.agendar(h=10, cliente_telefone="(11) 98765-4321").status_code, 201)


class RemarcarTests(PainelBase):
    def setUp(self):
        super().setUp()
        self.ag = Agendamento.objects.get(pk=self.agendar(h=10).json()["id"])

    def remarcar(self, h, m=0, **extra):
        return self.post(
            f"agendamentos/{self.ag.pk}/remarcar/",
            {"inicio": local(self.dia, h, m).isoformat(), **extra},
        )

    def test_remarca_mantendo_duracao(self):
        r = self.remarcar(14)
        self.assertEqual(r.status_code, 200, r.content)
        self.ag.refresh_from_db()
        self.assertEqual(self.ag.inicio, local(self.dia, 14))
        self.assertEqual(self.ag.fim - self.ag.inicio, timedelta(minutes=60))
        self.assertEqual(self.ag.fim_ocupado - self.ag.fim, timedelta(minutes=10))

    def test_remarcar_sobrepondo_o_proprio_horario_antigo(self):
        self.assertEqual(self.remarcar(10, 15).status_code, 200)

    def test_conflito_409_e_nada_muda(self):
        outro = criar_agendamento(self.a, servico=self.servico)  # BASE (2030): outra data
        outro_inicio = local(self.dia, 14)
        Agendamento.objects.filter(pk=outro.pk).update(
            inicio=outro_inicio, fim=outro_inicio + timedelta(minutes=60),
            fim_ocupado=outro_inicio + timedelta(minutes=70),
        )
        r = self.remarcar(14, 30)
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()["code"], "conflito_horario")
        self.ag.refresh_from_db()
        self.assertEqual(self.ag.inicio, local(self.dia, 10))

    def test_fora_da_grade_e_override(self):
        self.assertEqual(self.remarcar(3).status_code, 400)
        self.assertEqual(self.remarcar(3, override_grade=True).status_code, 200)

    def test_estado_final_nao_remarca(self):
        Agendamento.objects.filter(pk=self.ag.pk).update(status="concluido")
        r = self.remarcar(14)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "transicao_invalida")

    def test_corpo_invalido(self):
        self.assertEqual(self.post(f"agendamentos/{self.ag.pk}/remarcar/", {}).status_code, 400)
