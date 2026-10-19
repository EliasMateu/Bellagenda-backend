#!/bin/sh
# PRODUCAO. Executado pelo entrypoint do postgres como SUPERUSUARIO DO CONTAINER, apenas na PRIMEIRA
# criacao do volume (se o volume ja existe, nada disto roda de novo; veja docs/DEPLOY.md).
#
# Cria:
#   - papel DONO (nao superusuario): roda as migracoes e e dono do banco;
#   - papel de APLICACAO (nao superusuario): somente CRUD, sem DDL, usado pelo Gunicorn;
#   - o banco, pertencente ao dono;
#   - a extensao btree_gist (exigida pela ExclusionConstraint de agendamentos), criada aqui uma vez.
# O superusuario do container NAO e usado pela aplicacao.
set -e

: "${DB_NAME:?}" "${DB_OWNER_USER:?}" "${DB_OWNER_PASSWORD:?}" "${DB_APP_USER:?}" "${DB_APP_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v db="$DB_NAME" \
  -v owner="$DB_OWNER_USER" -v owner_pw="$DB_OWNER_PASSWORD" \
  -v app="$DB_APP_USER" -v app_pw="$DB_APP_PASSWORD" <<'SQL'
CREATE ROLE :"owner" LOGIN PASSWORD :'owner_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE ROLE :"app" LOGIN PASSWORD :'app_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;

CREATE DATABASE :"db" OWNER :"owner";
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO :"app";
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB_NAME" \
  -v owner="$DB_OWNER_USER" -v app="$DB_APP_USER" <<'SQL'
CREATE EXTENSION IF NOT EXISTS btree_gist;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"app";
GRANT ALL ON SCHEMA public TO :"owner";

-- Tabelas e sequencias criadas depois pelo dono (migracoes) concedem CRUD ao papel de aplicacao.
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"app";
SQL
