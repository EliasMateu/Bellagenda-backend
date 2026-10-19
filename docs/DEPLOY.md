# Deploy de producao do backend (VPS Hostinger KVM 2, Ubuntu)

Passo a passo para aplicar numa VPS **crua**. Hostname: `srv2037119.hstgr.cloud` (sem dominio proprio; o Caddy
emite o certificado Let's Encrypt para esse hostname). Arquitetura (D-14/D-16): so o **Caddy** publica portas
(80/443); Django/Gunicorn e PostgreSQL ficam numa rede interna do Docker.

```
Internet -> Caddy (80/443, HTTPS) -> web (Gunicorn :8000, rede interna) -> db (PostgreSQL, rede interna)
```

> Substitua `SEU-USUARIO`, `SEU-PROJETO.vercel.app` e os valores `TROQUE-*` pelos seus. Nunca cole segredos em
> arquivos versionados, chats ou issues.

## 1. Preparar o servidor

Entre como root (primeiro acesso) e atualize:

```bash
apt update && apt full-upgrade -y
apt install -y ufw git curl ca-certificates openssl
```

### 1.1 Usuario nao root com sudo

```bash
adduser SEU-USUARIO            # defina uma senha forte
usermod -aG sudo SEU-USUARIO
```

### 1.2 Chave SSH e desligar login por senha/root

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

### 1.3 Firewall (ufw): apenas 22, 80 e 443

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

### 1.4 Docker e plugin compose (repositorio oficial)

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

## 2. Codigo e variaveis

```bash
cd ~
git clone -b main https://github.com/EliasMateu/Bellagenda-backend.git bellagenda-backend
cd bellagenda-backend
cp .env.production.example .env.production
chmod 600 .env.production
```

(Se o repositorio for privado, use um token de leitura ou uma deploy key.)

### 2.1 Gerar os segredos

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

## 3. Subir pela primeira vez

```bash
dc config > /dev/null && echo "compose ok"      # falha se faltar alguma variavel obrigatoria
dc build web                                    # constroi a imagem
dc up -d db                                     # cria o volume; o init cria papeis, banco e btree_gist
dc ps                                           # aguarde "healthy" no db
```

### 3.1 Migrar com o papel DONO

A aplicacao roda com o papel **sem DDL**, entao as migracoes usam o servico `migrar` (papel dono, perfil `ops`,
nao sobe com `up`):

```bash
dc run --rm migrar                                              # python manage.py migrate
dc run --rm migrar python manage.py createcachetable            # tabela do limite de requisicoes
```

### 3.2 Estaticos, superusuario e primeira profissional

```bash
dc run --rm web python manage.py collectstatic --noinput        # estaticos do admin -> volume do Caddy
dc up -d                                                        # sobe web e caddy (espera o web ficar healthy)
dc run --rm web python manage.py createsuperuser                # admin da plataforma (pede a senha no terminal)
dc run --rm web python manage.py criar_profissional \
  --username calita --nome "Calita" --telefone "(15) 99766-9299" --slug calita \
  --ativa --aprovado-por SEU-SUPERUSUARIO --horarios-padrao      # pede a senha no terminal
```

### 3.3 Testar

```bash
dc ps                                                   # db, web e caddy "healthy/running"
curl -i https://srv2037119.hstgr.cloud/api/v1/saude/    # 200 {"status":"ok"}; certificado valido
curl -sI http://srv2037119.hstgr.cloud/ | head -3       # 308 redirecionando para https
curl -s https://srv2037119.hstgr.cloud/api/v1/cadastro/config/    # {"aberto":false,...}
```

O backoffice fica em `https://srv2037119.hstgr.cloud/<DJANGO_ADMIN_PATH>/` (so superusuario). O painel de contas
da API esta em `/api/v1/backoffice/contas/` (tambem so superusuario; para os demais responde 404).

### 3.4 Lado da Vercel

- Configure o frontend para chamar `https://srv2037119.hstgr.cloud`.
- O proxy do frontend deve enviar, em cada chamada ao backend, `X-Bela-Proxy-Secret: <DJANGO_PROXY_SECRET>` e
  `X-Bela-Client-Ip: <IP real do visitante>`. Sem isso, todos os visitantes parecem ter o IP da Vercel e os limites
  de requisicao se juntam. O backend ignora `X-Forwarded-For` de proposito.

## 4. Operacao

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
`dc down && docker volume rm bellagenda_pgdata` (e repita a secao 3).

## 5. NAO FAZER AINDA: backup

> **ATENCAO: nao ha backup configurado, por decisao do usuario.** Se a VPS ou o volume `pgdata` for perdido, **todos os
> dados serao perdidos** (contas, clientes, agendamentos). Configure backup automatico fora da VPS antes de ter
> dados reais.

Quando quiser, um dump manual de 1 comando (gera um arquivo na pasta atual; copie-o para **fora** da VPS, porque
um arquivo guardado so aqui nao e backup):

```bash
dc exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$DB_NAME" -Fc' > bela-$(date +%F).dump
```

## 6. Checklist de seguranca final

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

## 7. Solucao de problemas

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
