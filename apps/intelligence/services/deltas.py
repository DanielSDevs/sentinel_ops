"""What Changed — comparação período vs. período anterior nas métricas que movem decisão."""

from dataclasses import dataclass

from . import base


@dataclass
class Delta:
    slug: str
    rotulo: str
    valor_atual: str
    variacao: float | None
    direcao: str          # 'subiu' | 'caiu' | 'estavel'
    sentimento: str       # 'bom' | 'ruim' | 'neutro'
    explicacao: str

    @property
    def seta(self):
        return {'subiu': '↑', 'caiu': '↓', 'estavel': '→'}[self.direcao]


def _direcao(variacao, limiar=2.0):
    if variacao is None or abs(variacao) < limiar:
        return 'estavel'
    return 'subiu' if variacao > 0 else 'caiu'


def _sentimento(direcao, subir_e_ruim=True):
    if direcao == 'estavel':
        return 'neutro'
    subiu = direcao == 'subiu'
    return 'ruim' if subiu == subir_e_ruim else 'bom'


def calcular(janela_dias=7):
    atual, anterior = base.periodo_e_anterior(janela_dias)
    deltas = []

    # Volume elegível a KPI — o sinal que importa, não o volume bruto.
    var = base.variacao_percentual(atual['total_kpi'], anterior['total_kpi'])
    direcao = _direcao(var)
    deltas.append(Delta(
        slug='volume_kpi',
        rotulo='Volume elegível a KPI',
        valor_atual=f"{atual['total_kpi']}",
        variacao=var,
        direcao=direcao,
        sentimento=_sentimento(direcao),
        explicacao=f"{atual['total_kpi']} incidentes elegíveis contra {anterior['total_kpi']} no período anterior.",
    ))

    # Taxa de violação de OLA. A explicação precisa mostrar numerador E denominador: com poucas
    # violações em termos absolutos, a taxa pode disparar só porque o volume elegível encolheu —
    # apresentar "+103%" sem esse contexto assustaria o leitor por um motivo errado.
    taxa_atual = base.taxa(atual['violacoes'], atual['total_kpi']) * 100
    taxa_anterior = base.taxa(anterior['violacoes'], anterior['total_kpi']) * 100
    var = base.variacao_percentual(taxa_atual, taxa_anterior)
    direcao = _direcao(var, limiar=5.0)
    explicacao_ola = (
        f"{atual['violacoes']} violações em {atual['total_kpi']} elegíveis, contra "
        f"{anterior['violacoes']} em {anterior['total_kpi']} no período anterior."
    )
    if atual['violacoes'] <= 5 and anterior['violacoes'] <= 5:
        explicacao_ola += ' Números absolutos baixos — a variação percentual oscila muito.'
    deltas.append(Delta(
        slug='ola',
        rotulo='Taxa de violação de OLA',
        valor_atual=f'{taxa_atual:.1f}%',
        variacao=var,
        direcao=direcao,
        sentimento=_sentimento(direcao),
        explicacao=explicacao_ola,
    ))

    # Incidentes críticos.
    var = base.variacao_percentual(atual['criticos'], anterior['criticos'])
    direcao = _direcao(var)
    deltas.append(Delta(
        slug='criticos',
        rotulo='Incidentes críticos (P1/P2)',
        valor_atual=f"{atual['criticos']}",
        variacao=var,
        direcao=direcao,
        sentimento=_sentimento(direcao),
        explicacao=f"{atual['criticos']} críticos contra {anterior['criticos']} no período anterior.",
    ))

    # MTTR — cair é bom.
    mttr_atual = base.taxa(atual['soma_mttr_seg'], atual['resolvidos']) / 60
    mttr_anterior = base.taxa(anterior['soma_mttr_seg'], anterior['resolvidos']) / 60
    var = base.variacao_percentual(mttr_atual, mttr_anterior)
    direcao = _direcao(var)
    deltas.append(Delta(
        slug='mttr',
        rotulo='MTTR',
        valor_atual=f'{mttr_atual:.0f} min',
        variacao=var,
        direcao=direcao,
        sentimento=_sentimento(direcao),
        explicacao=f'Tempo médio de resolução passou de {mttr_anterior:.0f} para {mttr_atual:.0f} minutos.',
    ))

    # Ruído de monitoramento — contexto, não alarme.
    ruido_atual = atual['total'] - atual['total_kpi']
    pct_atual = base.taxa(ruido_atual, atual['total']) * 100
    ruido_anterior = anterior['total'] - anterior['total_kpi']
    pct_anterior = base.taxa(ruido_anterior, anterior['total']) * 100
    var = base.variacao_percentual(pct_atual, pct_anterior)
    deltas.append(Delta(
        slug='ruido',
        rotulo='Ruído de monitoramento',
        valor_atual=f'{pct_atual:.0f}% do volume',
        variacao=var,
        direcao=_direcao(var),
        sentimento='neutro',
        explicacao=(
            f'{ruido_atual} dos {atual["total"]} incidentes do período não entram no cálculo de OLA.'
        ),
    ))

    return deltas
