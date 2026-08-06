# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

SentinelOps é uma plataforma de AIOps (challenge FIAP 2026) para previsão de incidentes operacionais de TI. A visão completa está no PDF `EC_Sprint_2_2TSCPW_arqsolucao__sentinelops.pdf`: previsões D+1/D+7, detecção de padrões e alertas preventivos, com stack alvo Django + Celery/Redis, Airflow, MLflow, PostgreSQL e Prometheus/Grafana.

**Estado atual:** frontend Django com templates/estáticos e dados simulados em JS. Não há modelos de domínio, API nem integrações ainda — os comentários "sprints futuras" nos templates/JS marcam esses pontos.

## Comandos

```powershell
python manage.py runserver          # servidor de desenvolvimento (http://127.0.0.1:8000)
python manage.py migrate            # aplicar migrações (SQLite: db.sqlite3, ignorado pelo git)
python manage.py makemigrations core forecast ...   # nomes dos apps SEM o prefixo apps.
python manage.py test               # todos os testes
python manage.py test apps.forecast # testes de um app (aqui COM o prefixo apps.)
python manage.py test apps.forecast.tests.MinhaClasse.test_metodo   # um teste
```

## Arquitetura

Cada módulo da plataforma (definido no PDF) é um app Django dentro do pacote `apps/`:

| App | Módulo | Rota |
|---|---|---|
| `apps/core` | Home/dashboard e página Sobre | `/` |
| `apps/accounts` | Autenticação (login/logout/registro, `django.contrib.auth`) | `/contas/` |
| `apps/forecast` | Forecast Engine (previsões D+1/D+7) | `/forecast/` |
| `apps/monitor` | Ops Monitor (saúde operacional) | `/monitor/` |
| `apps/alerts` | Risk Alert Center | `/alertas/` |
| `apps/copilot` | Agent Copilot (chatbot) | `/copilot/` |
| `apps/reports` | Reporting Hub | `/relatorios/` |

Convenções que atravessam vários arquivos:

- Apps ficam em `apps/<nome>/` e o `name` no `apps.py` é `apps.<nome>` — registre novos apps assim em `INSTALLED_APPS` e inclua as rotas em `config/urls.py`.
- Cada app tem `urls.py` próprio com `app_name` definido; use URLs namespaced (`{% url 'forecast:index' %}`).
- Templates de app em `apps/<app>/templates/<app>/`; estáticos de app em `apps/<app>/static/<app>/css|js/`. Layout global (`base.html`, `navbar.html`, `footer.html`, `components/`) em `templates/`; CSS/JS globais em `static/`.
- Todo template estende `templates/base.html` e injeta CSS/JS específicos via blocos `extra_css`/`extra_js`. Tokens de design (cores, espaçamento) estão em `static/css/variables.css` — use as variáveis CSS em vez de valores fixos.
- JS global expõe helpers em `window.SentinelOps` (`static/js/utils.js`); os JS de módulo consomem esses helpers e hoje geram dados mock no cliente.
- Interface em português brasileiro (`LANGUAGE_CODE = 'pt-br'`, fuso `America/Sao_Paulo`); código (identificadores) em inglês.
- Uploads vão para `media/` (ignorado pelo git via `.gitkeep`); banco de dev é SQLite, a arquitetura alvo prevê PostgreSQL.
