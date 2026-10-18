"""TEN-01 completo para o painel: enumera as rotas registradas e prova o isolamento entre tenants."""

from datetime import time

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.agenda.models import Agendamento, Bloqueio, Configuracao, HorarioTrabalho
from apps.clientes.models import Cliente
from apps.servicos.models import Servico
from config.painel_urls import router, urlpatterns

from .testutils import (
    BASE,
    LIMITES_ALTOS,
    admin_plataforma,
    cliente_logado,
    criar_agendamento,
    criar_cliente,
    criar_profissional,
    criar_servico,
)

P = "/api/v1/painel"

# Uma fabrica de objeto do tenant A por rota registrada. Rota nova SEM entrada aqui reprova o teste.
OBJETOS_DE_A = {
    "painel-servico": lambda p: criar_servico(p, "Servico do A"),
    "painel-cliente": lambda p: criar_cliente(p),
    "painel-horario": lambda p: HorarioTrabalho.objects.create(
        profissional=p, dia_semana=2, inicio=time(9), fim=time(12)),
    "painel-bloqueio": lambda p: Bloqueio.objects.create(
        profissional=p, inicio=BASE, fim=BASE.replace(hour=12)),
    "painel-agendamento": lambda p: criar_agendamento(p),
}
MODELOS = {
    "painel-servico": Servico, "painel-cliente": Cliente, "painel-horario": HorarioTrabalho,
    "painel-bloqueio": Bloqueio, "painel-agendamento": Agendamento,
}


def snapshot(model):
    return list(model.objects.order_by("pk").values())


def rotas_do_router():
    """(nome, classe da view, {metodo: acao}, tem_pk) de cada rota gerada pelo router."""
    return [
        (p.name, p.callback.cls, p.callback.actions, "pk" in p.pattern.regex.groupindex)
        for p in router.urls
    ]


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class Ten01PainelTests(TestCase):
    def setUp(self):
        self.a = criar_profissional("calita")
        self.b = criar_profissional("outra-manicure")
        self.client_b = cliente_logado(self.b.user)
        self.objetos = {nome: fabrica(self.a) for nome, fabrica in OBJETOS_DE_A.items()}

    def test_toda_rota_registrada_tem_cobertura(self):
        registradas = {basename for _, _, basename in router.registry}
        self.assertEqual(registradas, set(OBJETOS_DE_A), "rota nova sem teste de vazamento")
        self.assertEqual(registradas, set(MODELOS))
        nomes_extras = {p.name for p in urlpatterns} - {n for n, *_ in rotas_do_router()}
        self.assertEqual(nomes_extras, {"painel-configuracao"})  # singleton, testado abaixo

    def test_toda_rota_tem_viewset_do_tenant(self):
        from .tenancy import TenantModelViewSet

        for nome, view, _, _ in rotas_do_router():
            with self.subTest(rota=nome):
                self.assertTrue(issubclass(view, TenantModelViewSet))

    def test_b_nunca_lista_objetos_de_a(self):
        for nome, view, actions, tem_pk in rotas_do_router():
            if tem_pk or "get" not in actions:
                continue
            with self.subTest(rota=nome):
                resposta = self.client_b.get(reverse(nome))
                self.assertEqual(resposta.status_code, 200)
                self.assertEqual(resposta.json(), [])

    def test_b_nao_le_edita_apaga_nem_aciona_acoes_em_objetos_de_a(self):
        antes = {nome: snapshot(modelo) for nome, modelo in MODELOS.items()}
        verificadas = 0
        for nome, view, actions, tem_pk in rotas_do_router():
            if not tem_pk:
                continue
            base = nome.rsplit("-", 1)[0] if nome.endswith(("-detail",)) else None
            prefixo = next(b for b in OBJETOS_DE_A if nome.startswith(b))
            alvo = self.objetos[prefixo]
            url = reverse(nome, kwargs={"pk": alvo.pk})
            for metodo in actions:
                with self.subTest(rota=nome, metodo=metodo):
                    esperado = 404 if metodo in view.http_method_names else 405
                    resposta = getattr(self.client_b, metodo)(url, {}, format="json")
                    self.assertEqual(resposta.status_code, esperado, resposta.content)
                    if esperado == 404:
                        if metodo != "head":  # HEAD nao tem corpo
                            self.assertEqual(resposta.json()["code"], "nao_encontrado")
                        verificadas += 1
            del base
        self.assertGreater(verificadas, 15)
        for nome, modelo in MODELOS.items():
            self.assertEqual(snapshot(modelo), antes[nome], f"{nome} foi alterado por B")

    def test_pk_inexistente_e_pk_de_outro_tenant_dao_a_mesma_resposta(self):
        for nome, *_ in rotas_do_router():
            if not nome.endswith("-detail"):
                continue
            prefixo = next(b for b in OBJETOS_DE_A if nome.startswith(b))
            real = self.client_b.get(reverse(nome, kwargs={"pk": self.objetos[prefixo].pk}))
            fantasma = self.client_b.get(reverse(nome, kwargs={"pk": 9_999_999}))
            self.assertEqual((real.status_code, real.content), (fantasma.status_code, fantasma.content))

    def test_criar_em_nome_de_a_nao_funciona(self):
        servico = self.client_b.post(
            f"{P}/servicos/",
            {"nome": "Novo", "duracao_min": 30, "preco": "10", "profissional": self.a.pk},
            format="json",
        )
        self.assertEqual(servico.status_code, 201)
        self.assertEqual(Servico.objects.get(pk=servico.json()["id"]).profissional, self.b)
        cliente = self.client_b.post(
            f"{P}/clientes/", {"nome": "X", "telefone": "11955554444", "profissional": self.a.pk}, format="json"
        )
        self.assertEqual(Cliente.objects.get(pk=cliente.json()["id"]).profissional, self.b)

    def test_agendamento_do_b_nao_aceita_cliente_nem_servico_do_a(self):
        corpo = {
            "servico": criar_servico(self.b).pk,
            "inicio": BASE.isoformat(),
            "cliente": self.objetos["painel-cliente"].pk,
            "override_grade": True,
        }
        r = self.client_b.post(f"{P}/agendamentos/", corpo, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("cliente", r.json()["fields"])
        corpo.update(servico=self.objetos["painel-agendamento"].servico.pk,
                     cliente=criar_cliente(self.b).pk)
        r = self.client_b.post(f"{P}/agendamentos/", corpo, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("servico", r.json()["fields"])

    def test_b_nao_ve_agendamento_nem_cliente_de_a_nas_listagens_filtradas(self):
        self.assertEqual(self.client_b.get(f"{P}/agendamentos/", {"status": "pendente"}).json(), [])
        self.assertEqual(self.client_b.get(f"{P}/clientes/", {"busca": "Cliente"}).json(), [])

    def test_configuracao_e_por_tenant(self):
        Configuracao.objects.create(profissional=self.a, janela_max_dias=15)
        self.assertEqual(self.client_b.get(f"{P}/configuracao/").json()["janela_max_dias"], 60)
        self.client_b.patch(f"{P}/configuracao/", {"janela_max_dias": 90, "profissional": self.a.pk}, format="json")
        self.assertEqual(Configuracao.objects.get(profissional=self.a).janela_max_dias, 15)
        self.assertEqual(Configuracao.objects.get(profissional=self.b).janela_max_dias, 90)


@override_settings(LIMITES_PUBLICOS=LIMITES_ALTOS)
class AcessoPainelTests(TestCase):
    def setUp(self):
        self.a = criar_profissional("calita")
        admin = admin_plataforma()
        pendente = criar_profissional("em-analise", ativa=False)
        suspensa = criar_profissional("suspensa-x")
        suspensa.mudar_status("suspensa", admin)
        rejeitada = criar_profissional("rejeitada-x", ativa=False)
        rejeitada.mudar_status("rejeitada", admin)
        sem_perfil = get_user_model().objects.create_user("sem-perfil", password="x")
        self.clientes = {
            "pendente": cliente_logado(pendente.user),
            "suspensa": cliente_logado(suspensa.user),
            "rejeitada": cliente_logado(rejeitada.user),
            "superusuario sem perfil": cliente_logado(admin),
            "usuario sem perfil": cliente_logado(sem_perfil),
            "anonimo": APIClient(enforce_csrf_checks=True),
        }

    def urls_e_metodos(self):
        for nome, view, actions, tem_pk in rotas_do_router():
            url = reverse(nome, kwargs={"pk": 1}) if tem_pk else reverse(nome)
            yield nome, url, list(actions)
        yield "painel-configuracao", reverse("painel-configuracao"), ["get", "patch"]

    def test_contas_nao_ativas_e_anonimo_recebem_403_em_todas_as_rotas(self):
        total = 0
        for quem, client in self.clientes.items():
            for nome, url, metodos in self.urls_e_metodos():
                for metodo in metodos:
                    with self.subTest(quem=quem, rota=nome, metodo=metodo):
                        resposta = getattr(client, metodo)(url, {}, format="json")
                        self.assertEqual(resposta.status_code, 403, resposta.content)
                        total += 1
        self.assertGreater(total, 100)

    def test_csrf_exigido_em_post_autenticado(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.a.user)
        corpo = {"nome": "Gel", "duracao_min": 30, "preco": "10"}
        self.assertEqual(client.get(f"{P}/servicos/").status_code, 200)  # GET nao exige
        sem = client.post(f"{P}/servicos/", corpo, format="json")
        self.assertEqual(sem.status_code, 403)
        self.assertFalse(Servico.objects.exists())
        token = client.get("/api/v1/auth/csrf/").json()["csrfToken"]
        com = client.post(f"{P}/servicos/", corpo, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(com.status_code, 201, com.content)

    def test_csrf_exigido_em_patch_delete_e_acoes(self):
        servico = criar_servico(self.a)
        ag = criar_agendamento(self.a)
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.a.user)
        self.assertEqual(client.patch(f"{P}/servicos/{servico.pk}/", {"nome": "X"}, format="json").status_code, 403)
        self.assertEqual(client.delete(f"{P}/servicos/{servico.pk}/").status_code, 403)
        self.assertEqual(client.post(f"{P}/agendamentos/{ag.pk}/confirmar/", {}, format="json").status_code, 403)
        self.assertEqual(client.patch(f"{P}/configuracao/", {"janela_max_dias": 5}, format="json").status_code, 403)
        ag.refresh_from_db()
        self.assertEqual(ag.status, "pendente")
        self.assertTrue(Servico.objects.filter(pk=servico.pk).exists())
        self.assertFalse(Configuracao.objects.filter(profissional=self.a, janela_max_dias=5).exists())
