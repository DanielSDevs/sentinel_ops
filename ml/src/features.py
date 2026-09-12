"""Engenharia de features.

Duas etapas, propositalmente separadas:

1. `painel_diario` transforma 122k incidentes num painel (entidade × dia) com os agregados
   brutos — volume, severidade, OLA, MTTR, alcance.
2. `construir_features` transforma o painel na matriz de treino, uma linha por
   (entidade, dia de origem, horizonte), com o alvo em t+h.

**Painel, e não apenas a série global.** Só o histórico global daria ~1 linha por dia; com
produtos, equipes e categorias como entidades o modelo aprende o mesmo mapa lag→alvo sobre
dezenas de milhares de linhas, e ainda passa a saber prever *por grupo* — que é o que a
operação precisa para agir ("onde vai doer", não só "quanto vai doer").

**Regra anti-vazamento, aplicada por construção.** Toda feature de uma linha é calculada com
dados até o dia de origem `t` (inclusive), e o alvo é `t + h` com `h ≥ 1`. Não existe janela
centrada, nem estatística calculada sobre a série inteira, nem alvo transformado em feature.
Por isso não há índice de tempo cru entre as features: além de ser uma porta de vazamento de
regime, árvore não extrapola — o nível recente (`media_28`, `media_expandida`) cumpre esse
papel sem prometer o que o modelo não pode entregar fora da amostra.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import config

# Agregados calculados por entidade e dia.
COLUNAS_PAINEL = [
    'total', 'total_kpi', 'violacoes', 'criticos', 'resolvidos', 'soma_mttr_min',
    'p1', 'p2', 'p3', 'p4', 'p5', 'manual', 'monitoramento',
    'itens_distintos', 'familias_distintas',
]

LAGS = (0, 1, 2, 3, 4, 5, 6, 7, 13, 14, 20, 21, 27, 28)
JANELAS_SOMA = (1, 2, 3, 7, 14, 30)
JANELAS_MEDIA = (3, 7, 14, 28)
DIMENSOES = ('global', 'produto', 'equipe', 'categoria', 'prioridade', 'item')


def _pascoa(ano):
    """Algoritmo de Butcher — base dos feriados móveis brasileiros."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados_brasil(anos):
    """Feriados nacionais (fixos + móveis). Não há calendário de feriados no dataset.

    Carnaval e Corpus Christi não são feriado nacional por lei, mas na prática param a operação
    de TI brasileira tanto quanto os oficiais — entram como feriado para o modelo.
    """
    feriados = set()
    for ano in anos:
        pascoa = _pascoa(ano)
        feriados.update({
            date(ano, 1, 1), date(ano, 4, 21), date(ano, 5, 1), date(ano, 9, 7),
            date(ano, 10, 12), date(ano, 11, 2), date(ano, 11, 15), date(ano, 11, 20),
            date(ano, 12, 25),
            pascoa - timedelta(days=48),   # Carnaval (segunda)
            pascoa - timedelta(days=47),   # Carnaval (terça)
            pascoa - timedelta(days=2),    # Sexta-feira Santa
            pascoa + timedelta(days=60),   # Corpus Christi
        })
    return feriados


def _agregar(grupo_cols, df):
    """Agregados de um recorte do fato. Segue as mesmas regras da plataforma.

    MTTR e severidade contam apenas incidentes elegíveis a KPI: chamado automático fecha em
    segundos sem intervenção humana e derrubaria o MTTR médio para perto de zero.
    """
    elegivel = df['entrou_kpi']
    base = df.assign(
        _kpi=elegivel.astype(int),
        _violacao=df['kpi_violado'].fillna(False).astype(bool).astype(int),
        _critico=(elegivel & df['prioridade'].isin([1, 2])).astype(int),
        _resolvido=(elegivel & df['resolvido_em'].notna()).astype(int),
        _mttr=df['mttr_min'].where(elegivel).fillna(0.0),
        _manual=(df['aberto_por'] == 'manual').astype(int),
        _monitoramento=(df['aberto_por'] == 'monitoramento').astype(int),
        **{f'_p{p}': (elegivel & (df['prioridade'] == p)).astype(int) for p in range(1, 6)},
    )
    agregado = base.groupby(grupo_cols, observed=True).agg(
        total=('numero', 'size'),
        total_kpi=('_kpi', 'sum'),
        violacoes=('_violacao', 'sum'),
        criticos=('_critico', 'sum'),
        resolvidos=('_resolvido', 'sum'),
        soma_mttr_min=('_mttr', 'sum'),
        p1=('_p1', 'sum'), p2=('_p2', 'sum'), p3=('_p3', 'sum'),
        p4=('_p4', 'sum'), p5=('_p5', 'sum'),
        manual=('_manual', 'sum'),
        monitoramento=('_monitoramento', 'sum'),
        itens_distintos=('item_configuracao', 'nunique'),
        familias_distintas=('familia_sinal', 'nunique'),
    )
    return agregado.reset_index()


def _top_entidades(df, coluna, quantidade):
    contagem = (
        df[df['entrou_kpi'] & (df[coluna] != '')]
        .groupby(coluna, observed=True)['numero'].size()
        .sort_values(ascending=False)
    )
    return list(contagem.head(quantidade).index)


def painel_diario(incidentes, top_produtos=config.TOP_PRODUTOS, top_equipes=config.TOP_EQUIPES,
                  top_categorias=config.TOP_CATEGORIAS, top_itens=config.TOP_ITENS,
                  min_dias=config.MIN_DIAS_ENTIDADE):
    """Painel (dimensão, chave, data) → agregados, densificado sobre todo o calendário.

    As dimensões são os recortes pelos quais a operação decide: produto, equipe, categoria,
    prioridade e os ativos de maior volume. Prever por prioridade é o que permite responder
    "quantos P2 esperar amanhã" sem um modelo separado — P2 e P3 são apenas mais duas séries do
    mesmo painel, com o mesmo modelo.

    Entidades sem histórico suficiente ficam de fora: uma série que existe há 3 semanas não tem
    como sustentar features de 28 dias, e completar com zero ensinaria o modelo a prever
    silêncio onde só faltou dado.
    """
    incidentes = incidentes.assign(
        prioridade_rotulo='P' + incidentes['prioridade'].astype(int).astype(str),
    )
    partes = [_agregar(['data'], incidentes).assign(dimensao='global', chave='')]

    recortes = (
        ('produto', 'produto', top_produtos),
        ('equipe', 'equipe', top_equipes),
        ('categoria', 'categoria', top_categorias),
        # Só as prioridades que de fato entram no KPI aparecem: `_top_entidades` conta sobre
        # incidentes elegíveis, e P1/P4/P5 não são medidas por OLA neste extrato.
        ('prioridade', 'prioridade_rotulo', 5),
        ('item', 'item_configuracao', top_itens),
    )
    for dimensao, coluna, quantidade in recortes:
        chaves = _top_entidades(incidentes, coluna, quantidade)
        recorte = incidentes[incidentes[coluna].isin(chaves)]
        if recorte.empty:
            continue
        parte = _agregar([coluna, 'data'], recorte).rename(columns={coluna: 'chave'})
        partes.append(parte.assign(dimensao=dimensao))

    painel = pd.concat(partes, ignore_index=True)

    # Densificação: todo dia do calendário existe para toda entidade. Dia sem incidente é zero,
    # não um buraco — buraco vira média inflada e lag deslocado.
    calendario = pd.date_range(painel['data'].min(), painel['data'].max(), freq='D')
    indice = pd.MultiIndex.from_product(
        [
            pd.MultiIndex.from_frame(painel[['dimensao', 'chave']].drop_duplicates()),
            calendario,
        ],
        names=['entidade', 'data'],
    )
    denso = (
        painel.set_index(['dimensao', 'chave', 'data'])
        .reindex(pd.MultiIndex.from_tuples(
            [(d, c, dt) for (d, c), dt in indice], names=['dimensao', 'chave', 'data'],
        ))
        .fillna(0)
        .reset_index()
    )

    if min_dias:
        # Primeiro dia com atividade por entidade: antes disso a série é zero artificial.
        primeiro = (
            denso[denso['total'] > 0].groupby(['dimensao', 'chave'])['data'].min()
            .rename('primeiro_dia')
        )
        denso = denso.merge(primeiro, on=['dimensao', 'chave'], how='left')
        denso = denso[denso['data'] >= denso['primeiro_dia']]
        dias = denso.groupby(['dimensao', 'chave'])['data'].size().rename('dias')
        denso = denso.merge(dias, on=['dimensao', 'chave'], how='left')
        denso = denso[denso['dias'] >= min_dias].drop(columns=['primeiro_dia', 'dias'])

    denso[COLUNAS_PAINEL] = denso[COLUNAS_PAINEL].astype(float)
    denso['mttr_medio'] = np.where(
        denso['resolvidos'] > 0, denso['soma_mttr_min'] / denso['resolvidos'].replace(0, np.nan), 0.0,
    )
    denso['taxa_violacao'] = np.where(
        denso['total_kpi'] > 0, denso['violacoes'] / denso['total_kpi'].replace(0, np.nan), 0.0,
    )
    return denso.sort_values(['dimensao', 'chave', 'data']).reset_index(drop=True)


def _features_calendario(datas):
    """Calendário do dia previsto. É a única informação do futuro legítima: já se sabe hoje."""
    dow = datas.dt.dayofweek
    feriados = feriados_brasil(range(datas.dt.year.min() - 1, datas.dt.year.max() + 2))
    eh_feriado = datas.dt.date.isin(feriados)
    return pd.DataFrame({
        'dow': dow,
        'dia_mes': datas.dt.day,
        'mes': datas.dt.month,
        'semana_ano': datas.dt.isocalendar().week.astype(int),
        'fim_de_semana': (dow >= 5).astype(int),
        'feriado': eh_feriado.astype(int),
        'vespera_feriado': (datas + pd.Timedelta(days=1)).dt.date.isin(feriados).astype(int),
        'pos_feriado': (datas - pd.Timedelta(days=1)).dt.date.isin(feriados).astype(int),
        'dow_sin': np.sin(2 * np.pi * dow / 7),
        'dow_cos': np.cos(2 * np.pi * dow / 7),
        'mes_sin': np.sin(2 * np.pi * datas.dt.month / 12),
        'mes_cos': np.cos(2 * np.pi * datas.dt.month / 12),
    }, index=datas.index)


def _historico_entidade(grupo, alvo):
    """Features de histórico de uma entidade. Tudo termina no dia de origem `t`."""
    s = grupo[alvo]
    saida = {}

    for k in LAGS:
        saida[f'lag_{k}'] = s.shift(k)
    for janela in JANELAS_SOMA:
        saida[f'soma_{janela}'] = s.rolling(janela, min_periods=janela).sum()
    for janela in JANELAS_MEDIA:
        saida[f'media_{janela}'] = s.rolling(janela, min_periods=janela).mean()
    for janela in (7, 14, 28):
        saida[f'desvio_{janela}'] = s.rolling(janela, min_periods=janela).std()
    saida['min_7'] = s.rolling(7, min_periods=7).min()
    saida['max_7'] = s.rolling(7, min_periods=7).max()
    saida['max_28'] = s.rolling(28, min_periods=28).max()

    soma_7 = saida['soma_7']
    saida['var_7_vs_7'] = (soma_7 - soma_7.shift(7)) / soma_7.shift(7).replace(0, np.nan)
    saida['razao_7_28'] = saida['media_7'] / saida['media_28'].replace(0, np.nan)
    saida['delta_media_7_14'] = saida['media_7'] - saida['media_14']
    saida['cv_28'] = saida['desvio_28'] / saida['media_28'].replace(0, np.nan)
    saida['dias_ativos_28'] = (s > 0).rolling(28, min_periods=28).sum()
    # Escala da entidade sem olhar o futuro: média acumulada até o dia de origem.
    saida['media_expandida'] = s.expanding(min_periods=7).mean()

    operacionais = {
        'criticos': (7, 28), 'p2': (7, 28), 'p3': (7, 28), 'violacoes': (7, 28),
        'total': (7,), 'monitoramento': (7,), 'itens_distintos': (7,), 'familias_distintas': (7,),
        'mttr_medio': (7, 28), 'taxa_violacao': (7, 28), 'resolvidos': (7,),
    }
    for coluna, janelas in operacionais.items():
        for janela in janelas:
            saida[f'{coluna}_media_{janela}'] = (
                grupo[coluna].rolling(janela, min_periods=janela).mean()
            )

    quadro = pd.DataFrame(saida, index=grupo.index)
    quadro['share_criticos_7'] = quadro['criticos_media_7'] / quadro['media_7'].replace(0, np.nan)
    quadro['share_p2_7'] = quadro['p2_media_7'] / quadro['media_7'].replace(0, np.nan)
    quadro['share_p3_7'] = quadro['p3_media_7'] / quadro['media_7'].replace(0, np.nan)
    quadro['share_ruido_7'] = (
        quadro['monitoramento_media_7'] / quadro['total_media_7'].replace(0, np.nan)
    )
    quadro['delta_mttr'] = quadro['mttr_medio_media_7'] - quadro['mttr_medio_media_28']
    quadro['delta_taxa_violacao'] = (
        quadro['taxa_violacao_media_7'] - quadro['taxa_violacao_media_28']
    )
    return quadro


def historico_painel(painel, alvo=config.ALVO_VOLUME):
    """Painel + features de histórico por entidade, sem alvo ainda.

    Sai daqui tanto a matriz de previsão de volume quanto o contexto operacional que o modelo
    de OLA anexa a cada incidente — uma definição só de "como andava essa entidade".
    """
    painel = painel.sort_values(['dimensao', 'chave', 'data'])
    chaves = ['dimensao', 'chave']

    historico = (
        painel.groupby(chaves, group_keys=False, observed=True)
        .apply(lambda g: _historico_entidade(g, alvo), include_groups=False)
    )
    base = pd.concat([painel[chaves + ['data']], historico], axis=1)

    # Escala relativa ao global: distingue "produto grande com queda" de "produto pequeno".
    global_28 = (
        base[base['dimensao'] == 'global'].set_index('data')['media_28'].rename('global_media_28')
    )
    base = base.merge(global_28, left_on='data', right_index=True, how='left')
    base['share_global_28'] = base['media_28'] / base['global_media_28'].replace(0, np.nan)
    base = base.drop(columns=['global_media_28'])

    for dimensao in DIMENSOES:
        base[f'dim_{dimensao}'] = (base['dimensao'] == dimensao).astype(int)
    return base


def construir_features(painel, horizontes=range(1, config.HORIZONTE_MAX + 1),
                       alvo=config.ALVO_VOLUME, apenas_com_alvo=True, origens=None):
    """Matriz de treino: uma linha por (entidade, origem, horizonte).

    Colunas de alvo (`alvo_*`) e de identificação (`dimensao`, `chave`, `data_origem`,
    `data_alvo`) saem separadas das features em `separar`.

    `apenas_com_alvo=False` mantém as linhas cujo alvo ainda não aconteceu — são exatamente as
    que a plataforma usa para prever o futuro, montadas pelo mesmo código que montou o treino.
    Reaproveitar a função é o que garante que a feature nº 47 significa a mesma coisa nos dois
    lados; duplicar essa lógica na camada de inferência é a origem clássica do training/serving
    skew.

    `origens` restringe quais dias de origem entram na matriz. O histórico continua sendo
    calculado sobre a série inteira (as janelas de 28 dias dependem disso); o que muda é quantas
    linhas são montadas depois. A inferência só precisa do último dia, e montar os 350 seria
    jogar fora 99% do trabalho.
    """
    chaves = ['dimensao', 'chave']
    painel = painel.sort_values(chaves + ['data'])
    base = historico_painel(painel, alvo)
    if origens is not None:
        base = base[base['data'].isin(pd.DatetimeIndex(origens))]

    # Alvos futuros por entidade, um bloco por horizonte.
    alvos = painel.set_index(chaves + ['data'])[[alvo, 'violacoes', 'criticos', 'total']]
    linhas = []
    for h in horizontes:
        bloco = base.copy()
        bloco['horizonte'] = h
        bloco['data_origem'] = bloco['data']
        bloco['data_alvo'] = bloco['data'] + pd.Timedelta(days=h)

        # Sazonal conhecida na origem: mesmo dia da semana das semanas anteriores.
        serie = painel.set_index(chaves + ['data'])[alvo]
        for semanas in (1, 2, 3, 4):
            deslocamento = 7 * semanas - h
            if deslocamento < 0:
                bloco[f'mesmo_dow_{semanas}'] = np.nan
                continue
            indice = pd.MultiIndex.from_arrays([
                bloco['dimensao'], bloco['chave'],
                bloco['data_origem'] - pd.Timedelta(days=deslocamento),
            ])
            bloco[f'mesmo_dow_{semanas}'] = serie.reindex(indice).to_numpy()
        bloco['media_mesmo_dow_4'] = bloco[
            [f'mesmo_dow_{s}' for s in (1, 2, 3, 4)]
        ].mean(axis=1)

        calendario = _features_calendario(bloco['data_alvo'])
        bloco = pd.concat([bloco, calendario], axis=1)
        bloco['dow_origem'] = bloco['data_origem'].dt.dayofweek

        indice_alvo = pd.MultiIndex.from_arrays(
            [bloco['dimensao'], bloco['chave'], bloco['data_alvo']],
        )
        futuros = alvos.reindex(indice_alvo)
        bloco['alvo'] = futuros[alvo].to_numpy()
        bloco['alvo_violacoes'] = futuros['violacoes'].to_numpy()
        bloco['alvo_criticos'] = futuros['criticos'].to_numpy()
        bloco['alvo_total'] = futuros['total'].to_numpy()
        linhas.append(bloco)

    matriz = pd.concat(linhas, ignore_index=True).drop(columns=['data'])
    if apenas_com_alvo:
        matriz = matriz[matriz['alvo'].notna()]
    # Linhas sem histórico completo (início de cada série) saem: imputar zero aqui ensinaria
    # um padrão que não existe.
    matriz = matriz.dropna(subset=['media_28', 'soma_30', 'mesmo_dow_4', 'media_expandida'])
    return matriz.reset_index(drop=True)


# ---------------------------------------------------------------------------------------------
# Features por incidente — base do modelo de risco de violação de OLA.
# ---------------------------------------------------------------------------------------------

# Metas de OLA por prioridade, em minutos (mesmo dicionário de dados que a plataforma usa).
SLA_MINUTOS = {1: 240, 2: 240, 3: 720, 4: 1440, 5: 5760}

# Contexto da entidade anexado a cada incidente, sempre do dia ANTERIOR ao da abertura.
CONTEXTO_EQUIPE = [
    'media_7', 'media_28', 'razao_7_28', 'p2_media_7', 'share_criticos_7',
    'taxa_violacao_media_28', 'violacoes_media_28', 'mttr_medio_media_7', 'delta_mttr',
]
CONTEXTO_PRODUTO = ['media_7', 'razao_7_28', 'taxa_violacao_media_28', 'share_criticos_7']

SUAVIZACAO = 50.0


def _taxa_expandida(df, coluna, alvo='violou', suavizacao=SUAVIZACAO):
    """Taxa histórica de violação de uma dimensão, vista apenas com o passado de cada linha.

    Um `groupby().expanding()` deslocado em 1: a linha N enxerga as N−1 anteriores daquela
    equipe/produto, nunca a si mesma nem o futuro. A suavização puxa entidade com pouca
    amostra para a taxa global corrente — sem ela, um produto com 2 incidentes e 1 violação
    entraria no modelo com "taxa de 50%".
    """
    ordenado = df.sort_values('aberto_em')
    grupo = ordenado.groupby(coluna, observed=True)[alvo]
    soma = grupo.cumsum() - ordenado[alvo]
    quantidade = grupo.cumcount()
    prior = (ordenado[alvo].cumsum() - ordenado[alvo]) / np.maximum(
        np.arange(len(ordenado)), 1,
    )
    taxa = (soma + suavizacao * prior) / (quantidade + suavizacao)
    return taxa.reindex(df.index)


def _carga_recente(df, coluna, janela, rotulo):
    """Quantos incidentes aquela equipe/ativo já tinha aberto na janela imediatamente anterior."""
    ordenado = df.sort_values('aberto_em')
    contagem = (
        ordenado.set_index('aberto_em')
        .groupby(coluna, observed=True)['prioridade']
        .rolling(janela, closed='left').count()
        .reset_index(level=0, drop=True)
    )
    contagem.index = ordenado.index
    return contagem.reindex(df.index).fillna(0.0).rename(rotulo)


def features_incidentes(incidentes, painel=None):
    """Uma linha por incidente elegível a KPI, com o que se sabia no instante da abertura.

    **O que fica de fora é o ponto principal.** `duracao_seg`, `resolvido_em`, `status`,
    `código de fechamento` e `solução` descrevem o desfecho: usá-los para prever violação de
    OLA daria uma AUC linda e um modelo inútil, porque nada disso existe quando o incidente
    entra na fila. O modelo só recebe atributos do chamado, calendário, carga do momento e
    histórico anterior da entidade.
    """
    df = incidentes[incidentes['entrou_kpi'] & incidentes['kpi_violado'].notna()].copy()
    df = df.sort_values('aberto_em').reset_index(drop=True)
    df['violou'] = df['kpi_violado'].astype(bool).astype(int)

    feriados = feriados_brasil(range(df['data'].dt.year.min(), df['data'].dt.year.max() + 2))
    abertura = df['aberto_em']

    quadro = pd.DataFrame({
        'prioridade': df['prioridade'].astype(float),
        'sla_minutos': df['prioridade'].map(SLA_MINUTOS).astype(float),
        'hora': abertura.dt.hour.astype(float),
        'minuto_do_dia': (abertura.dt.hour * 60 + abertura.dt.minute).astype(float),
        'dow': abertura.dt.dayofweek.astype(float),
        'dia_mes': abertura.dt.day.astype(float),
        'mes': abertura.dt.month.astype(float),
        'fim_de_semana': (abertura.dt.dayofweek >= 5).astype(float),
        'feriado': abertura.dt.date.isin(feriados).astype(float),
        'fora_horario': (~abertura.dt.hour.between(8, 18)).astype(float),
        'aberto_manual': (df['aberto_por'] == 'manual').astype(float),
        'tem_produto': (df['produto'] != '').astype(float),
        'tem_item_config': (df['item_configuracao'] != '').astype(float),
        'tem_pai': df['incidente_pai_id'].notna().astype(float),
    })

    for coluna, rotulo in (
        ('equipe', 'taxa_violacao_equipe'), ('produto', 'taxa_violacao_produto'),
        ('categoria', 'taxa_violacao_categoria'), ('familia_sinal', 'taxa_violacao_familia'),
        ('item_configuracao', 'taxa_violacao_item'),
    ):
        quadro[rotulo] = _taxa_expandida(df, coluna).astype(float)

    quadro['carga_equipe_1h'] = _carga_recente(df, 'equipe', '1h', 'carga_equipe_1h')
    quadro['carga_equipe_24h'] = _carga_recente(df, 'equipe', '24h', 'carga_equipe_24h')
    quadro['volume_ci_7d'] = _carga_recente(df, 'item_configuracao', '7D', 'volume_ci_7d')
    quadro['volume_produto_24h'] = _carga_recente(df, 'produto', '24h', 'volume_produto_24h')

    if painel is not None:
        contexto = historico_painel(painel)
        # Dia anterior: no instante da abertura, o fechamento do próprio dia ainda não existe.
        vespera = df['data'] - pd.Timedelta(days=1)
        for dimensao, chave_df, colunas, prefixo in (
            ('equipe', df['equipe'], CONTEXTO_EQUIPE, 'equipe'),
            ('produto', df['produto'], CONTEXTO_PRODUTO, 'produto'),
        ):
            recorte = contexto[contexto['dimensao'] == dimensao].set_index(['chave', 'data'])
            indice = pd.MultiIndex.from_arrays([chave_df, vespera])
            anexo = recorte.reindex(indice)[colunas]
            anexo.index = df.index
            quadro = quadro.join(anexo.add_prefix(f'{prefixo}_').astype(float))

    identificacao = df[['numero', 'aberto_em', 'data', 'equipe', 'produto', 'prioridade']].copy()
    return quadro.fillna(0.0).replace([np.inf, -np.inf], 0.0), df['violou'], identificacao


COLUNAS_IDENTIFICACAO = ['dimensao', 'chave', 'data_origem', 'data_alvo']
COLUNAS_ALVO = ['alvo', 'alvo_violacoes', 'alvo_criticos', 'alvo_total']


def colunas_de_features(matriz):
    """Tudo que não é identificação nem alvo é feature. Uma lista só, sem surpresa."""
    excluir = set(COLUNAS_IDENTIFICACAO + COLUNAS_ALVO)
    return [c for c in matriz.columns if c not in excluir and matriz[c].dtype != object]


def separar(matriz, alvo='alvo'):
    """Devolve (X, y, identificação) com as features já numéricas e ordenadas."""
    features = colunas_de_features(matriz)
    X = matriz[features].astype(float)
    # Árvore lida com NaN; modelo linear não. Como as razões podem dividir por zero (entidade
    # parada na janela), o NaN dessas colunas vira 0 — "sem sinal", que é o significado real.
    X = X.fillna(0.0).replace([np.inf, -np.inf], 0.0)
    return X, matriz[alvo].astype(float), matriz[COLUNAS_IDENTIFICACAO]
