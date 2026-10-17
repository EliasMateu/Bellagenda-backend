# Bellagenda-backend

API Django + DRF do Bela Agenda.

## Como rodar (desenvolvimento)

Requisitos: Python 3.13 e Docker.

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # ajuste as credenciais de desenvolvimento
docker compose -f docker-compose.dev.yml up -d   # PostgreSQL em 127.0.0.1:5432

# migrate e testes precisam do DONO do banco (o papel de app nao tem DDL):
DJANGO_DB_ROLE=owner python manage.py migrate     # PowerShell: $env:DJANGO_DB_ROLE="owner"
DJANGO_DB_ROLE=owner python manage.py test
python manage.py check
python manage.py runserver                         # roda com o papel de aplicacao (padrao)
```

Verificacao: `GET http://127.0.0.1:8000/api/v1/saude/` retorna `200 {"status": "ok"}`.

O `.env` nao e versionado. O PostgreSQL de desenvolvimento publica a porta somente em 127.0.0.1.

## Papeis de banco

- **Dono** (`POSTGRES_USER`): roda migracoes e testes (`DJANGO_DB_ROLE=owner`).
- **Aplicacao** (`POSTGRES_APP_USER`, padrao em `DJANGO_DB_ROLE=app`): somente SELECT/INSERT/UPDATE/DELETE.
  Sem DDL, sem superusuario. Criado por `docker/postgres/init/01-papel-aplicacao.sh`, que tambem define
  `ALTER DEFAULT PRIVILEGES` para que tabelas e sequencias criadas por migracoes futuras ja concedam CRUD
  ao papel de aplicacao.

O script de init so roda quando o volume e criado. Para recriar o banco de desenvolvimento (APAGA os dados):

```bash
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d
DJANGO_DB_ROLE=owner python manage.py migrate
```

## App `contas`

`Profissional` (OneToOne com o `User` do Django): slug imutavel e unico sem diferenciar maiusculas
(3 a 40 caracteres, lista de reservados em `apps/contas/validators.py`). O admin da plataforma troca o slug
com `profissional.alterar_slug_admin(novo, por=superusuario)`. Status: `pendente -> ativa | rejeitada` e
`ativa <-> suspensa`, via `profissional.mudar_status(novo, por=superusuario, motivo="")`.

## API publica de agendamento (sem login)

Sob `/api/v1/publico/<slug>/` (slug inexistente ou de conta nao ativa = o mesmo 404):

- `GET servicos/` · `GET horarios/?servico=ID&data=AAAA-MM-DD`
- `POST agendamentos/` (servico, inicio, nome, telefone, consentimento=true, observacao?, website vazio)
- `POST agendamento/consultar/` e `POST agendamento/cancelar/` com `{token}` ou `{codigo, telefone}`

Erros no formato `{code, message, fields}`. O limite de requisicoes usa cache em tabela:
crie-a uma vez com `DJANGO_DB_ROLE=owner python manage.py createcachetable` (os testes a criam sozinhos).
Limites em `LIMITES_PUBLICOS` (settings.py); o IP e o `REMOTE_ADDR` por enquanto (ver `NUM_PROXIES`).

## Testes em paralelo

Para dois executores usarem o mesmo PostgreSQL sem colidir, defina `POSTGRES_TEST_DB` com um nome de banco
de testes proprio: `DJANGO_DB_ROLE=owner POSTGRES_TEST_DB=test_forja python manage.py test`.

## Login e painel

- `GET /api/v1/auth/csrf/` define o cookie `csrftoken` (e devolve o token); enviar em `X-CSRFToken` nos POST/PATCH/DELETE.
- `POST /api/v1/auth/login/` (username, password), `POST /api/v1/auth/logout/`, `GET /api/v1/auth/me/`.
  Conta pendente loga e ve `/auth/me`, mas o painel responde 403.
- `/api/v1/painel/`: `servicos`, `clientes` (`?busca=`), `horarios-trabalho`, `bloqueios`, `configuracao`,
  `agendamentos` (`?de=&ate=&status=`; acoes POST `confirmar`, `recusar`, `cancelar`, `concluir`, `faltou`, `remarcar`).
- Cookies: `DJANGO_SESSION_COOKIE_SECURE`, `DJANGO_CSRF_COOKIE_SECURE` (True em producao) e `DJANGO_CSRF_TRUSTED_ORIGINS`.
- Use `--noinput` ao rodar testes sem terminal (`manage.py test --noinput`), para recriar o banco de testes sem perguntar.
