from django.shortcuts import render

from apps.intelligence.services.base import DIM

from . import services


def index(request):
    previsao = services.resumo()
    maximo = max((p['valor'] for p in previsao['previsoes']), default=0) or 1 if previsao else 1
    if previsao:
        for p in previsao['previsoes']:
            p['altura_pct'] = round(p['valor'] / maximo * 100)

    return render(request, 'forecast/index.html', {
        'previsao': previsao,
        'interpretacao': services.interpretar(previsao),
        'calendario': services.calendario_risco(dias=21),
        'tendencias': services.tendencia_por_dimensao(DIM.FAMILIA),
    })


def model_performance(request):
    avaliacao = services.backtest()
    ganho = None
    if avaliacao and avaliacao['mae_naive']:
        ganho = round((1 - avaliacao['mae'] / avaliacao['mae_naive']) * 100, 1)

    maximo = 1
    if avaliacao:
        maximo = max(
            max((p['real'] for p in avaliacao['pontos']), default=1),
            max((p['previsto'] for p in avaliacao['pontos']), default=1),
        ) or 1

    return render(request, 'forecast/model_performance.html', {
        'avaliacao': avaliacao,
        'ganho': ganho,
        'maximo': maximo,
    })
