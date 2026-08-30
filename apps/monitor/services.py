"""Ops Monitor — saúde de serviços, operação ao vivo e inteligência de incidentes."""

from datetime import timedelta

from django.db.models import Count

from apps.core.models import Incidente, MetricaDiaria
from apps.core.tempo import data_referencia, referencia_temporal
from apps.intelligence.services import base
from apps.intelligence.services.base import DIM

JANELA_SAUDE = 14
JANELA_BASELINE = 60


def _faixa_saude(score):
    if score >= 85:
        return 'healthy', 'Saudável'
    if score >= 70:
        return 'attention', 'Requer atenção'
    if score >= 50:
        return 'warning', 'Degradado'
    return 'critical', 'Crítico'


def mapa_saude_servicos(limite=12):
    """Health score por produto — o Service Health Map.

    O score usa os mesmos princípios do Operational Health global (violação de OLA, severidade,
    tendência), mas restrito ao produto, para que a leitura seja comparável entre as duas telas.
    """
    recentes = {l['chave']: l for l in base.agregado_por_chave(JANELA_SAUDE, DIM.PRODUTO, minimo_total=1)}
    historicos = {l['chave']: l for l in base.agregado_por_chave(JANELA_BASELINE, DIM.PRODUTO, minimo_total=1)}

    servicos = []
    for chave, recente in recentes.items():
        historico = historicos.get(chave, recente)
        if recente['total'] < 5:
            continue

        taxa_violacao = base.taxa(historico['violacoes'], historico['total_kpi'])
        share_criticos = base.taxa(recente['criticos'], recente['total_kpi'])

        serie = base.serie_diaria(JANELA_SAUDE * 2, DIM.PRODUTO, chave, campo='total_kpi')
        metade = len(serie) // 2
        recente_media = base.media(v for _, v in serie[metade:])
        anterior_media = base.media(v for _, v in serie[:metade])
        tendencia = base.variacao_percentual(recente_media, anterior_media)

        penalidade = (
            min(1.0, taxa_violacao / 0.05) * 45
            + min(1.0, share_criticos / 0.5) * 30
            + (min(1.0, (tendencia or 0) / 50) * 25 if (tendencia or 0) > 0 else 0)
        )
        score = max(0, min(100, round(100 - penalidade)))
        faixa, rotulo = _faixa_saude(score)

        servicos.append({
            'produto': chave,
            'score': score,
            'faixa': faixa,
            'rotulo': rotulo,
            'total': recente['total'],
            'total_kpi': recente['total_kpi'],
            'criticos': recente['criticos'],
            'violacoes': historico['violacoes'],
            'taxa_violacao': round(taxa_violacao * 100, 1),
            'tendencia': tendencia,
            'mttr_min': round(base.taxa(recente['soma_mttr_seg'], recente['resolvidos']) / 60),
            'sparkline': [v for _, v in serie[metade:]],
        })

    servicos.sort(key=lambda s: s['score'])
    return servicos[:limite]


def detalhe_servico(codigo):
    recente = base.agregado(JANELA_SAUDE, DIM.PRODUTO, codigo)
    historico = base.agregado(JANELA_BASELINE, DIM.PRODUTO, codigo)
    serie = base.serie_diaria(30, DIM.PRODUTO, codigo, campo='total_kpi')

    familias = (
        Incidente.objects
        .filter(produto__codigo=codigo, aberto_em__gte=referencia_temporal() - timedelta(days=JANELA_BASELINE))
        .values('familia_sinal__nome')
        .annotate(total=Count('id'))
        .order_by('-total')[:6]
    )
    equipes = (
        Incidente.objects
        .filter(produto__codigo=codigo, aberto_em__gte=referencia_temporal() - timedelta(days=JANELA_BASELINE))
        .values('equipe__nome')
        .annotate(total=Count('id'))
        .order_by('-total')[:5]
    )

    return {
        'codigo': codigo,
        'recente': recente,
        'historico': historico,
        'serie': serie,
        'taxa_violacao': round(base.taxa(historico['violacoes'], historico['total_kpi']) * 100, 1),
        'mttr_min': round(base.taxa(historico['soma_mttr_seg'], historico['resolvidos']) / 60),
        'familias': list(familias),
        'equipes': list(equipes),
    }


def metricas_operacionais(janela_dias=7):
    atual, anterior = base.periodo_e_anterior(janela_dias)
    ativos = Incidente.objects.filter(
        status__in=[Incidente.Status.ABERTO, Incidente.Status.AGUARDANDO],
    ).count()
    return {
        'ativos': ativos,
        'total': atual['total'],
        'total_kpi': atual['total_kpi'],
        'criticos': atual['criticos'],
        'violacoes': atual['violacoes'],
        'conformidade_ola': round((1 - base.taxa(atual['violacoes'], atual['total_kpi'])) * 100, 1),
        'ruido_pct': round(base.taxa(atual['total'] - atual['total_kpi'], atual['total']) * 100),
        'mttr_min': round(base.taxa(atual['soma_mttr_seg'], atual['resolvidos']) / 60),
        'variacao_kpi': base.variacao_percentual(atual['total_kpi'], anterior['total_kpi']),
    }


def operacao_ao_vivo(horas=24, limite=25):
    """Últimas horas de operação a partir da referência temporal do dataset."""
    desde = referencia_temporal() - timedelta(hours=horas)
    incidentes = (
        Incidente.objects
        .filter(aberto_em__gte=desde)
        .select_related('produto', 'equipe', 'familia_sinal')
        .order_by('-aberto_em')[:limite]
    )
    return list(incidentes)


def volume_por_hora(horas=24):
    desde = referencia_temporal() - timedelta(hours=horas)
    incidentes = Incidente.objects.filter(aberto_em__gte=desde).values_list('aberto_em', 'entrou_kpi')
    buckets = {}
    for momento, entrou_kpi in incidentes:
        chave = momento.replace(minute=0, second=0, microsecond=0)
        alvo = buckets.setdefault(chave, {'total': 0, 'kpi': 0})
        alvo['total'] += 1
        alvo['kpi'] += 1 if entrou_kpi else 0
    return [
        {'hora': hora, 'total': dados['total'], 'kpi': dados['kpi']}
        for hora, dados in sorted(buckets.items())
    ]


def itens_recorrentes(dias=7, minimo=5, limite=8):
    """Ativos que repetem incidentes — instabilidade crônica que costuma passar batida."""
    desde = referencia_temporal() - timedelta(days=dias)
    linhas = (
        Incidente.objects
        .filter(aberto_em__gte=desde, item_configuracao__isnull=False)
        .values('item_configuracao__codigo', 'produto__codigo', 'familia_sinal__nome')
        .annotate(total=Count('id'))
        .filter(total__gte=minimo)
        .order_by('-total')[:limite]
    )
    return list(linhas)


def cobertura_dataset():
    """Metadados reais da base carregada — alimenta a tela Data Sources."""
    total = Incidente.objects.count()
    if not total:
        return None
    primeiro = Incidente.objects.order_by('aberto_em').values_list('aberto_em', flat=True).first()
    ultimo = Incidente.objects.order_by('-aberto_em').values_list('aberto_em', flat=True).first()
    return {
        'total': total,
        'inicio': primeiro,
        'fim': ultimo,
        'elegiveis_kpi': Incidente.objects.filter(entrou_kpi=True).count(),
        'violacoes': Incidente.objects.filter(kpi_violado=True).count(),
        'com_pai': Incidente.objects.filter(incidente_pai__isnull=False).count(),
        'sem_produto': Incidente.objects.filter(produto__isnull=True).count(),
        'sem_categoria': Incidente.objects.filter(categoria='').count(),
        'simulados': Incidente.objects.filter(origem=Incidente.Origem.SIMULACAO).count(),
        'linhas_metrica': MetricaDiaria.objects.count(),
        'referencia': data_referencia(),
    }
