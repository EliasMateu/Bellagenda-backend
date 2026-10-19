# Deploy de producao do backend (VPS Hostinger KVM 2, Ubuntu)

Hostname: `srv2037119.hstgr.cloud` (sem dominio proprio; ele resolve para o IP da VPS). Dois caminhos:

- **A. VPS que ja tem Coolify/Traefik (caminho recomendado para a srv2037119)** — secao logo abaixo. Usa
  `docker-compose.coolify.yml`; o Traefik do Coolify ja e dono das portas 80/443 e emite o certificado.
- **B. VPS crua, sem Coolify (Caddy)** — a partir da secao "B" mais adiante. Usa `docker-compose.prod.yml`.

> **NAO use o `docker-compose.prod.yml` na VPS com Coolify:** o Caddy tentaria publicar 80/443, que o Traefik ja usa.

# A. VPS que ja tem Coolify/Traefik (caminho recomendado para a srv2037119)

Fatos da VPS (levantados so em leitura): Docker 29 e Compose v5; o proxy `coolify-proxy` (Traefik v3.6) usa 80/443,
com entrypoints `http` (:80) e `https` (:443), resolver Let's Encrypt `letsencrypt` (desafio HTTP), provider Docker com
`exposedbydefault=false` e a rede Docker externa `coolify`. Na mesma VPS rodam apps de **terceiros** (Chatwoot,
Evolution API) que **nao podem ser afetados**. Este projeto roda isolado com o nome `bellagenda`, entra na rede
`coolify` apenas com o `web` e o `estaticos` (para o Traefik alcanca-los) e **nao publica nenhuma porta**.

```
Internet -> Traefik (Coolify, 80/443, HTTPS) -> web (Gunicorn :8000)                 [redes: interna + coolify]
                                              -> estaticos (nginx, so /static/admin)  [rede: coolify]
                                 web -> db (PostgreSQL)                               [rede interna, sem acesso externo]
```

## A.0 Pre-requisitos

- Acesso SSH como usuario nao root no grupo `docker`, e `git` instalado.
- O hostname `srv2037119.hstgr.cloud` resolve para o IP da VPS (`dig +short srv2037119.hstgr.cloud`). Na primeira
  vez, a emissao do certificado Let's Encrypt pelo Traefik **pode levar alguns minutos**.
- Codigo e `.env.production` prontos: siga a secao **B.2** (clonar e gerar segredos) deste documento. Aqui
  `SITE_ADDRESS` e **obrigatorio** (`SITE_ADDRESS=srv2037119.hstgr.cloud`), pois vira a regra de host do Traefik.
- Alias (coloque no `~/.bashrc`; use sempre dentro da pasta do repositorio):

```bash
alias dc='docker compose -p bellagenda --env-file .env.production -f docker-compose.coolify.yml'
```

## A.1 GUARDRAILS (leia antes de qualquer comando)

- **Nunca** use `docker system prune` (nem `docker volume prune`, `docker network prune`, `docker image prune -a`).
- **Nunca** reinicie o daemon do Docker (`systemctl restart docker`).
- **Nunca** mexa em containers `coolify-*`, `chatwoot-*`, `sidekiq-*`, `evolution-*`, `redis-*` nem `postgres-*`
  de outros projetos (nem pare, reinicie, remova ou entre neles).
- **Sempre** use `-p bellagenda` (o alias `dc` ja faz isso) e nunca `down -v`.
- **Nunca** edite arquivos ou configuracoes do Coolify/Traefik: este projeto so se anuncia por etiquetas (labels).
- Confira com `docker ps --format 'table {{.Names}}\t{{.Status}}'` que os containers dos outros projetos continuam
  saudaveis **antes e depois** de cada etapa que mexe em containers (`up`, `down`, `restart`).

## A.2 Subir pela primeira vez (ordem exata)

```bash
cd ~/bellagenda-backend
docker ps --format 'table {{.Names}}\t{{.Status}}'        # anote o estado dos outros projetos
dc config > /dev/null && echo "compose ok"                 # falha se faltar variavel obrigatoria
dc build web                                               # constroi a imagem bellagenda-web:prod
dc up -d db                                                # cria o volume; o init cria papeis, banco e btree_gist
dc ps                                                      # aguarde "healthy" no db

dc run --rm migrar                                         # migrate com o papel DONO
dc run --rm migrar python manage.py createcachetable      # tabela do limite de requisicoes (papel dono)
dc run --rm web python manage.py collectstatic --noinput  # estaticos do admin -> volume static_files

dc up -d                                                   # sobe web e estaticos (o Traefik os detecta pelas etiquetas)
dc run --rm web python manage.py createsuperuser           # admin da plataforma (senha pedida no terminal)
dc run --rm web python manage.py criar_profissional \
  --username calita --nome "Calita" --telefone "(15) 99766-9299" --slug calita \
  --ativa --aprovado-por SEU-SUPERUSUARIO --horarios-padrao         # senha pedida no terminal
docker ps --format 'table {{.Names}}\t{{.Status}}'        # os outros projetos devem estar iguais ao inicio
```

## A.3 Testar por HTTPS

```bash
dc ps                                                     # db, web e estaticos "healthy"
curl -i https://srv2037119.hstgr.cloud/api/v1/saude/      # 200 {"status":"ok"} com certificado valido
curl -sI http://srv2037119.hstgr.cloud/api/v1/saude/ | head -3     # 301 -> https
curl -sI https://srv2037119.hstgr.cloud/static/admin/css/base.css | head -3    # 200 (estaticos)
curl -sI https://srv2037119.hstgr.cloud/static/rest_framework/css/default.css | head -1   # 404: so /static/admin e servido
curl -s https://srv2037119.hstgr.cloud/api/v1/cadastro/config/    # {"aberto":false,...}
```

Se o certificado ainda nao saiu, aguarde alguns minutos e veja `docker logs --tail=50 coolify-proxy` (**somente
leitura**; nao reinicie o proxy).

### Conferir as etiquetas no Traefik (sem alterar nada)

- Dashboard/API do Traefik (se o Coolify o expuser): rotas `bellagenda-web@docker`, `bellagenda-web-http@docker` e
  `bellagenda-estaticos@docker`, middlewares `bellagenda-seguranca`, `bellagenda-compressao` e `bellagenda-https`,
  servicos `bellagenda-web` e `bellagenda-estaticos` (status `enabled`, servidor na porta 8000/80 da rede `coolify`).
- Pela linha de comando, so leitura: `docker inspect bellagenda-web-1 --format '{{json .Config.Labels}}'` e
  `docker inspect bellagenda-estaticos-1 --format '{{json .Config.Labels}}'`.
- Na pratica: `curl -sI https://srv2037119.hstgr.cloud/api/v1/saude/` deve trazer `strict-transport-security`,
  `x-content-type-options: nosniff`, `referrer-policy` e `x-frame-options: DENY`.

## A.4 Atualizar e ver logs

```bash
cd ~/bellagenda-backend
git pull origin main
dc build web
dc run --rm migrar
dc run --rm web python manage.py collectstatic --noinput
dc up -d                                    # recria so o que mudou
dc ps && curl -s https://srv2037119.hstgr.cloud/api/v1/saude/

dc logs -f --tail=100 web                   # logs do Django/Gunicorn (tambem: estaticos, db)
dc restart web                              # reiniciar so a aplicacao
```

## A.5 Reverter (parar so este projeto)

```bash
dc down            # SEM -v: para e remove apenas os containers deste projeto; volumes e dados permanecem
docker ps --format 'table {{.Names}}\t{{.Status}}'        # confira que os outros projetos seguem saudaveis
```

Isso nao remove a rede `coolify` (externa) nem toca no Traefik. Para voltar ao ar: `dc up -d`.

## A.6 Problemas comuns neste caminho

- **404 do Traefik** (pagina "404 page not found"): etiquetas nao detectadas. Confirme `dc ps` (web `healthy`), que
  `SITE_ADDRESS` esta correto e que web/estaticos estao na rede `coolify` (`docker inspect`).
- **502/504**: o `web` esta `unhealthy` ou fora da rede `coolify`; `dc logs --tail=100 web`.
- **Certificado nao emitido**: aguarde alguns minutos; confirme o DNS e `docker logs --tail=50 coolify-proxy`.
- **403 CSRF**: veja `DJANGO_CSRF_TRUSTED_ORIGINS` (secao 7, mais abaixo).
- **Rede `coolify` inexistente** (`network coolify declared as external, but could not be found`): o Coolify nao esta
  instalado nesta VPS; use o caminho B.

---

# B. VPS crua, sem Coolify (Caddy)

Passo a passo para aplicar numa VPS **crua** (sem Coolify). Hostname: `srv2037119.hstgr.cloud` (sem dominio proprio; o Caddy
emite o certificado Let's Encrypt para esse hostname). Arquitetura (D-14/D-16): so o **Caddy** publica portas
(80/443); Django/Gunicorn e PostgreSQL ficam numa rede interna do Docker.

```
Internet -> Caddy (80/443, HTTPS) -> web (Gunicorn :8000, rede interna) -> db (PostgreSQL, rede interna)
```

> Substitua `SEU-USUARIO`, `SEU-PROJETO.vercel.app` e os valores `TROQUE-*` pelos seus. Nunca cole segredos em
> arquivos versionados, chats ou issues.

## B.1 Preparar o servidor

Entre como root (primeiro acesso) e atualize:

```bash
apt update && apt full-upgrade -y
apt install -y ufw git curl ca-certificates openssl
```

### B.1.1 Usuario nao root com sudo

```bash
adduser SEU-USUARIO            # defina uma senha forte
usermod -aG sudo SEU-USUARIO
```

### B.1.2 Chave SSH e desligar login por senha/root

No **seu computador** (PowerShell ou terminal), se ainda nao tem chave:

```bash
ssh-keygen -t ed25519 -C "bela-agenda-vps"
ssh-copy-id SEU-USUARIO@srv2037119.hstgr.cloud      # ou cole a chave publica em ~/.ssh/authorized_keys
```

**Teste o login por chave numa SEGUNDA janela antes de continuar** (`ssh SEU-USUARIO@srv2037119.hstgr.cloud`).
Depois, na VPS:

```bash
sudo tee /etc/ssh/sshd_config.d/99-endurecimento.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sudo sshd -t && sudo systemctl restart ssh
```

Mantenha a primeira janela aberta ate confirmar, em outra janela, que o login por chave continua funcionando.

### B.1.3 Firewall (ufw): apenas 22, 80 e 443

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

> O Docker publica portas **por fora do ufw**. Por isso o `docker-compose.prod.yml` publica somente 80/443 (Caddy)
> e o banco **nao tem `ports`**. Nunca adicione `ports` ao servico `db`.

### B.1.4 Docker e plugin compose (repositorio oficial)

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker SEU-USUARIO     # saia e entre de novo para valer
docker --version && docker compose version
```

## B.2 Codigo e variaveis

```bash
cd ~
git clone -b main https://github.com/EliasMateu/Bellagenda-backend.git bellagenda-backend
cd bellagenda-backend
cp .env.production.example .env.production
chmod 600 .env.production
```

(Se o repositorio for privado, use um token de leitura ou uma deploy key.)

### B.2.1 Gerar os segredos

Gere cada valor e cole no `.env.production` (`nano .env.production`). **Senhas do banco em hexadecimal** evitam
caracteres que quebram o arquivo.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # DJANGO_SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # DJANGO_PROXY_SECRET (o MESMO valor vai na Vercel)
python3 -c "import secrets; print('gestao-' + secrets.token_hex(6))"   # DJANGO_ADMIN_PATH
openssl rand -hex 24      # rode 3 vezes: senha do superusuario, do dono e da aplicacao do PostgreSQL
```

Confira no arquivo:

- `DJANGO_ALLOWED_HOSTS=srv2037119.hstgr.cloud`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://SEU-PROJETO.vercel.app` (https, sem barra no fim)
- `DJANGO_CADASTRO_ABERTO=False` (so mude para `True` quando quiser aceitar cadastros)
- as **tres** credenciais do PostgreSQL diferentes entre si: superusuario do container, dono (migracoes) e aplicacao
  (CRUD). O nome do superusuario nao pode comecar com `pg_`.
- nenhum `TROQUE-` restante: `grep TROQUE .env.production` deve voltar vazio.

Atalho para os proximos comandos (coloque no `~/.bashrc` para nao perder ao sair):

```bash
alias dc='docker compose --env-file .env.production -f docker-compose.prod.yml'
```

Abaixo, `dc` = esse comando, executado dentro da pasta `bellagenda-backend`.

## B.3 Subir pela primeira vez

```bash
dc config > /dev/null && echo "compose ok"      # falha se faltar alguma variavel obrigatoria
dc build web                                    # constroi a imagem
dc up -d db                                     # cria o volume; o init cria papeis, banco e btree_gist
dc ps                                           # aguarde "healthy" no db
```

### B.3.1 Migrar com o papel DONO

A aplicacao roda com o papel **sem DDL**, entao as migracoes usam o servico `migrar` (papel dono, perfil `ops`,
nao sobe com `up`):

```bash
dc run --rm migrar                                              # python manage.py migrate
dc run --rm migrar python manage.py createcachetable            # tabela do limite de requisicoes
```

### B.3.2 Estaticos, superusuario e primeira profissional

```bash
dc run --rm web python manage.py collectstatic --noinput        # estaticos do admin -> volume do Caddy
dc up -d                                                        # sobe web e caddy (espera o web ficar healthy)
dc run --rm web python manage.py createsuperuser                # admin da plataforma (pede a senha no terminal)
dc run --rm web python manage.py criar_profissional \
  --username calita --nome "Calita" --telefone "(15) 99766-9299" --slug calita \
  --ativa --aprovado-por SEU-SUPERUSUARIO --horarios-padrao      # pede a senha no terminal
```

### B.3.3 Testar

```bash
dc ps                                                   # db, web e caddy "healthy/running"
curl -i https://srv2037119.hstgr.cloud/api/v1/saude/    # 200 {"status":"ok"}; certificado valido
curl -sI http://srv2037119.hstgr.cloud/ | head -3       # 308 redirecionando para https
curl -s https://srv2037119.hstgr.cloud/api/v1/cadastro/config/    # {"aberto":false,...}
```

O backoffice fica em `https://srv2037119.hstgr.cloud/<DJANGO_ADMIN_PATH>/` (so superusuario). O painel de contas
da API esta em `/api/v1/backoffice/contas/` (tambem so superusuario; para os demais responde 404).

### B.3.4 Lado da Vercel

- Configure o frontend para chamar `https://srv2037119.hstgr.cloud`.
- O proxy do frontend deve enviar, em cada chamada ao backend, `X-Bela-Proxy-Secret: <DJANGO_PROXY_SECRET>` e
  `X-Bela-Client-Ip: <IP real do visitante>`. Sem isso, todos os visitantes parecem ter o IP da Vercel e os limites
  de requisicao se juntam. O backend ignora `X-Forwarded-For` de proposito.

## B.4 Operacao

```bash
dc logs -f --tail=100 web          # logs do Django/Gunicorn (tambem: caddy, db)
dc ps                              # estado e saude
dc restart web                     # reiniciar so a aplicacao
dc restart caddy
dc down                            # para tudo (os volumes, e os dados, permanecem)
```

### Atualizar para uma versao nova

```bash
cd ~/bellagenda-backend
git pull origin main
dc build web
dc run --rm migrar                                          # aplica migracoes novas
dc run --rm web python manage.py collectstatic --noinput
dc up -d                                                    # recria o que mudou
dc ps && curl -s https://srv2037119.hstgr.cloud/api/v1/saude/
```

### Mudar as credenciais do PostgreSQL

O script `docker/postgres/init-prod/*.sh` roda **somente na primeira criacao do volume**. Alterar senhas ou nomes no
`.env.production` depois **nao** muda os papeis do banco. Para trocar uma senha:

```bash
dc exec db sh -c 'psql -U "$POSTGRES_USER" -d postgres -c "ALTER ROLE NOME-DO-PAPEL PASSWORD '"'"'NOVA-SENHA'"'"'"'
```

e atualize o mesmo valor no `.env.production` (depois `dc up -d`). **Recriar tudo do zero apaga os dados:**
`dc down && docker volume rm bellagenda_pgdata` (e repita a secao B.3).

## 5. NAO FAZER AINDA (vale para A e B): backup

> **ATENCAO: nao ha backup configurado, por decisao do usuario.** Se a VPS ou o volume `pgdata` for perdido, **todos os
> dados serao perdidos** (contas, clientes, agendamentos). Configure backup automatico fora da VPS antes de ter
> dados reais.

Quando quiser, um dump manual de 1 comando (gera um arquivo na pasta atual; copie-o para **fora** da VPS, porque
um arquivo guardado so aqui nao e backup):

```bash
dc exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$DB_NAME" -Fc' > bela-$(date +%F).dump
```

## 6. Checklist (vale para A e B) de seguranca final

- [ ] Login SSH por chave; `PermitRootLogin no`; `PasswordAuthentication no`.
- [ ] `ufw status` mostra so 22, 80 e 443.
- [ ] `sudo ss -tlnp` mostra 5432 e 8000 **fora** de `0.0.0.0` (so 22, 80 e 443 publicos).
- [ ] `.env.production` com `chmod 600`, sem `TROQUE-`, **fora do Git** (`git status` nao o lista).
- [ ] `DJANGO_DEBUG=False`, `DJANGO_ADMIN_PATH` proprio e nao obvio, `DJANGO_CADASTRO_ABERTO=False` ate querer abrir.
- [ ] Tres credenciais do PostgreSQL distintas; o app usa o papel sem DDL.
- [ ] `https://.../api/v1/saude/` responde 200 com certificado valido; HTTP redireciona para HTTPS.
- [ ] `DJANGO_PROXY_SECRET` igual na Vercel e no backend; `DJANGO_CSRF_TRUSTED_ORIGINS` com a URL exata da Vercel.
- [ ] `/<DJANGO_ADMIN_PATH>/` e `/api/v1/backoffice/contas/` respondem 404 para quem nao e superusuario.
- [ ] Ciente de que **nao ha backup** (secao 5).

## 7. Solucao (caminho B e geral) de problemas

**Portas 80/443 em uso (`address already in use`)** — outro servico ocupa a porta. `sudo ss -tlnp | grep -E ':80|:443'`.
Pare/desabilite o que aparecer (ex.: `sudo systemctl disable --now apache2 nginx`) e rode `dc up -d`.

**Certificado Let's Encrypt nao emitido** — `dc logs caddy`. Causas comuns: porta 80 ou 443 fechada (ufw/painel da
Hostinger), `SITE_ADDRESS` diferente do hostname real, o hostname nao resolve para o IP publico da VPS
(`dig +short srv2037119.hstgr.cloud`), ou limite de emissoes do Let's Encrypt (aguarde e tente de novo). Enquanto
isso, o navegador mostra erro de certificado e o `curl` falha no TLS.

**403 CSRF ao entrar/gravar** — `DJANGO_CSRF_TRUSTED_ORIGINS` precisa conter a origem exata do frontend (`https://...`,
sem barra final); cookies Secure exigem HTTPS de ponta a ponta; o frontend deve buscar `/api/v1/auth/csrf/` e enviar
o cabecalho `X-CSRFToken`. Depois de editar o `.env.production`: `dc up -d`.

**502 Bad Gateway no Caddy** — o `web` esta fora do ar ou nao saudavel. `dc ps` e `dc logs --tail=100 web`.
Causas: variavel obrigatoria faltando (`ImproperlyConfigured` no log), banco inacessivel, migracoes nao aplicadas.

**`web` fica `unhealthy` / 400 `DisallowedHost`** — `DJANGO_ALLOWED_HOSTS` precisa conter o hostname (o primeiro valor
da lista e usado pelo healthcheck interno).

**`permission denied` ao migrar** — migre sempre pelo servico `migrar` (papel dono), nunca pelo `web`.

**`relation cache_throttle does not exist`** — falta `dc run --rm migrar python manage.py createcachetable`.
