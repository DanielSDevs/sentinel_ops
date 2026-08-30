"""Anomaly Detection.

Detecta desvios do comportamento normal comparando cada dia com a **própria baseline** da série
(z-score sobre uma janela móvel anterior). Cada dimensão tem seu próprio normal — 40 incidentes/dia
pode ser rotina para um produto e uma anomalia grave para outro.

A saída inclui sempre o comportamento normal esperado, o observado e o desvio, porque
"anomalia detectada" sozinho não permite decidir nada.
"""

from dataclasses import dataclass, field

from . import base
from .base import DIM

JANELA_ANALISE = 30
JANELA_BASELINE = 21
Z_ATENCAO = 2.0
Z_ANOMALIA = 3.0


@dataclass
class PontoTimeline:
    data: object
    valor: int
    z: float
    estado: str    # normal | atencao | anomalia


@dataclass
class Anomalia:
    dimensao: str
    chave: str
    data: object
    observado: int
    esperado_min: int
    esperado_max: int
    desvio_pct: float
    z: float
    confianca: int
    correlacoes: list = field(default_factory=list)
    timeline: list = field(default_factory=list)

    @property
    def faixa(self):
        return 'critical' if self.z >= Z_ANOMALIA else 'warning'

    @property
    def rotulo(self):
        return 'Anomalia' if self.z >= Z_ANOMALIA else 'Atenção'


def _confianca(z):
    """Confiança derivada do próprio z-score (regra empírica normal), não arbitrada.

    z=2 → ~95%, z=3 → ~99,7%. Limitada a 99% para não sugerir certeza absoluta.
    """
    if z >= 3:
        return 99
    if z >= 2.5:
        return 98
    if z >= 2:
        return 95
    return round(max(0, min(95, (z / 2) * 95)))


def _estado(z):
    if z >= Z_ANOMALIA:
        return 'anomalia'
    if z >= Z_ATENCAO:
        return 'atencao'
    return 'normal'


def _analisar_serie(serie, dimensao, chave):
    """Percorre a série calculando z-score contra a baseline móvel anterior a cada ponto."""
    timeline, anomalias = [], []

    for i, (data, valor) in enumerate(serie):
        anteriores = [v for _, v in serie[max(0, i - JANELA_BASELINE):i]]
        if len(anteriores) < 7:
            timeline.append(PontoTimeline(data, valor, 0.0, 'normal'))
            continue

        m = base.media(anteriores)
        dp = base.desvio_padrao(anteriores)
        z = ((valor - m) / dp) if dp else 0.0
        timeline.append(PontoTimeline(data, valor, round(z, 2), _estado(z)))

        if z >= Z_ATENCAO:
            anomalias.append(Anomalia(
                dimensao=dimensao,
                chave=chave,
                data=data,
                observado=valor,
                esperado_min=max(0, round(m - dp)),
                esperado_max=round(m + dp),
                desvio_pct=round(((valor - m) / m * 100) if m else 0, 1),
                z=round(z, 2),
                confianca=_confianca(z),
            ))

    return timeline, anomalias


def detectar(dimensao=DIM.GLOBAL, chave='', campo='total_kpi'):
    serie = base.serie_diaria(JANELA_ANALISE + JANELA_BASELINE, dimensao, chave, campo=campo)
    timeline, anomalias = _analisar_serie(serie, dimensao, chave)
    recorte = timeline[-JANELA_ANALISE:]
    for anomalia in anomalias:
        anomalia.timeline = recorte
    return recorte, anomalias


def detectar_por_dimensao(dimensao, limite=6):
    """Varre todas as chaves de uma dimensão e devolve as anomalias mais recentes/severas."""
    chaves = {
        linha['chave'] for linha in base.agregado_por_chave(JANELA_ANALISE, dimensao, minimo_total=30)
    }
    encontradas = []
    for chave in chaves:
        _timeline, anomalias = detectar(dimensao, chave)
        encontradas.extend(anomalias)

    encontradas.sort(key=lambda a: (a.data, a.z), reverse=True)
    return encontradas[:limite]


def correlacionar(anomalia):
    """Procura famílias de sinal que dispararam junto com a anomalia, no mesmo dia.

    É uma correlação temporal real (co-ocorrência acima da própria baseline), não uma
    relação causal — a UI precisa apresentar como pista, não como diagnóstico.
    """
    from apps.core.models import FamiliaSinal

    nomes = dict(FamiliaSinal.objects.values_list('slug', 'nome'))
    achados = []

    for linha in base.agregado_por_chave(1, DIM.FAMILIA, ate=anomalia.data, minimo_total=5):
        slug = linha['chave']
        serie = base.serie_diaria(JANELA_BASELINE, DIM.FAMILIA, slug, ate=anomalia.data)
        anteriores = [v for _, v in serie[:-1]]
        if len(anteriores) < 7:
            continue
        m = base.media(anteriores)
        dp = base.desvio_padrao(anteriores)
        atual = serie[-1][1]
        z = ((atual - m) / dp) if dp else 0.0
        if z >= Z_ATENCAO:
            achados.append({
                'slug': slug,
                'nome': nomes.get(slug, slug),
                'valor': atual,
                'z': round(z, 2),
                'desvio_pct': round(((atual - m) / m * 100) if m else 0, 1),
            })

    achados.sort(key=lambda a: a['z'], reverse=True)
    return achados[:4]
