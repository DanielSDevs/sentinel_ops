from django.shortcuts import render

from apps.alerts.services import fila_de_decisao
from apps.forecast import services as forecast_services
from apps.intelligence.services import briefing, deltas, health, insights, risk
from apps.intelligence.services.base import DIM
from apps.ml.services import inferencia
from apps.monitor import services as monitor_services


def diario(request):
    previsao = forecast_services.resumo()
    return render(request, 'reports/diario.html', {
        'saude': health.calcular(janela_dias=1),
        'briefing': briefing.montar(janela_dias=1),
        'metricas': monitor_services.metricas_operacionais(janela_dias=1),
        'deltas': deltas.calcular(janela_dias=1),
        'incidentes': monitor_services.operacao_ao_vivo(horas=24, limite=10),
        'previsao': previsao,
        'interpretacao': forecast_services.interpretar(previsao),
    })


def executivo(request):
    """Executive Mode — foco em exposição e decisão, sem detalhe técnico."""
    saude = health.calcular(janela_dias=30)
    riscos = risk.calcular(DIM.PRODUTO, limite=5)
    metricas = monitor_services.metricas_operacionais(janela_dias=30)
    return render(request, 'reports/executivo.html', {
        'saude': saude,
        'metricas': metricas,
        'riscos': riscos,
        'servicos_em_risco': [r for r in riscos if r.faixa in ('critical', 'warning')],
        'deltas': deltas.calcular(janela_dias=30),
        'acoes': fila_de_decisao(limite=5),
        'insights': insights.gerar(limite=4),
        'briefing': briefing.montar(janela_dias=30),
        # A leitura executiva precisa do que vem, não só do que passou — e com a incerteza junto,
        # que é o que separa previsão de promessa.
        'previsao': forecast_services.resumo(),
        'interpretacao_previsao': forecast_services.interpretar(forecast_services.resumo()),
        'risco_ola': inferencia.risco_ola_global(),
        'picos': forecast_services.picos(limite=3),
    })
