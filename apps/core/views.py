from django.shortcuts import render

from apps.alerts.services import fila_de_decisao
from apps.forecast import services as forecast_services
from apps.intelligence.services import briefing, deltas, health, insights, risk
from apps.intelligence.services.base import DIM
from apps.monitor import services as monitor_services


def command_center(request):
    """A tela que responde, em 30 segundos: como está / o que mudou / o que vem / o que fazer."""
    saude = health.calcular()
    previsao = forecast_services.resumo()

    contexto = {
        'saude': saude,
        'briefing': briefing.montar(),
        'deltas': deltas.calcular(),
        'metricas': monitor_services.metricas_operacionais(),
        'riscos': risk.calcular(DIM.PRODUTO, limite=4),
        'insights': insights.gerar(limite=4),
        'acoes': fila_de_decisao(limite=4),
        'previsao': previsao,
        'interpretacao_previsao': forecast_services.interpretar(previsao),
    }
    return render(request, 'core/command_center.html', contexto)


def data_sources(request):
    """Transparência sobre a base: o que foi carregado, cobertura e lacunas conhecidas."""
    cobertura = monitor_services.cobertura_dataset()
    qualidade = []
    if cobertura:
        total = cobertura['total']
        qualidade = [
            {
                'campo': 'Produto',
                'ausentes': cobertura['sem_produto'],
                'pct': round(cobertura['sem_produto'] / total * 100, 1),
                'impacto': 'Incidentes sem produto ficam fora do Service Health Map e do Risk Radar por produto.',
            },
            {
                'campo': 'Categoria',
                'ausentes': cobertura['sem_categoria'],
                'pct': round(cobertura['sem_categoria'] / total * 100, 1),
                'impacto': 'Reduz a granularidade da análise de causa e da similaridade entre incidentes.',
            },
        ]

    return render(request, 'core/data_sources.html', {
        'cobertura': cobertura,
        'qualidade': qualidade,
        'campos_ausentes': [
            ('Impacto financeiro', 'Não há custo por serviço nem valor de transação no extrato.'),
            ('Métricas de infraestrutura', 'CPU, latência e error rate não são exportados — só o incidente resultante.'),
            ('Topologia de dependências', 'Não há mapa de dependência entre serviços; a correlação usa incidente-pai e co-ocorrência.'),
        ],
    })


def sobre(request):
    return render(request, 'core/sobre.html')
