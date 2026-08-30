"""Operational Health Score.

Score 0–100 composto por cinco fatores, cada um com peso explícito. O ponto central é que o
score nunca aparece sozinho: ele sempre vem acompanhado da contribuição de cada fator, para que
a pergunta "por que caiu?" tenha resposta direta em vez de exigir garimpo em outras telas.

Cada fator é uma penalidade de 0 a 1 (0 = saudável, 1 = pior caso), multiplicada pelo seu peso.
    score = 100 − Σ (penalidade_do_fator × peso_do_fator)

Os limiares abaixo são calibrados sobre o próprio histórico do dataset, não arbitrados: a
taxa de violação de OLA da base é ~1%, então 5% já representa uma degradação relevante.
"""

from dataclasses import dataclass, field

from . import base
from .base import DIM

JANELA_PADRAO = 7
JANELA_BASELINE = 28


@dataclass
class Fator:
    slug: str
    nome: str
    peso: int
    penalidade: float           # 0..1
    valor_exibido: str
    explicacao: str
    detalhe_calculo: str

    @property
    def pontos_perdidos(self):
        return round(self.penalidade * self.peso, 1)


@dataclass
class Health:
    score: int
    faixa: str
    rotulo: str
    fatores: list = field(default_factory=list)
    variacao: float | None = None
    janela_dias: int = JANELA_PADRAO

    @property
    def fatores_ordenados(self):
        return sorted(self.fatores, key=lambda f: f.pontos_perdidos, reverse=True)

    @property
    def principal_ofensor(self):
        ordenados = self.fatores_ordenados
        return ordenados[0] if ordenados and ordenados[0].pontos_perdidos > 0 else None


def _faixa(score):
    if score >= 85:
        return 'healthy', 'Saudável'
    if score >= 70:
        return 'attention', 'Requer atenção'
    if score >= 50:
        return 'warning', 'Degradado'
    return 'critical', 'Crítico'


def _limitar(valor):
    return max(0.0, min(1.0, valor))


def calcular(janela_dias=JANELA_PADRAO):
    atual = base.agregado(janela_dias)
    baseline = base.agregado(JANELA_BASELINE)

    fatores = []

    # 1) Violação de OLA (peso 30) — impacto contratual direto, o fator mais caro.
    taxa_violacao = base.taxa(atual['violacoes'], atual['total_kpi'])
    fatores.append(Fator(
        slug='ola',
        nome='Violação de OLA',
        peso=30,
        penalidade=_limitar(taxa_violacao / 0.05),
        valor_exibido=f'{taxa_violacao * 100:.1f}%',
        explicacao=(
            f"{atual['violacoes']} de {atual['total_kpi']} incidentes elegíveis violaram o OLA "
            f'nos últimos {janela_dias} dias.'
        ),
        detalhe_calculo='penalidade = taxa_violação ÷ 5% (limiar de degradação relevante)',
    ))

    # 2) Volume elegível vs. baseline (peso 25) — mede pressão real, ignorando ruído de monitoramento.
    media_atual = base.taxa(atual['total_kpi'], janela_dias)
    media_baseline = base.taxa(baseline['total_kpi'], JANELA_BASELINE)
    razao = base.taxa(media_atual, media_baseline)
    fatores.append(Fator(
        slug='volume',
        nome='Volume elegível vs. baseline',
        peso=25,
        penalidade=_limitar((razao - 1) / 0.5) if razao > 1 else 0.0,
        valor_exibido=f'{media_atual:.0f}/dia',
        explicacao=(
            f'Média de {media_atual:.1f} incidentes elegíveis por dia, contra baseline de '
            f'{media_baseline:.1f}/dia nos últimos {JANELA_BASELINE} dias.'
        ),
        detalhe_calculo='penalidade = (média_atual ÷ baseline − 1) ÷ 0,5 · só penaliza aumento',
    ))

    # 3) Severidade (peso 20) — a mesma quantidade de incidentes pesa mais se for P1/P2.
    share_criticos = base.taxa(atual['criticos'], atual['total_kpi'])
    fatores.append(Fator(
        slug='severidade',
        nome='Participação de críticos (P1/P2)',
        peso=20,
        penalidade=_limitar(share_criticos / 0.5),
        valor_exibido=f'{share_criticos * 100:.0f}%',
        explicacao=(
            f"{atual['criticos']} dos {atual['total_kpi']} incidentes elegíveis são de "
            'prioridade crítica ou alta.'
        ),
        detalhe_calculo='penalidade = share_P1P2 ÷ 50%',
    ))

    # 4) Tendência (peso 15) — direção importa: piorar rápido é pior que estar parado alto.
    serie = base.serie_diaria(janela_dias * 2, campo='total_kpi')
    metade = len(serie) // 2
    recente = base.media(v for _, v in serie[metade:])
    anterior = base.media(v for _, v in serie[:metade])
    variacao = base.variacao_percentual(recente, anterior)
    fatores.append(Fator(
        slug='tendencia',
        nome='Tendência de volume',
        peso=15,
        penalidade=_limitar((variacao or 0) / 40) if (variacao or 0) > 0 else 0.0,
        valor_exibido=f'{variacao:+.1f}%' if variacao is not None else '—',
        explicacao=(
            f'Média diária elegível passou de {anterior:.1f} para {recente:.1f} '
            f'entre os dois últimos períodos de {janela_dias} dias.'
        ),
        detalhe_calculo='penalidade = variação_% ÷ 40% · só penaliza crescimento',
    ))

    # 5) MTTR (peso 10) — quanto mais lento resolver, maior o risco de estourar o próximo OLA.
    mttr_atual = base.taxa(atual['soma_mttr_seg'], atual['resolvidos']) / 60
    mttr_baseline = base.taxa(baseline['soma_mttr_seg'], baseline['resolvidos']) / 60
    razao_mttr = base.taxa(mttr_atual, mttr_baseline)
    fatores.append(Fator(
        slug='mttr',
        nome='MTTR vs. baseline',
        peso=10,
        penalidade=_limitar((razao_mttr - 1) / 0.5) if razao_mttr > 1 else 0.0,
        valor_exibido=f'{mttr_atual:.0f} min',
        explicacao=(
            f'Tempo médio de resolução de {mttr_atual:.0f} min, contra baseline de '
            f'{mttr_baseline:.0f} min.'
        ),
        detalhe_calculo='penalidade = (MTTR_atual ÷ MTTR_baseline − 1) ÷ 0,5',
    ))

    score = round(100 - sum(f.pontos_perdidos for f in fatores))
    score = max(0, min(100, score))
    faixa, rotulo = _faixa(score)

    return Health(
        score=score,
        faixa=faixa,
        rotulo=rotulo,
        fatores=fatores,
        variacao=_variacao_score(janela_dias, score),
        janela_dias=janela_dias,
    )


def _variacao_score(janela_dias, score_atual):
    """Compara com o score que teríamos calculado no período anterior."""
    from datetime import timedelta

    from apps.core.tempo import data_referencia

    ate = data_referencia() - timedelta(days=janela_dias)
    anterior = base.agregado(janela_dias, ate=ate)
    if not anterior['total_kpi']:
        return None

    taxa_violacao = base.taxa(anterior['violacoes'], anterior['total_kpi'])
    share_criticos = base.taxa(anterior['criticos'], anterior['total_kpi'])
    penalidade = _limitar(taxa_violacao / 0.05) * 30 + _limitar(share_criticos / 0.5) * 20
    score_anterior = max(0, min(100, round(100 - penalidade)))
    return score_atual - score_anterior
