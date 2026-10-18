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
