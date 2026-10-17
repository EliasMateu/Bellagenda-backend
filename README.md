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

python manage.py check
python manage.py test
python manage.py runserver
```

Verificacao: `GET http://127.0.0.1:8000/api/v1/saude/` retorna `200 {"status": "ok"}`.

O `.env` nao e versionado. O PostgreSQL de desenvolvimento publica a porta somente em 127.0.0.1.
