# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão geral

SentinelOps é uma plataforma de AIOps (challenge FIAP 2026) para previsão de incidentes operacionais de TI. A visão completa está no PDF `documentos/EC_Sprint_2_2TSCPW_arqsolucao__sentinelops.pdf`: previsões D+1/D+7, detecção de padrões e alertas preventivos, com stack alvo Django + Celery/Redis, Airflow, MLflow, PostgreSQL e Prometheus/Grafana.

**Estado atual:** ferramenta interna (não um site público) para os operadores de TI da Locaweb. A navegação é uma sidebar fixa (`templates/sidebar.html`) com 18 telas; a topbar exibe sempre o selo "Dados até &lt;data&gt;", injetado por `apps.core.context_processors.referencia`.

A plataforma roda sobre o **dataset real da Locaweb** (`documentos/LW-DATASET.xlsx`, aba `Dataset Geral`), carregado por `python manage.py importar_dataset`: 122.543 incidentes, 51 produtos, 17 equipes, 9.171 itens de configuração e 12 famílias de sinal, com referência temporal em 31/12/2025. Não há mais geração sintética de histórico — a simulação de cenários (abaixo) é a única escrita fora da importação.

A camada preditiva é **Machine Learning treinado**, não fórmula: `ml/` contém o pipeline
(features → treino → avaliação → registry) e `ml/models/` os artefatos que a aplicação carrega em
tempo de request. Detalhes na seção "Machine Learning" abaixo.

Quatro decisões atravessam o código inteiro e explicam a maior parte das escolhas:

1. **Ruído vs. sinal.** O campo `Entrou para KPI?` separa o ruído de monitoramento (chamados automáticos, fechados sem intervenção) do que de fato é medido por OLA. Quase toda métrica da plataforma é calculada sobre `total_kpi`, não sobre o volume bruto — misturar os dois produz MTTR artificialmente baixo e participação de críticos acima de 100%.
2. **Âncora temporal.** O extrato termina em 31/12/2025, então `timezone.now()` cairia num vazio. `apps/core/tempo.py` ancora o "agora" da plataforma no último incidente aberto (`referencia_temporal()`, em cache de 5 min) e todas as janelas partem dele.
3. **Números auditáveis.** Nenhum score aparece sozinho: Health Score vem com a contribuição de cada fator, Risk Radar com probabilidade e impacto separados, anomalias com a faixa esperada, previsões com intervalo medido fora da amostra e com as contribuições SHAP que as produziram. Ao mexer nesses serviços, mantenha a evidência junto do número.
4. **Sem modelo, sem número.** Se o artefato não existe em `ml/models/`, a tela diz "modelo ainda não treinado" e não mostra previsão. Nunca introduza fallback para média, regra fixa ou valor de exemplo: um número inventado com cara de previsão é pior que campo vazio, e é exatamente o que o requisito de não simular resultados proíbe.

Ainda **não** existem: API, Celery/Redis, Airflow, MLflow e Postgres (o banco é SQLite, também em produção).

**Deploy:** Azure App Service (Linux, Python 3.13) via GitHub Actions (`.github/workflows/azure-deploy.yml`), com a base SQLite em `/home/data/` e `startup.sh` como comando de inicialização. O passo a passo está em `DEPLOY.md`. `config/settings.py` lê tudo de variáveis de ambiente e entra em modo produção quando `WEBSITE_HOSTNAME` existe (App Service) ou `DJANGO_PRODUCAO=1`; sem nenhuma variável, roda em dev como sempre.

## Comandos

```powershell
python manage.py runserver          # servidor de desenvolvimento (http://127.0.0.1:8000)
python manage.py migrate            # aplicar migrações (SQLite: db.sqlite3, ignorado pelo git)
python manage.py importar_dataset   # carrega o LW-DATASET.xlsx e materializa as métricas diárias
python manage.py importar_dataset --arquivo caminho.xlsx --aba "Dataset Geral" --manter-simulacoes
python manage.py treinar_modelos    # treina os 6 modelos de ML (~6 min) e grava ml/models/
python manage.py registrar_previsoes            # publica a previsão de hoje no histórico
python manage.py registrar_previsoes --historico  # importa as previsões do conjunto de teste
python manage.py makemigrations core ml ...     # nomes dos apps SEM o prefixo apps.
python manage.py test               # todos os testes (114)
python manage.py test apps.ml                   # testes de um app (aqui COM o prefixo apps.)
python manage.py test apps.ml.tests.SplitsTest.test_corte_e_pela_data_alvo_e_nao_pela_origem
```

`treinar_modelos` lê o banco já importado, treina, grava artefato + cartão por modelo, registra as
previsões de teste no histórico e limpa o cache. Leva ~6 minutos e exige as dependências de ML do
`requirements.txt`.

`importar_dataset` apaga e recarrega tudo (incidentes, dimensões e `MetricaDiaria`) dentro de uma transação, e invalida o cache da referência temporal no fim. Leva alguns minutos e exige `pandas` e `openpyxl` (já em `requirements.txt`).

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

`apps/intelligence/templatetags/sentinel.py` desenha os gráficos em **SVG inline** (`dial`, `sparkline`, `barra_contribuicao`, `grafo_blast`, `timeline`) — não há biblioteca de charts no cliente. O gráfico de previsão (`apps/ml/templatetags/modelos.py`) também chega pronto do servidor, mas emite uma faixa de captura por dia com os valores em `data-*`; `forecast/js/forecast.js` só move o cursor e preenche a legenda flutuante a partir desses atributos. Sem o JS o gráfico continua correto — só não responde ao ponteiro. Não use `preserveAspectRatio="none"` em gráfico com texto: era o que esticava a linha e achatava os rótulos na largura do card.

O Correlation Engine é o ponto mais caro da plataforma: `cadeia_de_sintomas` varre ~69k eventos e guarda o resultado em cache por 5 min, com chave versionada pela contagem de incidentes (`_versao_dados`) — assim a simulação invalida o cache sozinha, sem hook. Ao mexer em `tempestades`, cuidado com o N+1: iterar `pai.filhos` sobre instâncias parcialmente carregadas fazia um SELECT por desdobramento (o teste `test_tempestades_nao_disparam_uma_consulta_por_filho` trava isso em 3 queries).

### Machine Learning (`ml/` + `apps/ml`)

O pipeline vive **fora do Django**, em `ml/src/`, e é importado tanto pelos notebooks de `modelos_ml/` (um por propósito de modelo, na ordem de execução) quanto pela
aplicação. Essa separação é o que garante que o modelo do notebook é o modelo que a plataforma
serve — não há uma segunda implementação de feature em lugar nenhum.

| Módulo | Papel |
|---|---|
| `ml/src/dataset.py` | Lê incidentes e métricas do SQLite. Aceita caminho **ou conexão DBAPI** — a inferência passa a conexão do Django, e é isso que faz os testes rodarem no banco de teste. |
| `ml/src/features.py` | `painel_diario` (entidade × dia) e `construir_features` (entidade × dia × horizonte). Também `features_incidentes`, para o modelo de OLA. |
| `ml/src/splits.py` | Partições temporais **pela data-alvo**, walk-forward e detecção do início do regime operacional. |
| `ml/src/models_zoo.py` | Candidatos (Ridge, RF, XGBoost, LightGBM, logística) e as baselines, que competem como estimadores de verdade. |
| `ml/src/selecao.py` | O protocolo: treina no treino, escolhe na validação, reajusta, mede no teste uma vez só. |
| `ml/src/metrics.py` | MAE/RMSE/MAPE/R²/MASE e precisão/recall/F1/ROC-AUC/PR-AUC/Brier, com o glossário que a tela exibe. |
| `ml/src/train.py` | Uma função por modelo + `executar_tudo()`. É o que o comando e os notebooks chamam. |
| `ml/src/explain.py` | Importância por permutação, SHAP de árvore e o explicador linear em forma fechada. |
| `ml/src/registry.py` | `<modelo>.pkl` (artefato) + `<modelo>.json` (cartão legível e versionável). |

`apps/ml` é a ponte com o Django: `services/catalogo.py` lê os cartões para a tela de
transparência, `services/inferencia.py` carrega os artefatos e produz as previsões, e
`PrevisaoRegistrada` guarda o histórico previsão × real.

Pontos em que é fácil errar ao mexer aqui:

- **Corte pela `data_alvo`.** Cortar por `data_origem` deixa uma linha de horizonte 7 enxergando
  dentro da validação. Há teste travando isso.
- **Probabilidade precisa de calibração.** Os classificadores treinam com peso de classe, o que
  distorce a escala da saída (média prevista de 34% contra taxa real de 1,3%). `_calibrar` aplica
  Platt na validação; sem isso a composição do risco de OLA dá 100% todo dia.
- **Explicabilidade sai do estimador base**, não do invólucro calibrado — o calibrador não tem
  `feature_importances_`. `explain._estimador_final` desembrulha por nome de classe, e não por
  atributo, porque `RandomForest` também tem `estimator_` (o molde da árvore).
- **`tabela_previsoes()` prevê tudo de uma vez.** Chamar o modelo por entidade/horizonte fazia
  300+ chamadas por tela e levava 10s no Command Center.
- **Toda floresta tem `max_depth` limitado, e o artefato é gravado comprimido.** Sem teto, uma
  Random Forest sobre 110 mil linhas gerou um `.pkl` de 325 MB — que funciona, mas não cabe no
  repositório nem no pacote de deploy. O limite vale para todos os candidatos antes da competição;
  `treinar_modelos` avisa se algum artefato passar de 50 MB.
- O cache de inferência é versionado pela contagem de incidentes, como o do Correlation Engine.

### Apps de tela

| App | Módulo | Rotas |
|---|---|---|
| `apps/core` | Command Center, Data Sources, Sobre | `/`, `/dados/`, `/sobre/` |
| `apps/accounts` | Autenticação (`django.contrib.auth`) | `/contas/` |
| `apps/monitor` | Live Operations, Service Health, Incident Intelligence + drill-downs e simulação | `/monitor/`, `/monitor/saude/[<codigo>/]`, `/monitor/incidentes/[<numero>/]`, `/monitor/simular/` |
| `apps/intelligence` | Risk Radar, Anomaly Detection, Correlation Engine, Operational Insights | `/inteligencia/risco/`, `/anomalias/`, `/correlacao/`, `/insights/` |
| `apps/forecast` | Forecast Engine e Model Performance | `/forecast/`, `/forecast/modelo/` |
| `apps/ml` | Modelos de ML, cartão de cada modelo, histórico de previsões | `/modelos/`, `/modelos/<nome>/`, `/modelos/previsoes/` |
| `apps/alerts` | Alert Center e Decision Center | `/alertas/`, `/alertas/decisao/` |
| `apps/reports` | Daily Report e Executive Report | `/relatorios/`, `/relatorios/executivo/` |

Notas sobre alguns deles:

- **Forecast** (`apps/forecast/services.py`): não calcula previsão nenhuma — só organiza o que os modelos de `ml/models/` devolvem e escreve a leitura em português. A heurística que ocupava esse arquivo (nível × perfil de dia da semana) virou **baseline** dentro do pipeline de ML, medida na mesma partição e na mesma métrica que os modelos; é isso que sustenta a afirmação de que o ML acrescenta algo. A avaliação é **walk-forward** (12 origens de 7 dias) além do teste fixo, porque uma janela só bastava para o Natal dominar o MAE reportado.
- **Alerts** (`apps/alerts/services.py`): todo alerta responde o quê / por que importa / impacto / ação. Inclui a regra ruído vs. sinal, que evita alarme falso quando o volume bruto sobe só por ruído de monitoramento, e dois blocos preventivos vindos dos modelos (pico previsto e risco de OLA por equipe) — os únicos que falam no futuro, e por isso os únicos que carregam probabilidade junto.
- **Copilot** (`apps/copilot/`): **desligado**. Saiu do `INSTALLED_APPS` e de `config/urls.py`; na sidebar sobrou uma entrada desabilitada "Por vir", ao lado de outra para o Analista Operacional. O código foi preservado — reconhece intenção por palavra-chave e monta a resposta chamando os mesmos serviços das telas, via formulário GET (`?q=`), sem JS. Para religar: devolver `'apps.copilot'` ao `INSTALLED_APPS`, a rota em `config/urls.py`, o link na sidebar e `'copilot:index'` em `RotasTest.ROTAS`.
- **Grupos de comportamento** (`apps/monitor/perfis.py`, bloco no Service Health): a leitura
  operacional do `model_cluster`. O modelo devolve "grupo 0" e "grupo 1"; este módulo rotula cada
  grupo **a partir do próprio perfil** (volume, MTTR, regularidade, instabilidade) e calcula o
  contraste entre eles. O rótulo nunca é fixo no código — se o próximo treino inverter os grupos
  ou produzir três, o rótulo acompanha, e há teste travando isso.
- **Simulação** (`apps/monitor/simulacao.py`, rota `monitor:simular`): injeta um pico de incidentes ou uma degradação com blast radius, marcados com `origem=simulacao`, e permite limpar tudo depois. **Ela grava apenas `Incidente`, não recalcula `MetricaDiaria`** — o efeito aparece de imediato nas telas que leem incidentes direto (Live Operations, itens recorrentes, Correlation, alertas de crítico ativo, detalhe de incidente), mas não nos números agregados (Health Score, Risk Radar, Forecast, anomalias), que só mudam com uma nova importação.

### Convenções

- Apps ficam em `apps/<nome>/` e o `name` no `apps.py` é `apps.<nome>` — registre novos apps assim em `INSTALLED_APPS` e inclua as rotas em `config/urls.py`.
- Cada app tem `urls.py` próprio com `app_name`; use URLs namespaced (`{% url 'intelligence:risk' %}`).
- Templates de app em `apps/<app>/templates/<app>/`; estáticos de app em `apps/<app>/static/<app>/css|js/`. Layout global (`base.html`, `sidebar.html`, `components/`) em `templates/`; CSS global em `static/css/` (`variables.css` → `global.css` → `components.css`).
- Todo template estende `templates/base.html` e injeta CSS/JS via `extra_css`/`extra_js`. Use as variáveis CSS de `variables.css` em vez de valores fixos; os componentes de UI (card, chip, tile, secao, insight, risco, cadeia, explain) já existem em `components.css`.
- Interface em português brasileiro (`LANGUAGE_CODE = 'pt-br'`, fuso `America/Sao_Paulo`); código (identificadores) em inglês, exceto o domínio, que segue o vocabulário do dataset em português.
- **Toda rota exige login** (`LoginRequiredMiddleware`). Uma view nova já nasce protegida; só as que precisam ficar abertas (login, cadastro) levam `@login_not_required`. Testes que abrem telas herdam de `TesteDeTela` (em `apps/core/tests.py`), que já faz o login.
- As telas são renderizadas no servidor. `static/js/main.js` cuida só de interação (drawer da sidebar, link ativo, fechar alertas, animação de entrada dos cards) — não há geração de dados no cliente.
- Sobraram da estrutura anterior alguns arquivos não referenciados (`core/home.html`, `monitor/index.html`, `alerts/index.html`, `reports/index.html` e os JS de módulo de accounts/alerts/copilot/monitor/reports). Não estenda esses arquivos; as telas vivas são as da tabela acima.

### Testes

`apps/core/tests.py` guarda as **fábricas compartilhadas** (`criar_incidente`, `criar_metrica`, `criar_serie`, `criar_serie_valores`, `montar_operacao`) e a classe base `TesteComCache`, que limpa o cache entre casos — sem isso, a âncora temporal de um teste vaza para o seguinte. Os cenários usam números redondos de propósito, para que o valor esperado possa ser conferido na mão.

Cobertura atual: `apps/core` (âncora temporal, propriedades de `Incidente`, o bloqueio de acesso anônimo, e um smoke test que exige status 200 em todas as telas da sidebar **com e sem dados** — o estado vazio é onde aparecem divisões por zero), `apps/intelligence` (os nove serviços da camada de inferência e as quatro telas), `apps/forecast` (a camada de leitura e a narrativa, incluindo o caso "sem modelo treinado") e `apps/ml`. Os `tests.py` dos demais apps ainda são stubs.

Os testes de `apps/ml` merecem atenção porque cobrem o que não quebra sozinho: que o alvo nunca
aparece entre as features, que as partições não se sobrepõem no tempo em nenhum horizonte, que as
taxas históricas por incidente não enxergam o próprio desfecho, e que nenhum campo pós-resolução
entrou no modelo de OLA. Vazamento temporal não gera exceção — gera métrica boa demais que
ninguém questiona. Há também um treino real, pequeno, sobre série determinística, que trava a
fiação da seleção de modelos.
