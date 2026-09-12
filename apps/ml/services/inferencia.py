"""Inferência: transforma os artefatos treinados nas previsões que as telas mostram.

Duas regras valem para tudo aqui:

1. **Mesmo código de feature do treino.** As linhas de previsão saem de
   `ml.src.features.construir_features(..., apenas_com_alvo=False)` — a mesma função que montou
   a matriz de treino. Reescrever a engenharia de features no lado da aplicação é como se cria
   training/serving skew, e ele aparece semanas depois como "o modelo piorou sozinho".
2. **Sem modelo, sem número.** Se o `.pkl` não existe, a função devolve `None` e a tela diz
   "modelo não treinado". Em nenhum caminho existe fallback silencioso para média, regra fixa ou
   valor de exemplo — um número inventado com cara de previsão é pior que campo vazio.
"""

import logging

import numpy as np
import pandas as pd
from django.core.cache import cache
from django.db import connection as conexao_django

from apps.core.models import Incidente
from ml.src import explain, features, registry, train

logger = logging.getLogger(__name__)

CACHE_TTL = 300
DIAS_SEMANA = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']

# Faixas de decisão do risco de OLA. São cortes de política operacional (quando acionar quem),
# aplicados sobre a probabilidade que o modelo calculou — não substituem nem alteram o número.
FAIXA_ALTO = 0.50
FAIXA_MEDIO = 0.25


class ModeloIndisponivel(Exception):
    """Artefato treinado com um conjunto de features diferente do que o código produz hoje.

    Acontece quando `ml/src/features.py` muda e ninguém retreina. É erro de operação, não de
    dado: a plataforma para de prever e diz o porquê, em vez de preencher com o que sobrou.
    """


def _versao_dados():
    """Chave de cache que muda sozinha quando a base muda (importação ou simulação)."""
    return Incidente.objects.count()


def artefato(nome):
    """Carrega o artefato uma vez por processo — desserializar XGBoost a cada request é caro."""
    chave = f'sentinelops:ml:artefato:{nome}'
    em_cache = cache.get(chave)
    if em_cache is not None:
        return em_cache
    try:
        carregado = registry.carregar(nome)
    except FileNotFoundError:
        return None
    cache.set(chave, carregado, CACHE_TTL)
    return carregado


def disponivel(nome):
    return registry.existe(nome)


def contexto():
    """Painel e linhas de previsão para a data de referência, montados como no treino.

    Lê pela conexão do próprio Django (e não pelo caminho em `ml.src.config`), para que a
    inferência enxergue exatamente o banco que a aplicação está servindo.
    """
    if not Incidente.objects.exists():
        return None

    chave = f'sentinelops:ml:contexto:{_versao_dados()}'
    em_cache = cache.get(chave)
    if em_cache is not None:
        return em_cache

    conexao_django.ensure_connection()
    base = train.preparar_base(banco=conexao_django.connection, verbose=False)
    # Base curta demais para as janelas de 28 dias: não há previsão a fazer, e completar com
    # zeros produziria uma previsão confiante de silêncio.
    if base['painel'].empty:
        return None

    # Só o último dia de origem: é dele que saem as previsões de D+1 a D+7.
    ultima_origem = base['painel']['data'].max()
    linhas = features.construir_features(
        base['painel'], apenas_com_alvo=False, origens=[ultima_origem],
    )
    if linhas.empty:
        return None
    linhas = linhas.reset_index(drop=True)

    dados = {
        'painel': base['painel'],
        'linhas': linhas,
        'origem': ultima_origem,
        'inicio_regime': base['inicio_regime'],
    }
    cache.set(chave, dados, CACHE_TTL)
    return dados


def _matriz(linhas, colunas):
    faltando = [c for c in colunas if c not in linhas.columns]
    if faltando:
        raise ModeloIndisponivel(
            f'Features ausentes na inferência: {faltando[:5]}. Retreine com `treinar_modelos`.'
        )
    return linhas[colunas].astype(float).fillna(0.0).replace([np.inf, -np.inf], 0.0)


def tabela_previsoes():
    """Todas as entidades e horizontes previstos de uma vez, em três chamadas de `predict`.

    A versão anterior chamava o modelo uma vez por (entidade, horizonte): 45 entidades × 7 dias
    davam mais de 300 chamadas por tela, e o Command Center levava 10 segundos. Árvore prevê em
    lote praticamente pelo mesmo custo de uma linha — o gargalo é a travessia do Python, não a
    aritmética. Uma chamada só resolve.

    São dois modelos de volume porque são dois problemas: com um dia à frente o último valor
    observado ainda carrega quase toda a informação; com sete, o que sobra é o perfil de semana
    e o patamar. Deixar um modelo só cobrir os dois horizontes piorava ambos.
    """
    d1 = artefato('model_d1')
    d7 = artefato('model_d7')
    if d1 is None and d7 is None:
        return None

    dados = contexto()
    if dados is None:
        return None

    chave_cache = f'sentinelops:ml:previsoes:{_versao_dados()}'
    em_cache = cache.get(chave_cache)
    if em_cache is not None:
        return em_cache

    linhas = dados['linhas']
    # `share_p2_7` e `share_criticos_7` viajam junto porque respondem "qual prioridade está
    # puxando este pico" — são features que o modelo já usa, não um cálculo novo para a tela.
    tabela = linhas[[
        'dimensao', 'chave', 'horizonte', 'data_alvo', 'media_7',
        'share_p2_7', 'share_criticos_7', 'p2_media_7',
    ]].copy()
    tabela['valor_exato'] = np.nan
    tabela['modelo'] = ''
    tabela['algoritmo'] = ''

    primeiro = linhas['horizonte'] == 1
    blocos = [(primeiro, d1 or d7), (~primeiro, d7 or d1)]
    for mascara, usado in blocos:
        if usado is None or not mascara.any():
            continue
        X = _matriz(linhas[mascara], usado['colunas'])
        tabela.loc[mascara, 'valor_exato'] = np.clip(usado['modelo'].predict(X), 0, None)
        tabela.loc[mascara, 'modelo'] = usado['cartao']['nome']
        tabela.loc[mascara, 'algoritmo'] = usado['cartao']['algoritmo']
        margens = usado.get('margens') or {'geral': 0.0, 'por_chave': {}}
        relativa = tabela.loc[mascara, 'dimensao'].map(
            lambda d: margens['por_chave'].get(d, margens['geral']),
        )
        tabela.loc[mascara, 'margem'] = relativa * np.maximum(
            tabela.loc[mascara, 'media_7'], 1.0,
        )

    pico = artefato('model_pico')
    if pico is not None:
        # O classificador de pico é acessório: se ele estiver defasado, a previsão de volume
        # continua valendo e só a coluna de probabilidade fica de fora.
        try:
            X_pico = _matriz(linhas, pico['colunas'])
        except ModeloIndisponivel:
            logger.warning('model_pico defasado em relação às features atuais; retreine.')
        else:
            tabela['probabilidade_pico'] = pico['modelo'].predict_proba(X_pico)[:, 1].round(3)
            tabela['pico'] = tabela['probabilidade_pico'] >= pico['limiar']

    tabela = tabela.dropna(subset=['valor_exato'])
    cache.set(chave_cache, tabela, CACHE_TTL)
    return tabela


def _formatar(linha):
    data = linha['data_alvo'].date()
    valor = float(linha['valor_exato'])
    margem = float(linha.get('margem') or 0.0)
    item = {
        'data': data,
        'horizonte': int(linha['horizonte']),
        'dia_semana': DIAS_SEMANA[data.weekday()],
        'valor': round(valor),
        'valor_exato': round(valor, 2),
        'minimo': max(0, round(valor - margem)),
        'maximo': round(valor + margem),
        'modelo': linha['modelo'],
        'algoritmo': linha['algoritmo'],
        'media_recente': round(float(linha['media_7']), 1),
    }
    if 'probabilidade_pico' in linha:
        item['probabilidade_pico'] = float(linha['probabilidade_pico'])
        item['pico'] = bool(linha['pico'])
    return item


def prever(dimensao='global', chave='', dias=7):
    """Previsão de volume por dia de uma entidade, já formatada para a tela."""
    tabela = tabela_previsoes()
    if tabela is None:
        return None

    recorte = tabela[
        (tabela['dimensao'] == dimensao) & (tabela['chave'] == chave)
        & (tabela['horizonte'] <= dias)
    ].sort_values('horizonte')
    if recorte.empty:
        return None
    return [_formatar(linha) for _, linha in recorte.iterrows()]


def explicar(dimensao='global', chave='', horizonte=1, limite=6):
    """Por que o modelo chegou nesse número: contribuições SHAP daquela previsão específica."""
    nome = 'model_d1' if horizonte == 1 else 'model_d7'
    item = artefato(nome)
    if item is None or item.get('explicador') is None:
        return None

    dados = contexto()
    if dados is None:
        return None
    linhas = dados['linhas']
    linha = linhas[
        (linhas['dimensao'] == dimensao) & (linhas['chave'] == chave)
        & (linhas['horizonte'] == horizonte)
    ]
    if linha.empty:
        return None

    X = _matriz(linha, item['colunas'])
    contribuicoes = explain.contribuicoes_shap(
        item['explicador'], X, item['colunas'], limite=limite,
    )
    base = float(item['explicador'].expected_value)
    return {
        'modelo': nome,
        'algoritmo': item['cartao']['algoritmo'],
        'valor_base': round(base, 2),
        'contribuicoes': contribuicoes,
    }


def resumo_global(dias=7):
    """Bloco consolidado que o Command Center e o Forecast Engine consomem."""
    previsoes = prever(dias=dias)
    if not previsoes:
        return None

    dados = contexto()
    if dados is None:
        return None
    serie = dados['painel']
    global_serie = serie[serie['dimensao'] == 'global'].sort_values('data')
    ultimos_7 = global_serie['total_kpi'].tail(7).mean()
    anteriores_7 = global_serie['total_kpi'].tail(14).head(7).mean()

    d1 = previsoes[0]
    total_d7 = sum(p['valor'] for p in previsoes)
    pico = max(previsoes, key=lambda p: p['valor'])

    return {
        'd1': d1,
        'previsoes': previsoes,
        'd7_total': total_d7,
        'd7_min': sum(p['minimo'] for p in previsoes),
        'd7_max': sum(p['maximo'] for p in previsoes),
        'media_recente': round(ultimos_7, 1),
        'variacao_d1': (
            round((d1['valor'] - ultimos_7) / ultimos_7 * 100, 1) if ultimos_7 else None
        ),
        'tendencia': (
            round((ultimos_7 - anteriores_7) / anteriores_7 * 100, 1) if anteriores_7 else None
        ),
        'pico': pico,
        'origem': dados['origem'].date(),
        'explicacao': explicar(),
    }


def previsao_por_dimensao(dimensao='produto', limite=8, dias=7):
    """D+1 e total D+7 por produto, equipe ou categoria — onde o volume vai cair."""
    tabela = tabela_previsoes()
    if tabela is None:
        return []

    recorte = tabela[(tabela['dimensao'] == dimensao) & (tabela['horizonte'] <= dias)]
    resultado = []
    for chave, bloco in recorte.groupby('chave'):
        previsoes = [_formatar(linha) for _, linha in bloco.sort_values('horizonte').iterrows()]
        atual = previsoes[0]['media_recente']
        primeira = bloco.iloc[0]
        resultado.append({
            'chave': chave,
            'd1': previsoes[0]['valor'],
            'd7_total': sum(p['valor'] for p in previsoes),
            'media_recente': atual,
            'share_p2': round(float(primeira['share_p2_7'] or 0), 3),
            'share_criticos': round(float(primeira['share_criticos_7'] or 0), 3),
            'variacao': round((previsoes[0]['valor'] - atual) / atual * 100, 1) if atual else None,
            'probabilidade_pico': max((p.get('probabilidade_pico') or 0) for p in previsoes),
            'dia_pico': max(previsoes, key=lambda p: p['valor'])['data'],
            'previsoes': previsoes,
        })

    resultado.sort(key=lambda item: item['d7_total'], reverse=True)
    return resultado[:limite]


def picos_previstos(limite=6, minimo=None, volume_minimo=3):
    """Combinação dos dois modelos: onde, quando e com que probabilidade o pico deve aparecer.

    `minimo` é, por padrão, o **limiar do próprio modelo** — aquele escolhido na validação para
    maximizar F1, não um 0,5 arbitrado. Depois da calibração as probabilidades vivem na escala
    real (taxa-base ~0,10), e um corte fixo em 0,5 simplesmente nunca dispararia.

    `volume_minimo` é filtro de tela, não de modelo: pico é um conceito relativo, e numa equipe
    que recebe 0,1 incidente por dia o classificador acerta ao marcar "2 incidentes amanhã" como
    topo da distribuição dela. Só que ninguém remaneja escala por causa disso. O corte deixa na
    tela o que sustenta uma decisão; a probabilidade em si não é alterada.
    """
    artefato_pico = artefato('model_pico')
    if artefato_pico is None:
        return None
    if minimo is None:
        minimo = artefato_pico['limiar']

    achados = []
    # Prioridade fica de fora: "P3 vai ter pico" não diz a ninguém onde agir. As dimensões aqui
    # são as que apontam um responsável ou um ativo.
    for dimensao in ('produto', 'equipe', 'categoria', 'item'):
        for entidade in previsao_por_dimensao(dimensao, limite=99):
            for previsao in entidade['previsoes']:
                probabilidade = previsao.get('probabilidade_pico') or 0
                if probabilidade < minimo or previsao['valor'] < volume_minimo:
                    continue
                achados.append({
                    'dimensao': dimensao,
                    'chave': entidade['chave'],
                    'data': previsao['data'],
                    'dia_semana': previsao['dia_semana'],
                    'horizonte': previsao['horizonte'],
                    'valor': previsao['valor'],
                    'media_recente': entidade['media_recente'],
                    'probabilidade': probabilidade,
                    'share_p2': entidade['share_p2'],
                    'share_criticos': entidade['share_criticos'],
                    'p2_esperados': round(previsao['valor'] * entidade['share_p2']),
                    'variacao': (
                        round((previsao['valor'] - entidade['media_recente'])
                              / entidade['media_recente'] * 100)
                        if entidade['media_recente'] else None
                    ),
                })

    achados.sort(key=lambda item: (item['probabilidade'], item['valor']), reverse=True)
    return achados[:limite]


def _faixa(probabilidade):
    if probabilidade >= FAIXA_ALTO:
        return 'alto'
    if probabilidade >= FAIXA_MEDIO:
        return 'medio'
    return 'baixo'


def risco_ola(dimensao='equipe', limite=8):
    """Risco de perda de OLA em D+1, composto pelos dois modelos treinados.

    O classificador de OLA estima a probabilidade de **um** incidente violar o prazo; o modelo
    de volume estima **quantos** incidentes aquela equipe deve receber amanhã. A chance de pelo
    menos uma violação é a composição dos dois: `1 − (1 − p)^volume`.

    É por isso que uma equipe com probabilidade individual baixa pode aparecer em risco alto:
    volume suficiente transforma um evento raro em quase certo. Nenhuma das duas pontas é
    arbitrada — ambas saem de modelo treinado e avaliado.
    """
    ola = artefato('model_ola')
    if ola is None:
        return None

    probabilidades = ola['probabilidade_media']
    contribuicoes = ola.get('contribuicoes', {}).get(dimensao, {})
    padrao = probabilidades['global']

    resultado = []
    for entidade in previsao_por_dimensao(dimensao, limite=99, dias=1):
        chave = entidade['chave']
        p = float(probabilidades.get(dimensao, {}).get(chave, padrao))
        volume = entidade['d1']
        risco = 1 - (1 - p) ** max(volume, 0)
        resultado.append({
            'chave': chave,
            'probabilidade_incidente': round(p * 100, 2),
            'volume_previsto': volume,
            'violacoes_esperadas': round(p * volume, 2),
            'risco': round(risco * 100, 1),
            'faixa': _faixa(risco),
            'fatores': contribuicoes.get(chave, []),
            'media_recente': entidade['media_recente'],
            'variacao': entidade['variacao'],
        })

    resultado.sort(key=lambda item: item['risco'], reverse=True)
    return resultado[:limite]


def risco_ola_global():
    """Risco consolidado do dia seguinte, na mesma composição, para o KPI do dashboard."""
    ola = artefato('model_ola')
    previsao = prever(dias=1)
    if ola is None or not previsao:
        return None

    p = float(ola['probabilidade_media']['global'])
    volume = previsao[0]['valor']
    risco = 1 - (1 - p) ** max(volume, 0)
    return {
        'risco': round(risco * 100, 1),
        'faixa': _faixa(risco),
        'probabilidade_incidente': round(p * 100, 2),
        'volume_previsto': volume,
        'violacoes_esperadas': round(p * volume, 2),
        'algoritmo': ola['cartao']['algoritmo'],
        'pr_auc': ola['cartao']['metricas']['teste'].get('pr_auc'),
    }


def anomalias(dias=30, limite=10):
    """Dias recentes marcados pelo Isolation Forest, com a pontuação de isolamento."""
    item = artefato('model_anomalia')
    if item is None:
        return None

    dados = contexto()
    if dados is None:
        return None
    contexto_painel = features.historico_painel(dados['painel'])
    colunas = item['colunas']
    recorte = contexto_painel.dropna(subset=colunas)
    limite_data = recorte['data'].max() - pd.Timedelta(days=dias)
    recorte = recorte[recorte['data'] >= limite_data]
    if recorte.empty:
        return None

    X = recorte[colunas].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    pontuacao = -item['modelo'].score_samples(item['escala'].transform(X))

    achados = recorte.assign(pontuacao=pontuacao)
    achados = achados[achados['pontuacao'] >= item['limiar']]
    achados = achados.sort_values('pontuacao', ascending=False).head(limite)

    return [
        {
            'data': linha['data'].date(),
            'dimensao': linha['dimensao'],
            'chave': linha['chave'] or 'Operação inteira',
            'pontuacao': round(float(linha['pontuacao']), 3),
            'volume': int(linha['lag_0']),
            'media_7': round(float(linha['media_7']), 1),
            'criticos': round(float(linha['criticos_media_7']), 1),
            'mttr': round(float(linha['mttr_medio_media_7']), 1),
        }
        for _, linha in achados.iterrows()
    ]


def clusters():
    """Grupos de produtos com comportamento operacional semelhante."""
    item = artefato('model_cluster')
    if item is None:
        return None
    cartao = item['cartao']
    return {
        'k': cartao['metricas']['k_escolhido'],
        'silhueta': cartao['metricas']['silhueta'],
        'grupos': cartao['grupos'],
        'features': cartao['features'],
    }
