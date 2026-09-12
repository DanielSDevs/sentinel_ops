from django.shortcuts import render

from apps.core.models import FamiliaSinal
from apps.ml.services import catalogo, inferencia

from .services import anomaly, correlation, insights, risk
from .services.base import DIM


def risk_radar(request):
    return render(request, 'intelligence/risk_radar.html', {
        'panorama': risk.panorama(),
    })


def anomaly_detection(request):
    dimensao = request.GET.get('dim', DIM.FAMILIA)
    timeline_global, _ = anomaly.detectar(DIM.GLOBAL, '', campo='total_kpi')
    achadas = anomaly.detectar_por_dimensao(dimensao, limite=8)

    nomes = dict(FamiliaSinal.objects.values_list('slug', 'nome'))
    for item in achadas:
        item.correlacoes = anomaly.correlacionar(item)
        item.nome_exibicao = nomes.get(item.chave, item.chave)

    cartao = catalogo.modelo('model_anomalia')
    return render(request, 'intelligence/anomaly.html', {
        'timeline_global': timeline_global,
        'anomalias': achadas,
        # O Isolation Forest entra ao lado do z-score, não no lugar dele: um olha 15 variáveis
        # ao mesmo tempo, o outro olha o volume contra a própria média. Onde os dois apontam
        # junto, a evidência é mais forte; onde divergem, cada um está vendo uma coisa.
        'anomalias_ml': inferencia.anomalias(dias=45, limite=10),
        'modelo_anomalia': cartao,
        'dimensao': dimensao,
        'dimensoes': [
            (DIM.FAMILIA, 'Família de sinal'),
            (DIM.PRODUTO, 'Produto'),
            (DIM.EQUIPE, 'Equipe'),
        ],
    })


def correlation_engine(request):
    tempestades = correlation.tempestades(limite=5)
    return render(request, 'intelligence/correlation.html', {
        'tempestades': tempestades,
        'cadeias': correlation.cadeia_de_sintomas(limite=8),
    })


def operational_insights(request):
    return render(request, 'intelligence/insights.html', {
        'insights': insights.gerar(limite=10),
        'rotulos': insights.ROTULO_TIPO,
    })
