from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.models import Incidente
from apps.intelligence.services import anomaly, similarity
from apps.intelligence.services.base import DIM

from . import perfis, services, simulacao


def live_operations(request):
    horas = int(request.GET.get('horas', 24))
    timeline, _anomalias = anomaly.detectar(DIM.GLOBAL, '', campo='total_kpi')
    return render(request, 'monitor/live.html', {
        'metricas': services.metricas_operacionais(),
        'incidentes': services.operacao_ao_vivo(horas=horas),
        'por_hora': services.volume_por_hora(horas=horas),
        'recorrentes': services.itens_recorrentes(),
        'timeline': timeline[-30:],
        'horas': horas,
    })


def service_health(request):
    return render(request, 'monitor/service_health.html', {
        'servicos': services.mapa_saude_servicos(limite=18),
        'comportamento': perfis.grupos_de_comportamento(),
    })


def detalhe_servico(request, codigo):
    return render(request, 'monitor/detalhe_servico.html', {
        'detalhe': services.detalhe_servico(codigo),
    })


def incident_intelligence(request):
    filtros = {
        'prioridade': request.GET.get('prioridade', ''),
        'busca': request.GET.get('q', '').strip(),
        'so_kpi': request.GET.get('kpi', '') == '1',
    }

    incidentes = Incidente.objects.select_related('produto', 'equipe', 'familia_sinal')
    if filtros['prioridade']:
        incidentes = incidentes.filter(prioridade=filtros['prioridade'])
    if filtros['so_kpi']:
        incidentes = incidentes.filter(entrou_kpi=True)
    if filtros['busca']:
        incidentes = incidentes.filter(descricao__icontains=filtros['busca'])

    return render(request, 'monitor/incident_intelligence.html', {
        'incidentes': incidentes.order_by('-aberto_em')[:40],
        'filtros': filtros,
        'prioridades': Incidente.Prioridade.choices,
    })


def detalhe_incidente(request, numero):
    incidente = get_object_or_404(
        Incidente.objects.select_related('produto', 'equipe', 'familia_sinal', 'item_configuracao', 'incidente_pai'),
        numero=numero,
    )
    vizinhos = similarity.semelhantes(incidente)
    perfil = similarity.perfil_resolucao(incidente, vizinhos)
    return render(request, 'monitor/detalhe_incidente.html', {
        'incidente': incidente,
        'semelhantes': vizinhos,
        'perfil': perfil,
        'hipotese': similarity.hipotese_causa(incidente, perfil),
        'filhos': incidente.filhos.select_related('produto', 'familia_sinal')[:10],
    })


def simular(request):
    if request.method != 'POST':
        return redirect('monitor:live')

    cenario = request.POST.get('cenario')
    if cenario == 'pico':
        resultado = simulacao.simular_pico_incidentes()
        if resultado:
            messages.success(
                request,
                f'Pico simulado: {resultado["quantidade"]} incidentes elegíveis em {resultado["produto"]}.',
            )
    elif cenario == 'degradacao':
        resultado = simulacao.simular_degradacao_servico()
        if resultado:
            messages.warning(
                request,
                f'Degradação simulada em {resultado["produto"]}: incidente {resultado["incidente_pai"]} '
                f'com {resultado["filhos"]} desdobramentos.',
            )
    elif cenario == 'limpar':
        total = simulacao.limpar_simulacoes()
        messages.info(request, f'{total} incidentes simulados removidos. Base voltou aos dados reais.')
    else:
        messages.error(request, 'Cenário de simulação inválido.')

    return redirect('monitor:live')
