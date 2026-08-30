"""Acesso às séries agregadas — base de toda a camada de inferência.

Tudo aqui lê `MetricaDiaria` (materializada na importação), não a tabela de 122k incidentes.
Isso mantém as páginas rápidas e garante que todos os módulos partam exatamente dos mesmos
números — se o Risk Radar e o Service Health discordassem, a plataforma perderia credibilidade.
"""

from datetime import timedelta

from django.db.models import Sum

from apps.core.models import MetricaDiaria
from apps.core.tempo import data_referencia

CAMPOS = ('total', 'total_kpi', 'violacoes', 'criticos', 'resolvidos', 'soma_mttr_seg')

DIM = MetricaDiaria.Dimensao


def _intervalo(dias, ate=None):
    fim = ate or data_referencia()
    return fim - timedelta(days=dias - 1), fim


def serie_diaria(dias, dimensao=DIM.GLOBAL, chave='', campo='total', ate=None):
    """Série densa (dias sem registro viram 0) — evita buracos que distorcem médias e z-scores."""
    inicio, fim = _intervalo(dias, ate)
    registros = dict(
        MetricaDiaria.objects
        .filter(dimensao=dimensao, chave=chave, data__gte=inicio, data__lte=fim)
        .values_list('data', campo)
    )
    return [
        (inicio + timedelta(days=i), registros.get(inicio + timedelta(days=i), 0))
        for i in range((fim - inicio).days + 1)
    ]


def agregado(dias, dimensao=DIM.GLOBAL, chave='', ate=None):
    """Soma dos campos no período."""
    inicio, fim = _intervalo(dias, ate)
    resultado = (
        MetricaDiaria.objects
        .filter(dimensao=dimensao, chave=chave, data__gte=inicio, data__lte=fim)
        .aggregate(**{campo: Sum(campo) for campo in CAMPOS})
    )
    return {campo: (resultado.get(campo) or 0) for campo in CAMPOS}


def agregado_por_chave(dias, dimensao, ate=None, minimo_total=0):
    """Soma dos campos por chave da dimensão (todos os produtos, todas as equipes, …)."""
    inicio, fim = _intervalo(dias, ate)
    linhas = (
        MetricaDiaria.objects
        .filter(dimensao=dimensao, data__gte=inicio, data__lte=fim)
        .values('chave')
        .annotate(**{campo: Sum(campo) for campo in CAMPOS})
    )
    return [
        {'chave': linha['chave'], **{campo: (linha.get(campo) or 0) for campo in CAMPOS}}
        for linha in linhas
        if (linha.get('total') or 0) >= minimo_total
    ]


def media(valores):
    valores = list(valores)
    return sum(valores) / len(valores) if valores else 0.0


def desvio_padrao(valores):
    valores = list(valores)
    if len(valores) < 2:
        return 0.0
    m = media(valores)
    return (sum((v - m) ** 2 for v in valores) / (len(valores) - 1)) ** 0.5


def variacao_percentual(atual, anterior):
    """Variação relativa. Retorna None quando não há base de comparação (evita '+100%' enganoso)."""
    if not anterior:
        return None
    return round(((atual - anterior) / anterior) * 100, 1)


def taxa(numerador, denominador):
    return (numerador / denominador) if denominador else 0.0


def periodo_e_anterior(dias, dimensao=DIM.GLOBAL, chave=''):
    """Agregados do período atual e do período imediatamente anterior, do mesmo tamanho."""
    atual = agregado(dias, dimensao, chave)
    fim_anterior = data_referencia() - timedelta(days=dias)
    anterior = agregado(dias, dimensao, chave, ate=fim_anterior)
    return atual, anterior
