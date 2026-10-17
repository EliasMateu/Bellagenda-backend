"""Configuracoes do Bela Agenda. Valores sensiveis vem de variaveis de ambiente."""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _carregar_env(caminho):
    """Carrega pares CHAVE=VALOR de um .env local sem sobrescrever o ambiente."""
    if not caminho.is_file():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip())


_carregar_env(BASE_DIR / ".env")


def _lista(valor):
    return [item.strip() for item in valor.split(",") if item.strip()]


def _booleano(valor):
    return valor.strip().lower() in ("1", "true", "yes")


SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

DEBUG = _booleano(os.environ.get("DJANGO_DEBUG", "False"))

ALLOWED_HOSTS = _lista(os.environ.get("DJANGO_ALLOWED_HOSTS", ""))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.core",
    "apps.contas",
    "apps.servicos",
    "apps.clientes",
    "apps.agenda",
    "apps.estoque",
    "apps.relatorios",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# "app" (padrao): usuario sem DDL, usado pela aplicacao.
# "owner": dono do banco, necessario para migrate e testes (cria tabelas e o banco de teste).
DJANGO_DB_ROLE = os.environ.get("DJANGO_DB_ROLE", "app")
if DJANGO_DB_ROLE == "owner":
    _db_user, _db_password = os.environ["POSTGRES_USER"], os.environ["POSTGRES_PASSWORD"]
elif DJANGO_DB_ROLE == "app":
    _db_user, _db_password = os.environ["POSTGRES_APP_USER"], os.environ["POSTGRES_APP_PASSWORD"]
else:
    raise ValueError("DJANGO_DB_ROLE deve ser 'app' ou 'owner'")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": _db_user,
        "PASSWORD": _db_password,
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

# Permite que mais de um executor use o mesmo PostgreSQL sem colidir no banco de testes.
if os.environ.get("POSTGRES_TEST_DB"):
    DATABASES["default"]["TEST"] = {"NAME": os.environ["POSTGRES_TEST_DB"]}

# Somente ao rodar os testes: hash rapido para nao deixar a suite lenta.
if len(sys.argv) > 1 and sys.argv[1] == "test":
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.tratar_excecao",
    # Restritivo por padrao: views publicas, saude e login declaram suas listas explicitamente.
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}

# Cookies e CSRF. Em producao (HTTPS) defina DJANGO_SESSION_COOKIE_SECURE/DJANGO_CSRF_COOKIE_SECURE=True.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = _booleano(os.environ.get("DJANGO_SESSION_COOKIE_SECURE", "False"))
CSRF_COOKIE_SECURE = _booleano(os.environ.get("DJANGO_CSRF_COOKIE_SECURE", "False"))
CSRF_TRUSTED_ORIGINS = _lista(os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", ""))

# Cache em tabela (sem Redis), usado pelo limite de requisicoes. Crie a tabela com
# `DJANGO_DB_ROLE=owner python manage.py createcachetable`. MAX_ENTRIES alto evita que o
# culling descarte contadores sob ataque.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "cache_throttle",
        "OPTIONS": {"MAX_ENTRIES": 100000},
    }
}

# Limite de requisicoes da API publica: (maximo, janela em segundos). Contadores em janela fixa.
LIMITES_PUBLICOS = {
    "ip_post": (10, 3600),        # POSTs publicos por IP
    "telefone": (5, 3600),        # criacoes de agendamento por telefone
    "tenant": (30, 3600),         # POSTs publicos por profissional (slug)
    "consulta": (5, 3600),        # consultar/cancelar por IP + slug
    "login_ip": (10, 3600),       # tentativas de login por IP
    "login_usuario": (10, 3600),  # tentativas de login por username
}

# IP do cliente: por ora REMOTE_ADDR. Quando o proxy da Vercel/Caddy estiver na frente, o IP real
# virira de X-Forwarded-For; so confie nesse cabecalho contando NUM_PROXIES saltos confiaveis.
NUM_PROXIES = 0
