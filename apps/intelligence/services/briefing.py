"""Briefing operacional — o resumo do turno montado a partir dos números reais.

Este módulo **não** usa modelo de linguagem. Ele compõe frases por template, preenchidas com
valores calculados pelos outros serviços. O nome anterior ("AI Operational Briefing") dizia o
contrário e foi corrigido: chamar template de IA é vender o que não existe, e derruba a
credibilidade de tudo que a plataforma afirma. Se um dia entrar uma camada de linguagem aqui,
o rótulo pode voltar — até lá, não.

O briefing responde três perguntas na ordem em que um analista sênior as responderia:
**o que mudou → por que importa → o que fazer**.

Todas as frases são compostas por templates alimentados com valores calculados pelos demais
serviços. Não há texto pré-escrito descrevendo situações que a plataforma não mediu.
"""

from dataclasses import dataclass, field

from . import deltas as deltas_service
from . import health as health_service
from . import insights as insights_service
from . import risk
from .base import DIM


@dataclass
class Briefing:
    o_que_mudou: list = field(default_factory=list)
    por_que_importa: str = ''
    acoes: list = field(default_factory=list)
    resumo: str = ''


def _frase_mudanca(delta):
    if delta.variacao is None:
        return f'{delta.rotulo}: {delta.valor_atual} (sem base de comparação no período anterior)'
    if delta.direcao == 'estavel':
        return f'{delta.rotulo} estável em {delta.valor_atual}'
    verbo = 'subiu' if delta.direcao == 'subiu' else 'caiu'
    return f'{delta.rotulo} {verbo} {abs(delta.variacao):.0f}% — agora em {delta.valor_atual}'


def montar(janela_dias=7):
    saude = health_service.calcular(janela_dias)
    mudancas = deltas_service.calcular(janela_dias)
    riscos = risk.calcular(DIM.PRODUTO, limite=3)
    principais_insights = insights_service.gerar(limite=3)

    o_que_mudou = [
        {'texto': _frase_mudanca(d), 'sentimento': d.sentimento, 'seta': d.seta}
        for d in mudancas
    ]

    # Por que importa: conecta o principal ofensor do score ao risco concreto.
    ofensor = saude.principal_ofensor
    partes = []
    if ofensor:
        partes.append(
            f'O Operational Health está em {saude.score}/100 ({saude.rotulo.lower()}), e o fator que '
            f'mais pesa é "{ofensor.nome}": {ofensor.explicacao}'
        )
    if riscos:
        topo = riscos[0]
        partes.append(
            f'A maior concentração de risco está no produto {topo.chave} '
            f'(score {topo.score}/100), com {topo.violacoes} violações de OLA no histórico recente.'
        )
    if not partes:
        partes.append(
            f'Operação dentro do padrão esperado: Health Score em {saude.score}/100 e nenhum '
            'produto em risco relevante.'
        )
    por_que_importa = ' '.join(partes)

    # Insights diferentes podem convergir para a mesma recomendação (duas anomalias distintas
    # pedem "investigar causa comum"). Repetir a mesma linha no briefing dilui as três vagas.
    acoes, vistas = [], set()
    for insight in principais_insights:
        if insight.acao in vistas:
            continue
        vistas.add(insight.acao)
        acoes.append({
            'texto': insight.acao,
            'motivo': insight.titulo,
            'prioridade': insight.prioridade,
            'faixa': insight.faixa,
        })
    if not acoes and riscos:
        acoes = [{
            'texto': riscos[0].acao,
            'motivo': f'Produto {riscos[0].chave} com maior risco',
            'prioridade': riscos[0].score,
            'faixa': riscos[0].faixa,
        }]

    piora = [m for m in mudancas if m.sentimento == 'ruim' and m.direcao != 'estavel']
    if piora:
        resumo = (
            f'{len(piora)} indicador(es) pioraram nos últimos {janela_dias} dias. '
            f'Atenção principal: {piora[0].rotulo.lower()}.'
        )
    else:
        resumo = f'Nenhum indicador-chave piorou nos últimos {janela_dias} dias.'

    return Briefing(
        o_que_mudou=o_que_mudou,
        por_que_importa=por_que_importa,
        acoes=acoes,
        resumo=resumo,
    )
