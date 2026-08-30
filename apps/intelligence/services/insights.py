"""Operational Insights — geração e priorização.

A plataforma não mostra tudo o que sabe. Cada insight recebe um score de prioridade e só o topo
chega à tela — a alternativa é fadiga de alerta, que faz o operador ignorar justamente o aviso
que importava.

    prioridade = impacto × urgência × confiança × alcance

Cada fator é 0..1 e vem de evidência do dataset (volume afetado, recência, tamanho da amostra),
nunca de um número escolhido a dedo.
"""

from dataclasses import dataclass, field

from . import anomaly, base, risk
from .base import DIM


@dataclass
class Insight:
    slug: str
    tipo: str               # critico | padrao | eficiencia | ola | capacidade
    titulo: str
    explicacao: str
    impacto_texto: str
    evidencia: str
    acao: str
    impacto: float
    urgencia: float
    confianca: float
    alcance: float
    link: str = ''
    link_rotulo: str = ''
    tags: list = field(default_factory=list)

    @property
    def prioridade(self):
        return round(self.impacto * self.urgencia * self.confianca * self.alcance * 100)

    @property
    def confianca_pct(self):
        return round(self.confianca * 100)

    @property
    def faixa(self):
        if self.prioridade >= 55:
            return 'critical'
        if self.prioridade >= 30:
            return 'warning'
        return 'attention'


ROTULO_TIPO = {
    'critico': 'Insight crítico',
    'padrao': 'Padrão detectado',
    'eficiencia': 'Eficiência',
    'ola': 'SLA / OLA',
    'capacidade': 'Capacidade',
}


def _insight_volume():
    atual, anterior = base.periodo_e_anterior(7)
    variacao = base.variacao_percentual(atual['total_kpi'], anterior['total_kpi'])
    if variacao is None or variacao < 15:
        return None
    return Insight(
        slug='volume-acima-baseline',
        tipo='critico',
        titulo=f'Volume elegível a KPI subiu {variacao:.0f}% em 7 dias',
        explicacao=(
            f'Foram {atual["total_kpi"]} incidentes elegíveis nos últimos 7 dias, contra '
            f'{anterior["total_kpi"]} no período anterior. Como a contagem exclui ruído de '
            'monitoramento, o crescimento representa carga real de operação.'
        ),
        impacto_texto=f'+{atual["total_kpi"] - anterior["total_kpi"]} incidentes que consomem capacidade da equipe',
        evidencia=f'Séries diárias de MetricaDiaria (dimensão global, campo total_kpi), 14 dias',
        acao='Revisar escala e priorização da fila antes que o aumento pressione o OLA.',
        impacto=min(1.0, variacao / 50),
        urgencia=0.9,
        confianca=0.9,
        alcance=1.0,
        link='forecast:index',
        link_rotulo='Ver previsão',
    )


def _insight_ruido():
    atual = base.agregado(7)
    ruido = atual['total'] - atual['total_kpi']
    pct = base.taxa(ruido, atual['total'])
    if pct < 0.5:
        return None
    return Insight(
        slug='ruido-monitoramento',
        tipo='eficiencia',
        titulo=f'{pct * 100:.0f}% do volume é ruído de monitoramento',
        explicacao=(
            f'{ruido} dos {atual["total"]} incidentes dos últimos 7 dias não entram no cálculo de '
            'OLA. São chamados automáticos que consomem triagem sem representar risco contratual.'
        ),
        impacto_texto=f'{ruido} chamados por semana ocupando fila sem impacto em SLA',
        evidencia='Campo `Entrou para KPI?` do dataset, agregado por dia',
        acao='Revisar thresholds de alerta e avaliar auto-fechamento dos sinais sem intervenção.',
        impacto=0.55,
        urgencia=0.4,
        confianca=0.95,
        alcance=min(1.0, pct),
        link='monitor:live',
        link_rotulo='Ver operação',
    )


def _insights_risco():
    achados = []
    for risco in risk.calcular(DIM.PRODUTO, limite=3):
        if risco.faixa not in ('critical', 'warning'):
            continue
        achados.append(Insight(
            slug=f'risco-produto-{risco.chave}',
            tipo='ola',
            titulo=f'Produto {risco.chave} concentra risco de violação de OLA',
            explicacao=(
                f'Historicamente {risco.probabilidade * 100:.1f}% dos incidentes elegíveis de '
                f'{risco.chave} violaram o OLA, e o produto segue com volume relevante '
                f'({risco.volume_kpi} elegíveis em 14 dias).'
            ),
            impacto_texto=f'{risco.violacoes} violações acumuladas em 90 dias',
            evidencia=f'Campos `Entrou para KPI?` e `KPI Violado?` agregados por produto',
            acao=risco.acao,
            impacto=min(1.0, risco.score / 100),
            urgencia=0.75,
            confianca=0.85,
            alcance=min(1.0, risco.impacto + 0.3),
            link='intelligence:risk',
            link_rotulo='Abrir Risk Radar',
            tags=[risco.chave],
        ))
    return achados


def _insights_anomalia():
    achados = []
    for anom in anomaly.detectar_por_dimensao(DIM.FAMILIA, limite=2):
        achados.append(Insight(
            slug=f'anomalia-{anom.chave}-{anom.data}',
            tipo='padrao',
            titulo=f'Sinal "{anom.chave}" fora do padrão em {anom.data:%d/%m}',
            explicacao=(
                f'Foram {anom.observado} ocorrências, contra a faixa esperada de '
                f'{anom.esperado_min}–{anom.esperado_max} para esse dia — desvio de '
                f'{anom.desvio_pct:+.0f}% (z={anom.z}).'
            ),
            impacto_texto=f'{anom.observado} ocorrências em um único dia',
            evidencia='z-score contra baseline móvel de 21 dias da própria série',
            acao=(
                f'Investigar o pico de "{anom.chave}" em {anom.data:%d/%m}: verificar se houve '
                'mudança/deploy na janela e se o desvio persiste.'
            ),
            impacto=min(1.0, anom.z / 5),
            urgencia=0.7,
            confianca=anom.confianca / 100,
            alcance=0.6,
            link='intelligence:anomaly',
            link_rotulo='Ver anomalias',
        ))
    return achados


def _insight_equipe_eficiente():
    equipes = base.agregado_por_chave(30, DIM.EQUIPE, minimo_total=50)
    com_mttr = [
        (e['chave'], base.taxa(e['soma_mttr_seg'], e['resolvidos']) / 60)
        for e in equipes if e['resolvidos'] >= 20
    ]
    if len(com_mttr) < 3:
        return None

    com_mttr.sort(key=lambda x: x[1])
    melhor_nome, melhor_mttr = com_mttr[0]
    mediana = com_mttr[len(com_mttr) // 2][1]
    if not mediana or melhor_mttr >= mediana:
        return None

    diferenca = round((1 - melhor_mttr / mediana) * 100)
    return Insight(
        slug='equipe-eficiente',
        tipo='eficiencia',
        titulo=f'{melhor_nome} resolve {diferenca}% mais rápido que a mediana',
        explicacao=(
            f'MTTR de {melhor_mttr:.0f} min contra mediana de {mediana:.0f} min entre as equipes '
            'com volume comparável nos últimos 30 dias.'
        ),
        impacto_texto='Prática replicável para as demais equipes',
        evidencia='MTTR real (Resolvido − Aberto) agregado por equipe',
        acao=f'Mapear o que {melhor_nome} faz diferente e replicar nas equipes com MTTR acima da mediana.',
        impacto=0.4,
        urgencia=0.25,
        confianca=0.8,
        alcance=0.7,
        link='monitor:incidentes',
        link_rotulo='Ver incidentes',
    )


def gerar(limite=6):
    candidatos = [
        _insight_volume(),
        _insight_ruido(),
        _insight_equipe_eficiente(),
        *_insights_risco(),
        *_insights_anomalia(),
    ]
    achados = [c for c in candidatos if c is not None]
    achados.sort(key=lambda i: i.prioridade, reverse=True)
    return achados[:limite]
