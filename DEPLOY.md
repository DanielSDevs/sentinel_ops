# Deploy no Azure App Service

Guia para publicar o SentinelOps no **Azure App Service (Linux, Python 3.13)**, com a base
**SQLite no armazenamento persistente** do App Service e publicação automática pelo
**GitHub Actions** a cada push na `main`.

```
GitHub (push na main)
   └─ Actions: testes → check --deploy → collectstatic → release.zip
         └─ App Service (Linux, B1)
               ├─ Oryx: pip install -r requirements.txt
               ├─ startup.sh: troca de base (se houver) → migrate → gunicorn
               └─ /home/data/db.sqlite3   ← persistente entre deploys e reinícios
```

O código é extraído para uma pasta temporária a cada deploy; **só `/home` sobrevive**. Por isso a
base mora em `/home/data/`, fora da pasta do código.

## O que o repositório já traz

| Arquivo | Papel |
|---|---|
| `config/settings.py` | Tudo que muda em produção vem de variável de ambiente. No App Service a variável `WEBSITE_HOSTNAME` existe sempre e liga o modo produção: `DEBUG` desligado, host liberado, cookies seguros, redirecionamento para HTTPS, WhiteNoise servindo os estáticos. Sem nenhuma variável, o projeto roda em dev como antes. |
| `startup.sh` | Comando de inicialização: põe a base no lugar, roda `migrate` e sobe o gunicorn. |
| `.github/workflows/azure-deploy.yml` | Testa, valida a configuração de produção, coleta os estáticos e publica. |
| `requirements.txt` | Inclui `gunicorn` e `whitenoise`. |

Toda tela exige login (`LoginRequiredMiddleware`). O cadastro em `/contas/registro/` continua
aberto: qualquer pessoa com a URL pode criar uma conta e entrar.

---

## 1. Criar o App Service

No [portal da Azure](https://portal.azure.com): **Criar um recurso → Aplicativo Web**.

| Campo | Valor |
|---|---|
| Grupo de recursos | novo, ex.: `rg-sentinelops` |
| Nome | ex.: `sentinelops-<seu-nome>` (vira parte da URL; precisa ser único) |
| Publicar | **Código** |
| Pilha de runtime | **Python 3.13** |
| Sistema operacional | **Linux** |
| Região | Brazil South (menor latência) ou East US (mais barato) |
| Plano de preços | **Básico B1** |

Na aba **Implantação**, deixe a implantação contínua **desabilitada**: o repositório já tem o
próprio workflow, e ativar ali faria o portal commitar um segundo.

> **E o plano gratuito (F1)?** Funciona para uma demonstração rápida, mas tem cota de 60 min de CPU
> por dia e não permite *Always On* — o primeiro acesso depois de um período parado fica lento, e o
> Correlation Engine consome a cota rápido.

## 2. Configurar o app

### Variáveis de ambiente

Em **Configurações → Variáveis de ambiente → Configurações de aplicativo**, adicione:

| Nome | Valor |
|---|---|
| `DJANGO_SECRET_KEY` | uma chave longa e aleatória (gere com o comando abaixo) |
| `SQLITE_PATH` | `/home/data/db.sqlite3` |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` |

```powershell
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Opcionais: `DJANGO_ALLOWED_HOSTS` e `DJANGO_CSRF_TRUSTED_ORIGINS` (listas separadas por vírgula,
só se você configurar um domínio próprio) e `DJANGO_LOG_LEVEL` (padrão `INFO`).

### Configurações gerais

Em **Configurações → Configuração → Configurações gerais**:

| Campo | Valor |
|---|---|
| Comando de inicialização | `sh startup.sh` |
| Credenciais de publicação básica do SCM | **Ativado** (necessário para o perfil de publicação) |
| Always On | **Ativado** — mantém o cache do Correlation Engine quente |
| Somente HTTPS | **Ativado** |

Salve. O app reinicia.

## 3. Ligar o GitHub ao App Service

1. No portal, na **Visão geral** do app, clique em **Baixar perfil de publicação**. Se o botão
   estiver desabilitado, a opção de credenciais básicas do SCM do passo anterior ainda está
   desligada.
2. No GitHub, em `DanielSDevs/sentinel_ops` → **Settings → Secrets and variables → Actions**:
   - aba **Secrets** → *New repository secret*: nome `AZURE_WEBAPP_PUBLISH_PROFILE`, valor = o
     conteúdo inteiro do arquivo `.PublishSettings` baixado;
   - aba **Variables** → *New repository variable*: nome `AZURE_WEBAPP_NAME`, valor = o nome do app.

> O perfil de publicação é uma credencial: não o commite e apague o arquivo baixado depois de
> colá-lo no GitHub.

## 4. Primeiro deploy

Faça push na `main` ou rode o workflow à mão em **Actions → Deploy no Azure App Service → Run
workflow**. O job *Testes e pacote* roda os testes, confere a configuração de produção
(`check --deploy`) e gera o pacote; *Publicar no App Service* envia o pacote, e o App Service
instala as dependências (leva alguns minutos no primeiro deploy por causa do `pandas`).

Enquanto a variável `AZURE_WEBAPP_NAME` não existir, o workflow só roda os testes e marca a
publicação como *skipped*.

Ao final, abra `https://<seu-app>.azurewebsites.net` (a URL exata aparece no job e na Visão geral).
Você deve cair na tela de login, com a plataforma **vazia**: o `startup.sh` criou uma base nova em
`/home/data/`. Os dados entram no próximo passo.

## 5. Carregar os dados

O extrato da Locaweb não está no repositório, então a base é montada na sua máquina e enviada
pronta.

### 5.1 Preparar a base localmente

```powershell
python manage.py migrate
python manage.py importar_dataset         # se a base local ainda não tem os dados
python manage.py createsuperuser          # o usuário administrador vai junto com a base
```

Pare o `runserver` antes de enviar. Tudo o que está na base local sobe junto: **usuários locais**
(com senhas em hash) e **incidentes de simulação**. Se houver simulações, limpe-as pelo controle de
simulação do Ops Monitor antes, ou rode `importar_dataset` de novo.

### 5.2 Enviar como `db.sqlite3.novo`

A base **não** deve ser sobrescrita com o app rodando. Envie o arquivo com o nome
`db.sqlite3.novo`; no próximo reinício, o `startup.sh` o põe no lugar antes de o gunicorn abrir o
banco e guarda a base anterior como `db.sqlite3.anterior`.

**Opção A — linha de comando (PowerShell, sem instalar nada).** Pegue usuário e senha no arquivo
do perfil de publicação (`userName` e `userPWD`) e o endereço do Kudu no portal, em **Ferramentas
de Desenvolvimento → Ferramentas Avançadas → Ir** (algo como `https://<seu-app>.scm.azurewebsites.net`):

```powershell
$scm   = 'https://<seu-app>.scm.azurewebsites.net'
$user  = '$<seu-app>'          # userName do perfil — começa com $, mantenha as aspas simples
$senha = '<userPWD>'
curl.exe --fail -u "${user}:${senha}" -X PUT --data-binary "@db.sqlite3" "$scm/api/vfs/data/db.sqlite3.novo"
```

**Opção B — navegador.** Copie `db.sqlite3` para `db.sqlite3.novo` na sua máquina, abra o Kudu
(mesmo caminho acima), vá em **File Manager**, entre na pasta `data` e arraste o arquivo.

### 5.3 Reiniciar

Na Visão geral do app, **Reiniciar**. No **Fluxo de log** deve aparecer
`startup: nova base encontrada`. Recarregue a plataforma: o selo "Dados até 31/12/2025" na topbar
confirma que a base real está no ar.

Para **atualizar os dados** no futuro, repita os passos 5.1 a 5.3. Para **desfazer** uma troca,
renomeie `db.sqlite3.anterior` para `db.sqlite3.novo` pelo Kudu e reinicie.

### Criar usuários direto no servidor (opcional)

Se preferir não levar usuários na base, crie o administrador pelo SSH do app (**Ferramentas de
Desenvolvimento → SSH → Ir**):

```sh
SQLITE_PATH=/home/data/db.sqlite3 python manage.py createsuperuser
```

Se o `manage.py` não estiver na pasta em que o SSH abriu, localize-o com
`ls /home/site/wwwroot/manage.py /tmp/*/manage.py`, entre na pasta e ative o ambiente com
`source antenv/bin/activate`.

---

## Solução de problemas

O **Fluxo de log** (Monitoramento → Fluxo de log) mostra a saída do `startup.sh`, do gunicorn e os
erros do Django.

| Sintoma | Causa provável |
|---|---|
| `:( Application Error` logo após o deploy | O app não subiu. Veja o Fluxo de log; o mais comum é `DJANGO_SECRET_KEY` ausente (`ImproperlyConfigured`) ou o comando de inicialização não configurado. |
| Plataforma vazia depois do deploy | Esperado até o passo 5. Se persistir, confira `SQLITE_PATH` e se o arquivo está em `/home/data/`. |
| 400 Bad Request | Host não permitido. Acessando por domínio próprio, inclua-o em `DJANGO_ALLOWED_HOSTS` e `https://<domínio>` em `DJANGO_CSRF_TRUSTED_ORIGINS`. |
| 403 no login (CSRF) | Mesmo caso acima, para `DJANGO_CSRF_TRUSTED_ORIGINS`. |
| Botão "Baixar perfil de publicação" desabilitado | Ative as credenciais de publicação básica do SCM (passo 2). |
| `ModuleNotFoundError: No module named 'django'` no log | `SCM_DO_BUILD_DURING_DEPLOYMENT` ausente: o pacote chegou sem as dependências instaladas. Adicione a variável e publique de novo. |
| `database is locked` no log | Escrita concorrente no SQLite (cadastros e simulações ao mesmo tempo). O app espera até 20 s pelo lock; se for frequente, é hora de migrar para PostgreSQL. |
| `412 Precondition Failed` no upload | Já existe um `db.sqlite3.novo` pendente. Reinicie o app para consumi-lo ou apague-o pelo Kudu. |

## Limites desta configuração

- **Uma instância só.** Cada instância do App Service abriria o mesmo arquivo SQLite pela rede;
  não aumente a contagem de instâncias nem ative o dimensionamento automático.
- **`/home` é um compartilhamento de rede.** Leitura é o caso comum da plataforma e vai bem;
  escritas (cadastro, simulação) são raras. O gunicorn roda com 1 processo e 4 threads — ajuste com
  `GUNICORN_WORKERS` e `GUNICORN_THREADS` se precisar.
- **Backup é manual:** baixe `/home/data/db.sqlite3` pelo Kudu antes de trocas importantes. A troca
  pelo `startup.sh` guarda só a versão imediatamente anterior.
- A arquitetura-alvo (PostgreSQL, Celery/Redis) continua fora de escopo. Migrar para o Azure
  Database for PostgreSQL exige trocar `DATABASES` e rodar `importar_dataset` contra o novo banco.
