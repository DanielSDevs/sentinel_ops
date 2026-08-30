"""Risk Radar — riscos ordenados por impacto potencial × probabilidade.

A ordenação deliberadamente **não** é por volume de incidentes: um produto com 500 chamados de
baixa severidade e OLA saudável representa menos risco que um com 40 chamados e histórico de
violação. O score combina as duas dimensões:

    risco = probabilidade × impacto

- probabilidade: taxa histórica de violação de OLA da entidade (evidência direta do dataset)
- impacto: volume elegível recente normalizado × peso de severidade (share de P1/P2)

Ambos os componentes ficam visíveis na UI para que a ordenação seja auditável.
"""

from dataclasses import dataclass

from . import base
from .base import DIM

JANELA_RECENTE = 14
JANELA_HISTORICO = 90
VOLUME_MINIMO = 20


@dataclass
class Risco:
    dimensao: str
    chave: str
    score: int
    faixa: str
    probabilidade: float      # 0..1
    impacto: float            # 0..1
    volume_kpi: int
    violacoes: int
    criticos: int
    drivers: list
    acao: str

    @property
    def rotulo_faixa(self):
        return {'critical': 'Crítico', 'warning': 'Alto', 'attention': 'Moderado', 'healthy': 'Baixo'}[self.faixa]


def _faixa(score):
    if score >= 70:
        return 'critical'
    if score >= 45:
        return 'warning'
    if score >= 20:
        return 'attention'
    return 'healthy'


def _acao_sugerida(dimensao, chave, probabilidade, share_criticos):
    if probabilidade >= 0.1:
        return (
            f'Revisar o fluxo de atendimento de {chave}: a taxa histórica de violação de OLA está '
            'muito acima da média da operação.'
        )
    if share_criticos >= 0.4:
        return f'Avaliar capacidade da equipe alocada em {chave} — concentração alta de P1/P2.'
    if dimensao == DIM.FAMILIA:
        return f'Investigar causa raiz recorrente do sintoma "{chave}" e avaliar automação de resposta.'
    return f'Monitorar {chave}: volume elegível relevante, sem violação sistemática até aqui.'


def calcular(dimensao=DIM.PRODUTO, limite=8):
    recentes = {
        linha['chave']: linha
        for linha in base.agregado_por_chave(JANELA_RECENTE, dimensao, minimo_total=1)
    }
    historicos = {
        linha['chave']: linha
        for linha in base.agregado_por_chave(JANELA_HISTORICO, dimensao, minimo_total=1)
    }
    if not recentes:
        return []

    volume_maximo = max((l['total_kpi'] for l in recentes.values()), default=0) or 1
    riscos = []

    for chave, recente in recentes.items():
        historico = historicos.get(chave, recente)
        if historico['total_kpi'] < VOLUME_MINIMO:
            continue  # amostra pequena demais para estimar probabilidade com honestidade

        probabilidade = base.taxa(historico['violacoes'], historico['total_kpi'])
        share_criticos = base.taxa(recente['criticos'], recente['total_kpi'])
        volume_normalizado = base.taxa(recente['total_kpi'], volume_maximo)
        impacto = min(1.0, volume_normalizado * (0.5 + share_criticos))

        score = round(min(1.0, probabilidade * 6 + impacto * 0.5) * 100)
        if score <= 0:
            continue

        drivers = []
        if probabilidade > 0:
            drivers.append(
                f'{historico["violacoes"]} violações de OLA em {historico["total_kpi"]} '
                f'incidentes elegíveis ({probabilidade * 100:.1f}%) nos últimos {JANELA_HISTORICO} dias'
            )
        if share_criticos >= 0.25:
            drivers.append(f'{share_criticos * 100:.0f}% do volume recente é P1/P2')
        if recente['total_kpi'] >= volume_maximo * 0.5:
            drivers.append(f'{recente["total_kpi"]} incidentes elegíveis nos últimos {JANELA_RECENTE} dias')
        mttr = base.taxa(recente['soma_mttr_seg'], recente['resolvidos']) / 60
        if mttr > 0:
            drivers.append(f'MTTR recente de {mttr:.0f} min')

        riscos.append(Risco(
            dimensao=dimensao,
            chave=chave,
            score=score,
            faixa=_faixa(score),
            probabilidade=probabilidade,
            impacto=impacto,
            volume_kpi=recente['total_kpi'],
            violacoes=historico['violacoes'],
            criticos=recente['criticos'],
            drivers=drivers,
            acao=_acao_sugerida(dimensao, chave, probabilidade, share_criticos),
        ))

    riscos.sort(key=lambda r: r.score, reverse=True)
    return riscos[:limite]


def panorama():
    """Riscos das três dimensões, para a tela do Risk Radar."""
    return {
        'produtos': calcular(DIM.PRODUTO),
        'equipes': calcular(DIM.EQUIPE),
        'familias': calcular(DIM.FAMILIA),
    }
