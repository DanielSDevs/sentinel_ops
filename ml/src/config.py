"""Caminhos e constantes compartilhados por todo o pipeline de ML."""

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

ML_DIR = RAIZ / 'ml'
MODELOS_DIR = ML_DIR / 'models'
DADOS_DIR = ML_DIR / 'data'
NOTEBOOKS_DIR = ML_DIR / 'notebooks'
FIGURAS_DIR = ML_DIR / 'reports'

# O treino lê o mesmo banco que a plataforma serve: o que o modelo aprendeu é o que as telas
# mostram. `SQLITE_PATH` é a mesma variável que o config/settings.py usa no App Service.
BANCO = Path(os.environ.get('SQLITE_PATH', RAIZ / 'db.sqlite3'))

FUSO = 'America/Sao_Paulo'
SEMENTE = 42

# Horizontes previstos. D+1 tem modelo próprio; D+2..D+7 saem do modelo multi-horizonte.
HORIZONTE_MAX = 7

# Alvo das previsões de volume: incidentes elegíveis a KPI. O volume bruto inclui o ruído de
# monitoramento (chamados automáticos fechados sem intervenção), que não consome capacidade da
# operação e não é medido por OLA — prever esse ruído seria prever o comportamento de um robô.
ALVO_VOLUME = 'total_kpi'

# Tamanho das partições temporais (em dias, contados do fim da série para trás).
DIAS_TESTE = 28
DIAS_VALIDACAO = 28

# Mínimo de dias de histórico que uma entidade precisa ter para entrar no painel de treino.
MIN_DIAS_ENTIDADE = 120

# Quantas entidades de cada dimensão entram no painel (as maiores por volume elegível).
TOP_PRODUTOS = 20
TOP_EQUIPES = 12
TOP_CATEGORIAS = 12
# Itens de configuração são 9.171: só os maiores têm série diária densa o bastante para sustentar
# janelas de 28 dias. O resto entra no modelo como feature agregada (ativos distintos por dia),
# não como entidade a prever.
TOP_ITENS = 8


def garantir_diretorios():
    for caminho in (MODELOS_DIR, DADOS_DIR, FIGURAS_DIR):
        caminho.mkdir(parents=True, exist_ok=True)
