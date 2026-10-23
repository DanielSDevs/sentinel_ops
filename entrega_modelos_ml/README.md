# SentinelOps — Código dos modelos de Machine Learning

Um notebook por etapa: a análise exploratória, a engenharia de features e um notebook para cada
modelo, com o treino completo escrito nas células (divisão temporal, candidatos, competição,
calibração, avaliação e explicabilidade). Os notebooks não dependem de nenhum outro arquivo de
código do projeto.

## Ordem de execução

| # | Notebook | Técnica | Métrica principal |
|---|---|---|---|
| 01 | `01_analise_exploratoria.ipynb` | EDA: janela utilizável, ruído × sinal, sazonalidade, desbalanceamento do OLA | — |
| 02 | `02_engenharia_de_features.ipynb` | Painel diário, features de série temporal, features por incidente, checagens de vazamento | — |
| 03 | `03_model_d1_xgboost.ipynb` | Previsão de volume D+1 (regressão) | MAE / MASE |
| 04 | `04_model_d7_lightgbm.ipynb` | Previsão de volume D+1..D+7 (regressão multi-horizonte) | MAE / MASE |
| 05 | `05_model_pico_random_forest.ipynb` | Risco de pico operacional (classificação, calibrada) | PR-AUC |
| 06 | `06_model_ola_regressao_logistica.ipynb` | Risco de violação de OLA por incidente (classificação desbalanceada, calibrada) | PR-AUC |
| 07 | `07_model_anomalia_isolation_forest.ipynb` | Detecção de anomalias multivariada (não supervisionado) | concordância com z-score |
| 08 | `08_model_cluster_kmeans.ipynb` | Clusterização de produtos por perfil operacional (não supervisionado) | silhueta |

O **02** grava `dados/base.pkl`; os notebooks **03 a 08** leem esse arquivo e podem ser executados
em qualquer ordem depois dele. Cada modelo treinado é gravado em `artefatos/`.

## Sobre o nome dos arquivos

O algoritmo no nome é o que **venceu** no último treino da plataforma. Nos notebooks de modelos
supervisionados (03 a 06), todos os candidatos competem de novo a cada execução: duas baselines,
Ridge ou regressão logística, Random Forest, XGBoost e LightGBM. Vence quem tiver a melhor métrica
na validação. A escolha não está fixada no código.

## Protocolo comum aos modelos supervisionados

1. Divisão **temporal** em treino / validação / teste, cortada pela **data do alvo** (não pela
   data de origem), para que nenhum horizonte enxergue dentro da validação.
2. Cada configuração de cada algoritmo treina **só no treino**.
3. O vencedor é escolhido pela **validação**; o teste não participa da decisão.
4. O vencedor é reajustado em treino + validação (volume) ou calibrado na validação (classificação).
5. O **teste** é lido uma única vez, no fim.
6. Os modelos de volume ainda passam por **walk-forward** de 12 origens semanais.

## Como executar

Os notebooks leem o banco SQLite da plataforma, já populado a partir do dataset da Locaweb:

```powershell
# na raiz do projeto SentinelOps
python manage.py migrate
python manage.py importar_dataset          # exige documentos/LW-DATASET.xlsx

cd entrega_modelos_ml
pip install -r requirements.txt
jupyter lab
```

Por padrão o banco é procurado em `../db.sqlite3` (raiz do projeto). Para outro caminho, defina a
variável de ambiente `SQLITE_PATH` antes de abrir o Jupyter.

O dataset (`LW-DATASET.xlsx`) é material do cliente e **não** acompanha esta pasta.

## Relação com a plataforma

O código destas células foi transcrito do pipeline que a plataforma usa (`ml/src/`): mesmas
features, mesma divisão, mesmas grades de hiperparâmetros e mesmo protocolo de seleção. Os
artefatos gravados aqui ficam em `artefatos/` e **não** substituem os modelos servidos pela
aplicação (`ml/models/`).
