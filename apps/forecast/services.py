"""Forecast Engine.

Modelo: média histórica do dia da semana × fator de tendência recente. Simples e interpretável
de propósito — com a janela de dados disponível, a análise do dataset mostrou que modelos mais
complexos não superaram consistentemente essa abordagem, e um modelo que o operador entende é
mais útil que um que ele precisa aceitar por fé.

O intervalo de previsão **não é arbitrado**: sai do desvio dos resíduos do próprio modelo em
backtest, então reflete o erro que ele de fato comete nesta série.
"""

from collections import defaultdict
from datetime import timedelta

from apps.core.models import Incidente
from apps.core.tempo import data_referencia
from apps.intelligence.services import base
from apps.intelligence.services.base import DIM

HISTORICO_PADRAO = 90
DIAS_SEMANA = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']


def _media_por_dia_semana(serie):
    somas = defaultdict(list)
    for data, valor in serie:
        somas[data.weekday()].append(valor)
    return {dia: base.media(valores) for dia, valores in somas.items() if valores}


def _fator_tendencia(serie):
    recentes = [v for _, v in serie[-14:]]
    anteriores = [v for _, v in serie[-28:-14]] or recentes
    media_recente = base.media(recentes)
    media_anterior = base.media(anteriores)
    if not media_anterior:
        return 1.0
    return max(0.6, min(media_recente / media_anterior, 1.8))


def _modelo(serie):
    """Devolve uma função que estima o volume de uma data futura."""
    media_semana = _media_por_dia_semana(serie)
    media_geral = base.media(v for _, v in serie)
    fator = _fator_tendencia(serie)

    def prever(data):
        return max(0, media_semana.get(data.weekday(), media_geral) * fator)

    return prever, fator


def backtest(historico_dias=HISTORICO_PADRAO, dias_teste=21, apenas_kpi=True):
    """Avalia o modelo em holdout cronológico — nunca em dados que ele já viu.

    Alimenta a tela Model Performance e o intervalo de previsão.
    """
    campo = 'total_kpi' if apenas_kpi else 'total'
    serie = base.serie_diaria(historico_dias + dias_teste, campo=campo)
    if len(serie) < dias_teste + 21:
        return None

    treino, teste = serie[:-dias_teste], serie[-dias_teste:]
    prever, _fator = _modelo(treino)

    erros, absolutos, percentuais, pontos = [], [], [], []
    for data, real in teste:
        estimado = prever(data)
        erro = real - estimado
        erros.append(erro)
        absolutos.append(abs(erro))
        if real:
            percentuais.append(abs(erro) / real)
        pontos.append({
            'data': data,
            'real': real,
            'previsto': round(estimado, 1),
            'erro': round(erro, 1),
        })

    # Baseline ingênua (repetir o valor de 7 dias antes) — referência honesta de comparação.
    naive_abs = []
    serie_dict = dict(serie)
    for data, real in teste:
        anterior = serie_dict.get(data - timedelta(days=7))
        if anterior is not None:
            naive_abs.append(abs(real - anterior))

    return {
        'mae': round(base.media(absolutos), 2),
        'rmse': round((base.media([e ** 2 for e in erros])) ** 0.5, 2),
        'mape': round(base.media(percentuais) * 100, 1) if percentuais else None,
        'mae_naive': round(base.media(naive_abs), 2) if naive_abs else None,
        'desvio_residuos': round(base.desvio_padrao(erros), 2),
        'dias_teste': dias_teste,
        'dias_treino': len(treino),
        'pontos': pontos,
        'campo': campo,
    }


def prever(dias_futuros=7, historico_dias=HISTORICO_PADRAO, apenas_kpi=True):
    campo = 'total_kpi' if apenas_kpi else 'total'
    serie = base.serie_diaria(historico_dias, campo=campo)
    if not serie:
        return []

    prever_data, _fator = _modelo(serie)
    avaliacao = backtest(historico_dias, apenas_kpi=apenas_kpi)
    margem = (avaliacao['desvio_residuos'] if avaliacao else 0) or 0

    hoje = data_referencia()
    previsoes = []
    for i in range(1, dias_futuros + 1):
        data = hoje + timedelta(days=i)
        valor = prever_data(data)
        previsoes.append({
            'data': data,
            'dia_semana': DIAS_SEMANA[data.weekday()],
            'valor': round(valor),
            'minimo': max(0, round(valor - margem)),
            'maximo': round(valor + margem),
        })
    return previsoes


def resumo(apenas_kpi=True):
    previsoes = prever(7, apenas_kpi=apenas_kpi)
    if not previsoes:
        return None

    avaliacao = backtest(apenas_kpi=apenas_kpi)
    serie = base.serie_diaria(28, campo='total_kpi' if apenas_kpi else 'total')
    recente = base.media(v for _, v in serie[-7:])
    anterior = base.media(v for _, v in serie[-14:-7])
    tendencia = base.variacao_percentual(recente, anterior)

    d1 = previsoes[0]
    d7_total = sum(p['valor'] for p in previsoes)
    pico = max(previsoes, key=lambda p: p['valor'])

    return {
        'd1': d1,
        'd7_total': d7_total,
        'd7_min': sum(p['minimo'] for p in previsoes),
        'd7_max': sum(p['maximo'] for p in previsoes),
        'previsoes': previsoes,
        'tendencia': tendencia,
        'pico': pico,
        'avaliacao': avaliacao,
        'media_recente': round(recente, 1),
    }


def interpretar(dados):
    """Transforma a previsão em leitura acionável — o gráfico sozinho não decide nada."""
    if not dados:
        return ''

    partes = []
    d1 = dados['d1']
    partes.append(
        f'Para amanhã ({d1["dia_semana"]}, {d1["data"]:%d/%m}) a estimativa é de {d1["valor"]} '
        f'incidentes elegíveis, com faixa provável entre {d1["minimo"]} e {d1["maximo"]}.'
    )

    tendencia = dados['tendencia']
    if tendencia is not None and abs(tendencia) >= 5:
        direcao = 'alta' if tendencia > 0 else 'queda'
        partes.append(f'A média diária vem em {direcao} de {abs(tendencia):.0f}% frente à semana anterior.')
    else:
        partes.append('A média diária está estável frente à semana anterior.')

    pico = dados['pico']
    if pico['valor'] > dados['media_recente'] * 1.15:
        partes.append(
            f'O maior volume previsto cai em {pico["dia_semana"]} ({pico["data"]:%d/%m}), '
            f'com {pico["valor"]} incidentes — é o dia que merece reforço de escala.'
        )

    avaliacao = dados['avaliacao']
    if avaliacao and avaliacao['mae_naive']:
        if avaliacao['mae'] < avaliacao['mae_naive']:
            ganho = round((1 - avaliacao['mae'] / avaliacao['mae_naive']) * 100)
            partes.append(
                f'Em backtest, o modelo erra {ganho}% menos que simplesmente repetir o mesmo dia '
                f'da semana anterior (MAE {avaliacao["mae"]} vs. {avaliacao["mae_naive"]}).'
            )
        else:
            partes.append(
                f'Em backtest o modelo não supera a baseline sazonal (MAE {avaliacao["mae"]} vs. '
                f'{avaliacao["mae_naive"]}) — trate a previsão como referência, não como garantia.'
            )
    return ' '.join(partes)


def calendario_risco(dias=28):
    """Risk Calendar — dias futuros ordenados por volume previsto e risco relativo."""
    previsoes = prever(dias)
    if not previsoes:
        return []
    valores = [p['valor'] for p in previsoes]
    maximo = max(valores) or 1
    limiar_alto = base.media(valores) * 1.15

    for p in previsoes:
        p['intensidade'] = round((p['valor'] / maximo) * 100)
        p['faixa'] = 'critical' if p['valor'] >= limiar_alto * 1.15 else (
            'warning' if p['valor'] >= limiar_alto else 'healthy'
        )
    return previsoes


def tendencia_por_dimensao(dimensao=DIM.FAMILIA, dias=28, limite=6):
    """Crescimento/redução por família de sinal, produto ou equipe."""
    from apps.core.models import FamiliaSinal

    nomes = dict(FamiliaSinal.objects.values_list('slug', 'nome')) if dimensao == DIM.FAMILIA else {}
    metade = dias // 2
    recentes = {l['chave']: l for l in base.agregado_por_chave(metade, dimensao, minimo_total=1)}
    anteriores = {
        l['chave']: l
        for l in base.agregado_por_chave(metade, dimensao, ate=data_referencia() - timedelta(days=metade))
    }

    linhas = []
    for chave, recente in recentes.items():
        anterior = anteriores.get(chave, {'total_kpi': 0})
        variacao = base.variacao_percentual(recente['total_kpi'], anterior.get('total_kpi', 0))
        linhas.append({
            'chave': chave,
            'nome': nomes.get(chave, chave),
            'recentes': recente['total_kpi'],
            'variacao': variacao,
        })

    linhas.sort(key=lambda x: x['recentes'], reverse=True)
    return linhas[:limite]
