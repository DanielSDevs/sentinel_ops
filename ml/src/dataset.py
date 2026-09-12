"""Leitura dos dados de treino direto do banco da plataforma.

Treinar sobre o mesmo SQLite que as telas servem (e não sobre o .xlsx original) evita a classe
de bug mais chata de MLOps: modelo e aplicação discordarem sobre o que é um incidente. O
`importar_dataset` já resolveu tipagem, taxonomia de sinais e a regra de KPI; o pipeline de ML
parte exatamente daí.
"""

import sqlite3

import pandas as pd

from . import config

COLUNAS_INCIDENTE = """
    i.id,
    i.numero,
    i.prioridade,
    i.categoria,
    i.subcategoria,
    i.descricao,
    i.aberto_em,
    i.resolvido_em,
    i.duracao_seg,
    i.aberto_por,
    i.status,
    i.entrou_kpi,
    i.kpi_violado,
    i.incidente_pai_id,
    p.codigo  AS produto,
    e.nome    AS equipe,
    c.codigo  AS item_configuracao,
    f.slug    AS familia_sinal
"""

SQL_INCIDENTES = f"""
SELECT {COLUNAS_INCIDENTE}
FROM core_incidente i
LEFT JOIN core_produto p          ON p.id = i.produto_id
LEFT JOIN core_equipe e           ON e.id = i.equipe_id
LEFT JOIN core_itemconfiguracao c ON c.id = i.item_configuracao_id
LEFT JOIN core_familiasinal f     ON f.id = i.familia_sinal_id
WHERE i.origem = 'dataset'
"""

SQL_METRICAS = """
SELECT data, dimensao, chave, total, total_kpi, violacoes, criticos, resolvidos, soma_mttr_seg
FROM core_metricadiaria
"""


def conectar(banco=None):
    """Abre o banco por caminho, ou aceita uma conexão DBAPI já aberta.

    A segunda forma existe para o Django: a camada de inferência entrega a própria conexão da
    aplicação, e com isso o pipeline lê o banco que o Django está usando naquele processo —
    inclusive o banco temporário dos testes. Resolver o caminho por conta própria faria a
    inferência ler a base de produção durante a suíte, que é o tipo de acoplamento que só
    aparece quando um teste passa por motivo errado.
    """
    if hasattr(banco, 'cursor'):
        return banco

    caminho = banco or config.BANCO
    if not caminho.exists():
        raise FileNotFoundError(
            f'Banco não encontrado em {caminho}. Rode `python manage.py importar_dataset` antes '
            f'de treinar — o pipeline de ML lê os dados já normalizados pela plataforma.'
        )
    return sqlite3.connect(f'file:{caminho}?mode=ro', uri=True)


def _consultar(sql, banco):
    conexao = conectar(banco)
    if conexao is banco:            # conexão emprestada: quem abriu é quem fecha
        return pd.read_sql_query(sql, conexao)
    with conexao:
        return pd.read_sql_query(sql, conexao)


def _para_local(serie):
    """Django grava UTC no SQLite; a operação (e o dataset) vive em horário de São Paulo."""
    return (
        pd.to_datetime(serie, format='mixed', utc=True)
        .dt.tz_convert(config.FUSO)
        .dt.tz_localize(None)
    )


def carregar_incidentes(banco=None):
    """Fato bruto: um registro por incidente, com as dimensões já resolvidas."""
    df = _consultar(SQL_INCIDENTES, banco)
    if df.empty:
        return df.assign(data=pd.Series(dtype='datetime64[ns]'))

    df['aberto_em'] = _para_local(df['aberto_em'])
    df['resolvido_em'] = _para_local(df['resolvido_em'])
    df['data'] = df['aberto_em'].dt.normalize()
    df['entrou_kpi'] = df['entrou_kpi'].astype(bool)
    # `kpi_violado` é nulo em quem não entrou no KPI — manter como NA em vez de virar False
    # preserva a diferença entre "não violou" e "nem foi medido".
    df['kpi_violado'] = df['kpi_violado'].astype('boolean')

    df['mttr_min'] = (df['resolvido_em'] - df['aberto_em']).dt.total_seconds() / 60
    df.loc[~df['entrou_kpi'], 'mttr_min'] = pd.NA

    for coluna in ('produto', 'equipe', 'item_configuracao', 'familia_sinal', 'categoria'):
        df[coluna] = df[coluna].fillna('').astype(str)

    return df


def carregar_metricas_diarias(banco=None):
    """Agregados materializados pela plataforma — usados para conferir o painel de features."""
    df = _consultar(SQL_METRICAS, banco)
    df['data'] = pd.to_datetime(df['data'])
    return df


def resumo(df_incidentes):
    """Números de capa do dataset, usados na EDA e na tela de Data Sources."""
    return {
        'incidentes': int(len(df_incidentes)),
        'elegiveis_kpi': int(df_incidentes['entrou_kpi'].sum()),
        'violacoes': int((df_incidentes['kpi_violado'] == True).sum()),  # noqa: E712
        'produtos': int(df_incidentes['produto'].replace('', pd.NA).nunique()),
        'equipes': int(df_incidentes['equipe'].replace('', pd.NA).nunique()),
        'itens_configuracao': int(df_incidentes['item_configuracao'].replace('', pd.NA).nunique()),
        'familias': int(df_incidentes['familia_sinal'].replace('', pd.NA).nunique()),
        'inicio': df_incidentes['data'].min(),
        'fim': df_incidentes['data'].max(),
    }
