# SentinelOps

Plataforma de AIOps para previsão de incidentes operacionais de TI — Challenge FIAP 2026 / Locaweb.

SentinelOps é uma ferramenta interna para operadores de TI. Ela lê um extrato fechado do ITSM e
responde, em sequência, quatro perguntas: **como a operação está agora, o que mudou, o que vem pela
frente e o que fazer a respeito**. Não é um painel genérico de BI — cada tela existe para sustentar
uma decisão específica, e todo número exibido carrega junto a evidência que o produziu.

---

## A tese: ruído versus sinal

O achado que organiza a plataforma inteira veio da análise do dataset real. O campo
`Entrou para KPI?` separa duas populações que não podem ser somadas:

| | Ruído de monitoramento | Sinal elegível a KPI |
|---|---|---|
| Origem | Chamados automáticos | Trabalhados pela operação |
| Prioridade | Predominantemente baixa | P1–P3 |
| Ritmo | Quase 24x7 | Sazonalidade de dia útil |
| Medido por OLA | Não | Sim |
| Volume | ~97 mil incidentes | 25.600 incidentes |

Quase toda métrica da plataforma é calculada sobre o volume elegível, não sobre o bruto. Misturar
os dois produz MTTR artificialmente baixo (chamados automáticos fecham em segundos) e participação
de críticos acima de 100%.

A distinção rende uma regra concreta de alerta: quando o volume total sobe mais de 20% mas o volume
elegível não acompanha, o Alert Center reporta o evento como informativo — explicitamente não
escalável — em vez de mobilizar a operação por um problema que não existe.

> Uma medição independente confirmou a separação: no fim de 2025 o volume elegível a KPI caiu para
> cerca de um quarto do normal, enquanto o volume bruto se manteve em ~900/dia. Monitoramento
> automático não tira férias; gente tira.

---

## Stack

- **Django 5.2** · Python 3.13 · SQLite em desenvolvimento
- Renderização no servidor; gráficos em **SVG inline** gerados por template tags — sem biblioteca
  de charts no cliente
- **Nenhuma dependência de machine learning em produção.** Todos os modelos são estatística
  explícita e auditável (`statsmodels` e `scikit-learn` aparecem apenas no notebook de análise)
- Arquitetura-alvo do PDF de solução (Celery/Redis, Airflow, MLflow, PostgreSQL, Prometheus/Grafana)
  ainda não implementada

---

## Como rodar

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pandas openpyxl          # necessários apenas para importar o dataset

python manage.py migrate
python manage.py importar_dataset    # ver nota sobre o dataset abaixo
python manage.py runserver           # http://127.0.0.1:8000
```

### O dataset

O extrato da Locaweb (`LW-DATASET.xlsx`, ~15 MB) **não está versionado** — é material do cliente e
não deve ir para um repositório público. Coloque o arquivo em `documentos/LW-DATASET.xlsx`, ou
aponte o caminho na importação:

```powershell
python manage.py importar_dataset --arquivo caminho/para/LW-DATASET.xlsx --aba "Dataset Geral"
```

A importação apaga e recarrega tudo dentro de uma transação: cria as dimensões, grava os
incidentes, resolve os vínculos de incidente-pai e materializa as métricas diárias. Leva alguns
minutos e carrega 122.543 incidentes.

### Comandos

```powershell
python manage.py test                        # 66 testes, ~2s
python manage.py test apps.intelligence      # testes de um app
python manage.py importar_dataset --manter-simulacoes
```

---

## As telas

Navegação por sidebar fixa, 16 telas. A topbar exibe sempre o selo "Dados até 31/12/2025" — a
plataforma analisa um extrato fechado, e deixar isso visível evita que alguém leia "hoje" como o
dia corrente.

| Grupo | Tela | Rota | Responde |
|---|---|---|---|
| — | Command Center | `/` | Como está, o que mudou, o que vem e o que fazer |
| Monitor | Live Operations | `/monitor/` | O que está acontecendo agora |
| Monitor | Service Health | `/monitor/saude/` | Quais produtos estão mais degradados |
| Monitor | Incident Intelligence | `/monitor/incidentes/` | Onde está o incidente que procuro |
| Intelligence | Forecast Engine | `/forecast/` | Quanto volume esperar em D+1 e D+7 |
| Intelligence | Risk Radar | `/inteligencia/risco/` | Onde o próximo problema tem mais chance de aparecer |
| Intelligence | Anomaly Detection | `/inteligencia/anomalias/` | O que fugiu do padrão e quando |
| Intelligence | Correlation Engine | `/inteligencia/correlacao/` | O que acontece junto e o que vem depois do quê |
| Intelligence | Operational Insights | `/inteligencia/insights/` | Do que eu não sabia que precisava saber |
| Ação | Decision Center | `/alertas/decisao/` | O que eu faço primeiro |
| Ação | Alert Center | `/alertas/` | O que precisa de atenção agora |
| IA | Sentinel Copilot | `/copilot/` | Perguntas em linguagem natural sobre a operação |
| Reports | Daily / Executive | `/relatorios/` | O dia anterior; o mês, sem detalhe técnico |
| Sistema | Data Sources | `/dados/` | O que foi carregado — e o que a base não tem |
| Sistema | Model Performance | `/forecast/modelo/` | Dá para confiar na previsão |

O Ops Monitor tem um controle de **simulação de cenários** que injeta um pico de incidentes ou uma
degradação com blast radius, marcados com `origem=simulacao` e removíveis depois.

---

## A camada de inferência

`apps/intelligence` não tem modelos próprios — concentra os serviços que todos os outros apps
consomem. Toda leitura de série passa por `services/base.py`, sobre a tabela de métricas diárias
materializadas: é isso que mantém as telas rápidas e garante que dois módulos nunca cheguem a
números diferentes para a mesma pergunta.

| Serviço | O que entrega |
|---|---|
| `health` | Score 0–100 = 100 − Σ(penalidade × peso) de cinco fatores, cada um com sua contribuição visível |
| `deltas` | Período contra período anterior, com direção e sentimento |
| `risk` | `risco = probabilidade × impacto` — ordena por risco, **não** por volume |
| `anomaly` | z-score contra baseline móvel de 21 dias da própria série |
| `correlation` | Blast radius (vínculo incidente-pai) e cadeia de sintomas por co-ocorrência |
| `insights` | `prioridade = impacto × urgência × confiança × alcance`; só o topo chega à tela |
| `similarity` | Incidentes semelhantes por match categórico ponderado, sem ML |
| `briefing` | Narrativa "o que mudou → por que importa → o que fazer" |

O Forecast Engine (`apps/forecast`) prevê volume por média histórica do dia da semana × fator de
tendência, com intervalo derivado dos resíduos do próprio backtest.

---

## Acurácia dos modelos

A tela Model Performance publica o resultado de um holdout cronológico. Um estudo com **avaliação
em origem móvel** (239 janelas ao longo de 2025, seleção de hiperparâmetros separada da medição)
mediu o erro real e identificou onde melhorar:

| Modelo | MAE | MASE | Vence o atual |
|---|---|---|---|
| Nível × perfil semanal + fator de feriado | **11,71** | 0,83 | 96/119 origens |
| Holt-Winters | 12,57 | 0,89 | 87/119 |
| Baseline sazonal (repete D-7) | 14,16 | 1,00 | 66/119 |
| Modelo atual, em produção | 15,31 | 1,08 | — |

Três conclusões do estudo: o holdout fixo cai inteiro sobre o Natal e reporta o pior caso do ano;
separar nível de sazonalidade e reconhecer feriados reduz o erro em 23%; e Holt-Winters não vence a
decomposição manual, o que dispensa uma dependência nova em produção.

Para a previsão de violação de OLA, com taxa base de 0,97%, a conclusão foi que o problema é o
enquadramento e não o algoritmo: uma pontuação por taxas históricas multidimensionais, sem treino,
empata em ROC-AUC e supera em PR-AUC os modelos de ML do notebook — e, usada como **ordenação de
fila** em vez de classificação, captura 39% das violações revisando 6,6% do volume.

O estudo completo, com protocolo, código e ressalvas, está em
[`documentos/SentinelOps_Documentacao_Tecnica.docx`](documentos/SentinelOps_Documentacao_Tecnica.docx).

---

## Testes

```powershell
python manage.py test
```

66 testes. Os cenários são montados à mão, nunca sobre o dataset real: as asserções precisam saber
exatamente quantos incidentes existem em cada dia para verificar médias, z-scores e janelas. As
fábricas compartilhadas ficam em `apps/core/tests.py`.

Dois testes merecem destaque por travarem regressões concretas: um exige que o Correlation Engine
resolva as tempestades em três queries (antes disparava uma por desdobramento), e outro garante que
o cache da cadeia de sintomas expire quando a simulação injeta incidentes. Há também um smoke test
que exige status 200 em todas as telas **com e sem dados** — o estado vazio é onde aparecem
divisões por zero.

---

## Estrutura

```
apps/
  core/           modelos de domínio, âncora temporal, taxonomia, importação, Command Center
  accounts/       autenticação
  intelligence/   camada de inferência (sem modelos próprios)
  monitor/        operação ao vivo, saúde de serviços, incidentes, simulação
  forecast/       previsão e avaliação do modelo
  alerts/         alertas acionáveis e fila de decisão
  copilot/        assistente que consulta a camada de inferência
  reports/        relatório diário e executivo
analytics/        notebook de análise e modelagem (Sprint 3)
documentos/       documentação técnica gerada; o dataset fica aqui, fora do versionamento
templates/        base.html, sidebar.html, components/
static/           variables.css → global.css → components.css
```

---

## Documentação

- [`documentos/SentinelOps_Documentacao_Tecnica.docx`](documentos/SentinelOps_Documentacao_Tecnica.docx)
  — arquitetura, features, telas, o código e a lógica de cada modelo, e o estudo de acurácia
- [`CLAUDE.md`](CLAUDE.md) — guia de contribuição e convenções do repositório
- `analytics/EC_Sprint_3_SentinelOps_ML.ipynb` — análise exploratória e modelagem da Sprint 3

## Limitações conhecidas

- A simulação de cenários grava apenas incidentes e não recalcula as métricas diárias: o efeito
  aparece nas telas que leem incidentes direto, mas não nos números agregados.
- Sem API, Celery/Redis, Airflow, MLflow ou PostgreSQL — previstos para sprints futuras.
- O extrato não traz impacto financeiro, métricas de infraestrutura nem topologia de dependências.
  A tela Data Sources declara essas ausências explicitamente.
