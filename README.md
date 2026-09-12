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

- **Django 5.2** · Python 3.13+ · SQLite em desenvolvimento e em produção
- Renderização no servidor; gráficos em **SVG inline** gerados por template tags — sem biblioteca
  de charts no cliente
- **Machine Learning em produção**: scikit-learn, XGBoost, LightGBM e SHAP. A aplicação carrega
  os artefatos de `ml/models/` e serve previsão, risco de OLA, probabilidade de pico e anomalia
  a cada request
- Arquitetura-alvo do PDF de solução (Celery/Redis, Airflow, MLflow, PostgreSQL, Prometheus/Grafana)
  ainda não implementada — o model registry é em arquivo (`ml/models/*.json` + `.pkl`)

---

## Como rodar

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python manage.py migrate
python manage.py importar_dataset    # ver nota sobre o dataset abaixo
python manage.py treinar_modelos     # treina os 6 modelos de ML (~6 min) e grava ml/models/
python manage.py createsuperuser     # toda tela exige login (ou cadastre-se em /contas/registro/)
python manage.py runserver           # http://127.0.0.1:8000
```

Sem o passo de treino a plataforma sobe normalmente, mas as telas de previsão mostram "modelo
ainda não treinado" em vez de número: não existe caminho no código que preencha o buraco com uma
média e chame de previsão.

### Deploy

A plataforma publica no **Azure App Service** (Linux, Python 3.13) pelo GitHub Actions a cada push
na `main`, com a base SQLite no armazenamento persistente do App Service. Passo a passo em
[`DEPLOY.md`](DEPLOY.md).

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
python manage.py test                        # 109 testes, ~9s
python manage.py test apps.ml                # testes de um app
python manage.py importar_dataset --manter-simulacoes

python manage.py treinar_modelos             # retreina tudo e registra as previsões de teste
python manage.py registrar_previsoes         # publica a previsão de hoje e confere as vencidas
```

---

## As telas

Navegação por sidebar fixa, 18 telas. A topbar exibe sempre o selo "Dados até 31/12/2025" — a
plataforma analisa um extrato fechado, e deixar isso visível evita que alguém leia "hoje" como o
dia corrente.

| Grupo | Tela | Rota | Responde |
|---|---|---|---|
| — | Command Center | `/` | Como está, o que mudou, o que vem e o que fazer |
| Monitor | Live Operations | `/monitor/` | O que está acontecendo agora |
| Monitor | Service Health | `/monitor/saude/` | Quais produtos estão mais degradados |
| Monitor | Incident Intelligence | `/monitor/incidentes/` | Onde está o incidente que procuro |
| Machine Learning | Forecast Engine | `/forecast/` | Quanto volume esperar em D+1 e D+7, onde o pico deve cair e por quê |
| Machine Learning | Modelos de ML | `/modelos/` | Que modelos existem, com que algoritmo, métrica e notebook |
| Machine Learning | Histórico de previsões | `/modelos/previsoes/` | O modelo está acertando ao longo do tempo? |
| Intelligence | Risk Radar | `/inteligencia/risco/` | Onde o próximo problema tem mais chance de aparecer |
| Intelligence | Anomaly Detection | `/inteligencia/anomalias/` | O que fugiu do padrão e quando |
| Intelligence | Correlation Engine | `/inteligencia/correlacao/` | O que acontece junto e o que vem depois do quê |
| Intelligence | Operational Insights | `/inteligencia/insights/` | Do que eu não sabia que precisava saber |
| Ação | Decision Center | `/alertas/decisao/` | O que eu faço primeiro |
| Ação | Alert Center | `/alertas/` | O que precisa de atenção agora |
| Reports | Daily / Executive | `/relatorios/` | O dia anterior; o mês, sem detalhe técnico |
| Sistema | Data Sources | `/dados/` | O que foi carregado — e o que a base não tem |
| Sistema | Model Performance | `/forecast/modelo/` | Dá para confiar na previsão — modelo × baseline, walk-forward, erro por entidade |

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

Esses serviços continuam sendo estatística explícita e auditável — é o que sustenta as telas de
diagnóstico ("como está", "o que mudou"). A parte **preditiva** ("o que vem") é outra camada, com
modelos treinados, descrita a seguir.

---

## Machine Learning

Os resultados preditivos da plataforma vêm de modelos treinados, não de fórmula. O pipeline vive
em `ml/`, é reproduzível por um comando (`python manage.py treinar_modelos`) e está inteiro nos
notebooks de `modelos_ml/` — que chamam as **mesmas funções** de `ml/src/`, de modo que o
artefato do notebook é literalmente o artefato que a aplicação serve.

### Os modelos

| Modelo | Objetivo | Algoritmo vencedor | Métrica | Resultado (teste) |
|---|---|---|---|---|
| `model_d1` | Volume de incidentes elegíveis em D+1 | **XGBoost** | MAE | **1,86** (baseline 2,24 · **+17,1%**) |
| `model_d7` | Volume diário de D+1 a D+7 | **LightGBM** | MAE | **2,14** (baseline 2,24 · **+4,8%**) |
| `model_pico` | Probabilidade de o dia entrar no topo 20% da entidade | **Random Forest** calibrado | PR-AUC | **0,224** (taxa-base 0,105 · lift 2,1×) · ROC-AUC 0,720 |
| `model_ola` | Probabilidade de um incidente violar o OLA | **Regressão logística** calibrada | PR-AUC | **0,238** (taxa-base 0,013 · **lift 18×**) · ROC-AUC 0,904 |
| `model_anomalia` | Dias fora do padrão multivariado | Isolation Forest | — | não supervisionado (ver abaixo) |
| `model_cluster` | Grupos de produtos por perfil operacional | K-Means | Silhueta | 0,321 |

Todos os números saem de `ml/models/*.json`, escritos pelo treino. A tela **Modelos de Machine
Learning** (`/modelos/`) lê exatamente esses arquivos — nenhuma métrica é digitada à mão em lugar
nenhum do código.

### O protocolo

O mesmo para todos os modelos supervisionados:

1. cada família de algoritmo treina cada configuração da sua grade **só com o treino**;
2. o vencedor sai da **validação** — Ridge, Random Forest, XGBoost, LightGBM e duas baselines
   disputam na mesma partição e na mesma métrica;
3. o vencedor é reajustado em treino + validação;
4. o **teste** é lido uma única vez, no fim, e é o número publicado;
5. os modelos de volume ainda passam por um **walk-forward** de 12 origens semanais.

O corte das partições é pela **data-alvo**, não pela data de origem: uma linha de origem 20/11 com
horizonte 7 enxerga o dia 27, e cortar pela origem deixaria esse dia dentro do treino. Isso é
testado (`apps/ml/tests.py::SplitsTest`), junto com a verificação de que nenhuma feature carrega o
próprio alvo e de que nenhum campo de desfecho (duração, resolução, status, código de fechamento)
entra no modelo de OLA.

### Features

83 features numéricas para os modelos de volume, todas calculadas com dados até o dia de origem:
defasagens (1 a 28 dias), janelas agregadas (soma, média, desvio, mín/máx de 3 a 30 dias), dinâmica
(variação semana a semana, razão 7d/28d, aceleração), sazonalidade (mesmo dia da semana nas últimas
4 semanas), calendário (dia da semana, dia do mês, mês, semana do ano, fim de semana e **feriados
nacionais**, incluindo os móveis), severidade (P2, P3, participação de críticos), OLA e MTTR,
alcance (ativos e famílias de sintoma distintos) e escala da entidade.

O modelo de OLA usa 36 features por incidente: atributos do chamado, calendário, carga da equipe no
momento da abertura e taxas históricas **expandidas e deslocadas** — a linha N enxerga as N−1
anteriores daquela equipe, nunca a si mesma.

Ficou de fora, deliberadamente, o índice de tempo cru: árvore não extrapola, e um índice que só
cresce vira porta para o modelo decorar o período em vez de aprender o padrão.

### Painel, e não uma série só

O treino não usa uma série global única — usa um **painel** de 55 entidades (global, 20 produtos,
12 equipes, 12 categorias, P2 e P3, e os 8 ativos de maior volume) × ~350 dias × 7 horizontes =
**122.507 linhas**. Com uma linha por dia
haveria ~350 exemplos para 83 features. Com o painel, o mesmo mapa defasagem → alvo é aprendido
sobre dezenas de milhares de linhas, e o modelo passa a prever **por grupo** — que é o que a
operação precisa para agir.

Por isso o MAE agregado (1,86) e o MAE da série global (13,7) convivem: são escalas diferentes, e a
tela Model Performance mostra os dois separados. Na série global, o ganho do D+1 sobre a baseline
é de **20,2%**.

Prever por prioridade não exigiu modelo novo: **P2 e P3 são duas séries a mais do mesmo painel**.
O mesmo vale para os ativos de maior volume. É o que permite responder "quantos P2 esperar amanhã"
mantendo a coerência com a previsão total.

### Uma restrição de deploy dentro da competição

Toda Random Forest da grade tem `max_depth` limitado. Não é ajuste fino: sem teto, a floresta
sobre 110 mil linhas produziu um artefato de **325 MB** — que funciona, mas não cabe no
versionamento nem no pacote de publicação, e teria de ser desserializado a cada processo novo.

O limite entra na **definição de todos os candidatos, antes da competição**, e não como desempate
depois de ver o resultado; escolher o modelo pela conveniência de tamanho depois do fato seria
maquiar a comparação. O efeito colateral foi bom: com profundidade limitada a Random Forest
regularizou e o PR-AUC do modelo de pico **subiu** de 0,212 para 0,224. O artefato final tem 39 MB,
e `treinar_modelos` avisa se algum passar de 50 MB.

### Calibração de probabilidade

Os dois classificadores treinam com peso de classe (violação de OLA é ~1% dos elegíveis), o que
distorce a escala da saída: a probabilidade média prevista dava **34%** onde a taxa real é 1,3%.
Sem corrigir isso, compor esse número com o volume previsto produziria "risco de 100%" todo dia.

A saída passa por calibração de Platt ajustada na validação. O efeito está registrado no cartão do
modelo: Brier de **0,177 → 0,012**, probabilidade média de **34% → 1,28%** contra taxa real de
**1,29%** no teste. O ranking dos casos não muda (a ROC-AUC é a mesma); o que muda é o número poder
ser lido como probabilidade.

### Do incidente para o dia

O risco de perda de OLA que aparece no Command Center compõe **dois modelos treinados**, sem
nenhuma constante arbitrada no meio:

```
P(ao menos 1 violação amanhã) = 1 − (1 − p) ^ volume_previsto
```

`p` vem do classificador de OLA (probabilidade de um incidente violar); `volume_previsto` vem do
modelo D+1. É por isso que uma equipe com probabilidade individual baixa pode aparecer em risco
alto: volume suficiente transforma evento raro em quase certo.

### Explicabilidade

Cada previsão abre a lista dos fatores que a produziram — contribuições de Shapley calculadas sobre
o próprio modelo (`TreeExplainer` nas árvores; forma fechada `coeficiente × (valor padronizado −
média)` no modelo linear, que é o valor exato de Shapley sob independência). No cartão de cada
modelo há ainda a **importância por permutação**, medida na validação, que responde "sem essa
feature, quanto o modelo piora?".

Exemplo real da previsão de 01/01/2026: partindo de uma estimativa base de 5,8 incidentes, a média
do mesmo dia da semana nas últimas 4 semanas somou +11,6 e **"é feriado" subtraiu −5,4**.

### O que não deu certo, e por quê

O modelo **D+7 vence a baseline no agregado (+4,8%), mas perde na série global do teste**
(−8,0%), e isso está na tela, não escondido.

A causa é de dados: a série em regime operacional começa em **13/01/2025** (antes disso o extrato
registra menos de um incidente elegível por dia — é backfill, não operação), e o teste cai sobre a
virada de ano. O modelo nunca viu um dezembro, então não tem como saber que o volume despenca entre
o Natal e o Ano-Novo; o viés de −15,1/dia mostra exatamente a superestimação. No walk-forward, que
mede 12 origens distintas, o mesmo modelo fica em **MASE 0,84 e vence a baseline em 12 das 12
janelas**. O D+1, mais curto, sofre menos: MASE 0,83 no teste e 0,79 no walk-forward.

As duas leituras aparecem lado a lado na plataforma. Com mais um ciclo anual de histórico, é
exatamente o tipo de padrão que passa a ser aprendido.

O **Isolation Forest** também não recebe métrica supervisionada: não existe rótulo de "dia anômalo"
no dataset, e inventar uma AUC seria pior que não ter. O que se reporta é a concordância com o
z-score de 21 dias (1,7%) — baixa porque os dois olham coisas diferentes, e é isso que justifica
manter ambos.

### Os notebooks

```
modelos_ml/     um notebook por propósito, na ordem de execução:
  01_analise_exploratoria      o que os dados permitem prever
  02_engenharia_de_features    painel diário, features e clusterização de produtos
  03_previsao_volume_d1        model_d1
  04_previsao_volume_d7        model_d7
  05_risco_de_ola              model_ola
  06_deteccao_de_anomalias     model_anomalia (e o classificador de pico)
  07_comparacao_de_modelos     resultado consolidado

ml/
  src/          config · dataset · features · splits · metrics · models_zoo
                selecao · train · explain · registry
  models/       <modelo>.pkl (artefato) + <modelo>.json (cartão) + registry.json
  data/         previsões do conjunto de teste, exportadas pelo treino
```

Os notebooks foram **executados** e estão versionados com as saídas reais. Para rodá-los:

```powershell
pip install -r requirements-notebooks.txt
jupyter lab modelos_ml
```

O estudo estatístico que antecedeu esta etapa está em
[`documentos/SentinelOps_Documentacao_Tecnica.docx`](documentos/SentinelOps_Documentacao_Tecnica.docx).

---

## Testes

```powershell
python manage.py test
```

109 testes. Os cenários são montados à mão, nunca sobre o dataset real: as asserções precisam saber
exatamente quantos incidentes existem em cada dia para verificar médias, z-scores e janelas. As
fábricas compartilhadas ficam em `apps/core/tests.py`.

Dois testes merecem destaque por travarem regressões concretas: um exige que o Correlation Engine
resolva as tempestades em três queries (antes disparava uma por desdobramento), e outro garante que
o cache da cadeia de sintomas expire quando a simulação injeta incidentes. Há também um smoke test
que exige status 200 em todas as telas **com e sem dados** — o estado vazio é onde aparecem
divisões por zero.

Os testes de ML cobrem o que não quebra sozinho: que o alvo nunca aparece entre as features, que as
partições não se sobrepõem no tempo **em nenhum horizonte**, que as taxas históricas por incidente
não enxergam o próprio desfecho e que nenhum campo pós-resolução entrou no modelo de OLA. Vazamento
temporal não levanta exceção — ele produz uma métrica boa demais que ninguém questiona, e por isso
precisa de teste. Há ainda um treino real, pequeno, sobre série determinística, que trava a fiação
da seleção de modelos, e a verificação de que a plataforma devolve vazio (não um número) quando não
há artefato treinado.

---

## Estrutura

```
apps/
  core/           modelos de domínio, âncora temporal, taxonomia, importação, Command Center
  accounts/       autenticação
  intelligence/   camada de inferência estatística (sem modelos próprios)
  ml/             registry, inferência dos modelos treinados e histórico de previsões
  monitor/        operação ao vivo, saúde de serviços, incidentes, simulação
  forecast/       leitura dos modelos de volume (Forecast Engine e Model Performance)
  alerts/         alertas acionáveis e fila de decisão
  copilot/        desligado (entrada "Por vir" na sidebar); código preservado
  reports/        relatório diário e executivo
modelos_ml/       os 7 notebooks, executados, um por propósito de modelo
ml/
  src/            pipeline de ML: dados → features → treino → avaliação → registry
  models/         artefatos .pkl + cartão .json de cada modelo
  data/           previsões do conjunto de teste exportadas pelo treino
analytics/        notebook estatístico da Sprint 3 (anterior ao pipeline de ML)
documentos/       documentação técnica gerada; o dataset fica aqui, fora do versionamento
templates/        base.html, sidebar.html, components/
static/           variables.css → global.css → components.css
```

A separação segue a cadeia da arquitetura: **ingestão** (`apps/core/management`), **tratamento e
features** (`ml/src/dataset.py`, `ml/src/features.py`), **treino e avaliação** (`ml/src/train.py`,
`selecao.py`, `metrics.py`), **registry** (`ml/src/registry.py` → `ml/models/`), **inferência**
(`apps/ml/services/inferencia.py`) e **visualização** (os apps de tela). Nenhuma dessas camadas
recalcula o que a anterior já definiu — em particular, a inferência usa a mesma função de features
do treino, que é o que evita training/serving skew.

---

## Documentação

- `modelos_ml/` — os sete notebooks do pipeline de ML, executados e com as saídas reais, um por
  propósito. Comece pelo `01_analise_exploratoria.ipynb` (o que os dados permitem prever) e pelo
  `07_comparacao_de_modelos.ipynb` (o resultado consolidado)
- [`documentos/SentinelOps_Documentacao_Tecnica.docx`](documentos/SentinelOps_Documentacao_Tecnica.docx)
  — arquitetura, features, telas e a lógica de cada serviço da camada de inferência estatística
- [`CLAUDE.md`](CLAUDE.md) — guia de contribuição e convenções do repositório
- `analytics/EC_Sprint_3_SentinelOps_ML.ipynb` — o estudo estatístico da Sprint 3, anterior ao
  pipeline de ML; mantido como registro de como se chegou até aqui

## Limitações conhecidas

- **Um único ciclo anual de histórico.** A série em regime operacional começa em 13/01/2025, então
  nenhum modelo viu um dezembro antes de ser avaliado num. É a causa direta do D+7 perder da
  baseline no teste (detalhado acima) e o limite mais duro do projeto.
- **Violação de OLA é evento raro** (238 casos em 25.156 elegíveis, ~1%). O classificador entrega
  ROC-AUC 0,90 e lift de 18× em PR-AUC, mas com precisão de 0,33 no limiar escolhido: serve para
  **ordenar a fila**, não para automatizar decisão.
- O treino é manual (`manage.py treinar_modelos`), sem reavaliação automática nem detecção de
  drift. O histórico de previsões existe justamente para tornar a degradação visível.
- A simulação de cenários grava apenas incidentes e não recalcula as métricas diárias: o efeito
  aparece nas telas que leem incidentes direto, mas não nos números agregados.
- Sem API, Celery/Redis, Airflow, MLflow ou PostgreSQL — previstos para sprints futuras. O model
  registry é em arquivo.
- O extrato não traz impacto financeiro, métricas de infraestrutura nem topologia de dependências.
  A tela Data Sources declara essas ausências explicitamente.
