# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

SentinelOps é uma plataforma de AIOps (challenge FIAP 2026) para previsão de incidentes operacionais de TI. A visão completa está no PDF `documentos/EC_Sprint_2_2TSCPW_arqsolucao__sentinelops.pdf`: previsões D+1/D+7, detecção de padrões e alertas preventivos, com stack alvo Django + Celery/Redis, Airflow, MLflow, PostgreSQL e Prometheus/Grafana.

**Estado atual:** ferramenta interna (não um site público) para os operadores de TI da Locaweb. A navegação é uma sidebar fixa (`templates/sidebar.html`) com 16 telas; a topbar exibe sempre o selo "Dados até &lt;data&gt;", injetado por `apps.core.context_processors.referencia`.

A plataforma roda sobre o **dataset real da Locaweb** (`documentos/LW-DATASET.xlsx`, aba `Dataset Geral`), carregado por `python manage.py importar_dataset`: 122.543 incidentes, 51 produtos, 17 equipes, 9.171 itens de configuração e 12 famílias de sinal, com referência temporal em 31/12/2025. Não há mais geração sintética de histórico — a simulação de cenários (abaixo) é a única escrita fora da importação.

Três decisões atravessam o código inteiro e explicam a maior parte das escolhas:

1. **Ruído vs. sinal.** O campo `Entrou para KPI?` separa o ruído de monitoramento (chamados automáticos, fechados sem intervenção) do que de fato é medido por OLA. Quase toda métrica da plataforma é calculada sobre `total_kpi`, não sobre o volume bruto — misturar os dois produz MTTR artificialmente baixo e participação de críticos acima de 100%.
2. **Âncora temporal.** O extrato termina em 31/12/2025, então `timezone.now()` cairia num vazio. `apps/core/tempo.py` ancora o "agora" da plataforma no último incidente aberto (`referencia_temporal()`, em cache de 5 min) e todas as janelas partem dele.
3. **Números auditáveis.** Nenhum score aparece sozinho: Health Score vem com a contribuição de cada fator, Risk Radar com probabilidade e impacto separados, anomalias com a faixa esperada, previsões com intervalo derivado dos resíduos do backtest. Ao mexer nesses serviços, mantenha a evidência junto do número.

Ainda **não** existem: API, Celery/Redis, Airflow, MLflow e Postgres (o banco de dev é SQLite).

## Comandos

```powershell
python manage.py runserver          # servidor de desenvolvimento (http://127.0.0.1:8000)
python manage.py migrate            # aplicar migrações (SQLite: db.sqlite3, ignorado pelo git)
python manage.py importar_dataset   # carrega o LW-DATASET.xlsx e materializa as métricas diárias
python manage.py importar_dataset --arquivo caminho.xlsx --aba "Dataset Geral" --manter-simulacoes
python manage.py makemigrations core forecast ...   # nomes dos apps SEM o prefixo apps.
python manage.py test               # todos os testes (66)
python manage.py test apps.intelligence              # testes de um app (aqui COM o prefixo apps.)
python manage.py test apps.intelligence.tests.CorrelationTest.test_cadeia_conta_pares_na_janela_do_mesmo_ativo
```

`importar_dataset` apaga e recarrega tudo (incidentes, dimensões e `MetricaDiaria`) dentro de uma transação, e invalida o cache da referência temporal no fim. Leva alguns minutos e exige `pandas` e `openpyxl` — que **não** estão em `requirements.txt` (hoje só o Django), então precisam ser instalados à parte.

## Arquitetura

### Dados (`apps/core`)

`apps/core/models.py` concentra o domínio, espelhando as colunas do dataset:

- `Equipe`, `Produto`, `ItemConfiguracao` — dimensões anonimizadas (`Grupo designado`, `Produto`, `Item de configuração`).
- `FamiliaSinal` — família de sintoma derivada da `Descrição resumida`, o único campo textual não anonimizado. As regras (regex, avaliadas em ordem) estão em `apps/core/taxonomia.py`; é o que permite agrupar ~18 mil descrições em 12 famílias analisáveis.
- `Incidente` — o fato. Prioridade P1–P5, `entrou_kpi`, `kpi_violado`, `incidente_pai` (blast radius), `aberto_por`, `status`, `origem` (`dataset` ou `simulacao`).
- `MetricaDiaria` — agregados diários materializados na importação, por dimensão (`global`, `produto`, `equipe`, `familia`, `categoria`). **Toda leitura de série passa por aqui**: agregar 122k incidentes por request deixaria as telas lentas e faria módulos diferentes discordarem entre si. Críticos e MTTR são contados apenas sobre incidentes elegíveis a KPI.

### Camada de inferência (`apps/intelligence`)

App sem modelos próprios — só serviços, consumidos por todos os outros apps:

| Módulo | O que entrega |
|---|---|
| `services/base.py` | Única porta de leitura de `MetricaDiaria` (séries densas, agregados, estatística básica). Todo serviço novo parte daqui. |
| `services/health.py` | Operational Health Score 0–100 = 100 − Σ(penalidade × peso) de 5 fatores (OLA 30, volume 25, severidade 20, tendência 15, MTTR 10). |
| `services/deltas.py` | "What changed" — período vs. período anterior, com direção e sentimento. |
| `services/risk.py` | Risk Radar: `risco = probabilidade (violação histórica) × impacto (volume × severidade)`. Ordena por risco, **não** por volume. |
| `services/anomaly.py` | z-score contra baseline móvel de 21 dias da própria série. |
| `services/correlation.py` | Blast radius (`incidente_pai`) e cadeia de sintomas (co-ocorrência de famílias no mesmo ativo em até 60 min). |
| `services/insights.py` | `prioridade = impacto × urgência × confiança × alcance`; só o topo chega à tela. |
| `services/similarity.py` | Incidentes semelhantes por match categórico ponderado (sem ML), perfil de resolução e hipótese de causa. |
| `services/briefing.py` | Narrativa "o que mudou → por que importa → o que fazer" montada a partir dos serviços acima. |

`apps/intelligence/templatetags/sentinel.py` desenha os gráficos em **SVG inline** (`dial`, `sparkline`, `barra_contribuicao`, `grafo_blast`, `timeline`) — não há biblioteca de charts no cliente.

O Correlation Engine é o ponto mais caro da plataforma: `cadeia_de_sintomas` varre ~69k eventos e guarda o resultado em cache por 5 min, com chave versionada pela contagem de incidentes (`_versao_dados`) — assim a simulação invalida o cache sozinha, sem hook. Ao mexer em `tempestades`, cuidado com o N+1: iterar `pai.filhos` sobre instâncias parcialmente carregadas fazia um SELECT por desdobramento (o teste `test_tempestades_nao_disparam_uma_consulta_por_filho` trava isso em 3 queries).

### Apps de tela

| App | Módulo | Rotas |
|---|---|---|
| `apps/core` | Command Center, Data Sources, Sobre | `/`, `/dados/`, `/sobre/` |
| `apps/accounts` | Autenticação (`django.contrib.auth`) | `/contas/` |
| `apps/monitor` | Live Operations, Service Health, Incident Intelligence + drill-downs e simulação | `/monitor/`, `/monitor/saude/[<codigo>/]`, `/monitor/incidentes/[<numero>/]`, `/monitor/simular/` |
| `apps/intelligence` | Risk Radar, Anomaly Detection, Correlation Engine, Operational Insights | `/inteligencia/risco/`, `/anomalias/`, `/correlacao/`, `/insights/` |
| `apps/forecast` | Forecast Engine e Model Performance | `/forecast/`, `/forecast/modelo/` |
| `apps/alerts` | Alert Center e Decision Center | `/alertas/`, `/alertas/decisao/` |
| `apps/copilot` | Sentinel Copilot | `/copilot/` |
| `apps/reports` | Daily Report e Executive Report | `/relatorios/`, `/relatorios/executivo/` |

Notas sobre alguns deles:

- **Forecast** (`apps/forecast/services.py`): média histórica do dia da semana × fator de tendência recente, deliberadamente simples e interpretável. O intervalo de previsão sai do desvio dos resíduos do próprio backtest, e `model_performance` compara o MAE do modelo com o de um baseline ingênuo.
- **Alerts** (`apps/alerts/services.py`): todo alerta responde o quê / por que importa / impacto / ação. Inclui a regra ruído vs. sinal, que evita alarme falso quando o volume bruto sobe só por ruído de monitoramento.
- **Copilot** (`apps/copilot/services.py`): não é LLM nem resposta fixa — reconhece intenção por palavra-chave e monta a resposta chamando os mesmos serviços das telas, então nunca diverge do que a plataforma mostra. Formulário GET (`?q=`), sem JS.
- **Simulação** (`apps/monitor/simulacao.py`, rota `monitor:simular`): injeta um pico de incidentes ou uma degradação com blast radius, marcados com `origem=simulacao`, e permite limpar tudo depois. **Ela grava apenas `Incidente`, não recalcula `MetricaDiaria`** — o efeito aparece de imediato nas telas que leem incidentes direto (Live Operations, itens recorrentes, Correlation, alertas de crítico ativo, detalhe de incidente), mas não nos números agregados (Health Score, Risk Radar, Forecast, anomalias), que só mudam com uma nova importação.

### Convenções

- Apps ficam em `apps/<nome>/` e o `name` no `apps.py` é `apps.<nome>` — registre novos apps assim em `INSTALLED_APPS` e inclua as rotas em `config/urls.py`.
- Cada app tem `urls.py` próprio com `app_name`; use URLs namespaced (`{% url 'intelligence:risk' %}`).
- Templates de app em `apps/<app>/templates/<app>/`; estáticos de app em `apps/<app>/static/<app>/css|js/`. Layout global (`base.html`, `sidebar.html`, `components/`) em `templates/`; CSS global em `static/css/` (`variables.css` → `global.css` → `components.css`).
- Todo template estende `templates/base.html` e injeta CSS/JS via `extra_css`/`extra_js`. Use as variáveis CSS de `variables.css` em vez de valores fixos; os componentes de UI (card, chip, tile, secao, insight, risco, cadeia, explain) já existem em `components.css`.
- Interface em português brasileiro (`LANGUAGE_CODE = 'pt-br'`, fuso `America/Sao_Paulo`); código (identificadores) em inglês, exceto o domínio, que segue o vocabulário do dataset em português.
- As telas são renderizadas no servidor. `static/js/main.js` cuida só de interação (drawer da sidebar, link ativo, fechar alertas, animação de entrada dos cards) — não há geração de dados no cliente.
- Sobraram da estrutura anterior alguns arquivos não referenciados (`core/home.html`, `monitor/index.html`, `alerts/index.html`, `reports/index.html` e os JS de módulo de accounts/alerts/copilot/monitor/reports). Não estenda esses arquivos; as telas vivas são as da tabela acima.

### Testes

`apps/core/tests.py` guarda as **fábricas compartilhadas** (`criar_incidente`, `criar_metrica`, `criar_serie`, `criar_serie_valores`, `montar_operacao`) e a classe base `TesteComCache`, que limpa o cache entre casos — sem isso, a âncora temporal de um teste vaza para o seguinte. Os cenários usam números redondos de propósito, para que o valor esperado possa ser conferido na mão.

Cobertura atual: `apps/core` (âncora temporal, propriedades de `Incidente`, e um smoke test que exige status 200 em todas as telas da sidebar **com e sem dados** — o estado vazio é onde aparecem divisões por zero) e `apps/intelligence` (os nove serviços da camada de inferência e as quatro telas). Os `tests.py` dos demais apps ainda são stubs.
