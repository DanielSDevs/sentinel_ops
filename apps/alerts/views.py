from django.shortcuts import render

from . import services


def alert_center(request):
    alertas = services.alertas_ativos()
    return render(request, 'alerts/alert_center.html', {
        'alertas': alertas,
        'criticos': sum(1 for a in alertas if a.faixa == 'critical'),
    })


def decision_center(request):
    return render(request, 'alerts/decision_center.html', {
        'acoes': services.fila_de_decisao(limite=10),
    })
