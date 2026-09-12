"""Sentinel Copilot.

Não é um chatbot com respostas fixas nem um LLM: cada resposta é montada consultando os mesmos
serviços de inferência que alimentam as telas. Isso significa que o copiloto nunca diverge do
que o resto da plataforma mostra, e toda afirmação vem acompanhada dos números que a sustentam.

O reconhecimento de intenção é por palavra-chave — simples e previsível. Quando nada casa, o
copiloto diz o que sabe responder em vez de improvisar.
"""

import re
from dataclasses import dataclass, field

from apps.alerts.services import fila_de_decisao
from apps.forecast import services as forecast_services
from apps.intelligence.services import anomaly, deltas, health, insights, risk
from apps.intelligence.services.base import DIM
from apps.ml.services import catalogo, inferencia


@dataclass
class Resposta:
    texto: str
    pontos: list = field(default_factory=list)
    link: str = ''
    link_rotulo: str = ''


SUGESTOES = [
    'Por que o Operational Health caiu?',
    'O que mudou nas últimas 24 horas?',
    'Quais serviços têm maior risco de violar OLA?',
    'O que devo priorizar agora?',
    'Existe alguma anomalia ativa?',
    'Qual a previsão para amanhã?',
    'Quais modelos de ML estão em uso?',
    'Por que o modelo prevê esse volume?',
    'Gerar resumo executivo',
]


def _health():
    saude = health.calcular()
    pontos = [
        f'{fator.nome}: −{fator.pontos_perdidos} de {fator.peso} pts — {fator.explicacao}'
        for fator in saude.fatores_ordenados if fator.pontos_perdidos > 0
    ]
    ofensor = saude.principal_ofensor
    texto = (
        f'O Operational Health está em {saude.score}/100 ({saude.rotulo.lower()}). '
        + (f'O fator que mais pesa é "{ofensor.nome}", responsável por '
           f'{ofensor.pontos_perdidos} dos {100 - saude.score} pontos perdidos.'
           if ofensor else 'Nenhum fator está penalizando o score de forma relevante.')
    )
    return Resposta(texto, pontos or ['Nenhum fator com penalidade relevante.'],
                    'core:home', 'Ver Command Center')


def _mudancas():
    lista = deltas.calcular()
    pontos = [f'{d.rotulo}: {d.valor_atual} ({d.seta} {d.variacao}%)' if d.variacao is not None
              else f'{d.rotulo}: {d.valor_atual}' for d in lista]
    piora = [d for d in lista if d.sentimento == 'ruim' and d.direcao != 'estavel']
    texto = (
        f'{len(piora)} indicador(es) pioraram nos últimos 7 dias. Atenção principal: '
        f'{piora[0].rotulo.lower()} — {piora[0].explicacao}'
        if piora else 'Nenhum indicador-chave piorou nos últimos 7 dias.'
    )
    return Resposta(texto, pontos, 'core:home', 'Ver Command Center')


def _riscos():
    itens = risk.calcular(DIM.PRODUTO, limite=4)
    if not itens:
        return Resposta('Nenhum produto tem volume suficiente para estimar risco com confiança.')
    pontos = [
        f'{r.chave}: score {r.score}/100 · probabilidade histórica de violação '
        f'{r.probabilidade * 100:.1f}% · {r.volume_kpi} elegíveis em 14 dias'
        for r in itens
    ]
    topo = itens[0]
    texto = (
        f'O maior risco está em {topo.chave} (score {topo.score}/100). '
        f'Historicamente {topo.probabilidade * 100:.1f}% dos incidentes elegíveis desse produto '
        f'violaram o OLA, com {topo.violacoes} violações nos últimos 90 dias.'
    )
    return Resposta(texto, pontos, 'intelligence:risk', 'Abrir Risk Radar')


def _prioridades():
    acoes = fila_de_decisao(limite=4)
    if not acoes:
        return Resposta('Nenhuma ação prioritária no momento.')
    pontos = [f'P{i}: {a["titulo"]} (prioridade {a["prioridade"]})' for i, a in enumerate(acoes, 1)]
    texto = (
        f'A fila de decisão tem {len(acoes)} ação(ões) priorizadas. A primeira é: {acoes[0]["titulo"]} '
        f'— motivo: {acoes[0]["motivo"]}.'
    )
    return Resposta(texto, pontos, 'alerts:decisao', 'Abrir Decision Center')


def _anomalias():
    achadas = anomaly.detectar_por_dimensao(DIM.FAMILIA, limite=4)
    if not achadas:
        return Resposta('Nenhum desvio relevante nas famílias de sinal.')
    pontos = [
        f'{a.chave} em {a.data:%d/%m}: {a.observado} ocorrências vs. faixa esperada '
        f'{a.esperado_min}–{a.esperado_max} (confiança {a.confianca}%)'
        for a in achadas
    ]
    return Resposta(
        f'{len(achadas)} anomalia(s) detectada(s). A mais forte é "{achadas[0].chave}" '
        f'em {achadas[0].data:%d/%m}, com desvio de {achadas[0].desvio_pct:+.0f}%.',
        pontos, 'intelligence:anomaly', 'Ver Anomaly Detection',
    )


def _previsao():
    dados = forecast_services.resumo()
    if not dados:
        return Resposta('Histórico insuficiente para gerar previsão.')
    pontos = [
        f'{p["dia_semana"]} {p["data"]:%d/%m}: {p["valor"]} (faixa {p["minimo"]}–{p["maximo"]})'
        for p in dados['previsoes']
    ]
    return Resposta(forecast_services.interpretar(dados), pontos, 'forecast:index', 'Abrir Forecast Engine')


def _resumo_executivo():
    saude = health.calcular(janela_dias=30)
    riscos = risk.calcular(DIM.PRODUTO, limite=3)
    em_risco = [r for r in riscos if r.faixa in ('critical', 'warning')]
    principais = insights.gerar(limite=3)
    pontos = [f'{i.titulo} — {i.acao}' for i in principais]
    texto = (
        f'Status operacional em 30 dias: {saude.rotulo.upper()} (score {saude.score}/100). '
        f'{len(em_risco)} serviço(s) exigindo atenção'
        + (f', com destaque para {em_risco[0].chave}.' if em_risco else '.')
    )
    return Resposta(texto, pontos, 'reports:executivo', 'Abrir Executive Report')


def _modelos():
    """Qual modelo está no ar, treinado com o quê e com que resultado."""
    cartoes = catalogo.tabela_resumo()
    if not cartoes:
        return Resposta(
            'Nenhum modelo de Machine Learning treinado ainda. Rode '
            '`python manage.py treinar_modelos` para treinar sobre a base importada.',
        )
    execucao = catalogo.execucao() or {}
    pontos = [
        f'{linha["titulo"]}: {linha["algoritmo"]} · {linha["metrica"]} = {linha["valor"]} '
        f'({linha["particao"]})'
        + (f' · {linha["ganho"]:+.0f}% vs. baseline' if linha['ganho'] is not None else '')
        for linha in cartoes
    ]
    return Resposta(
        f'{len(cartoes)} modelos treinados sobre {execucao.get("incidentes", "?")} incidentes '
        f'entre {execucao.get("inicio_regime", "?")} e {execucao.get("fim_dados", "?")}. '
        f'Cada métrica abaixo foi medida na partição que não participou da escolha do modelo.',
        pontos, 'ml:modelos', 'Abrir Modelos de ML',
    )


def _explicacao_previsao():
    """Por que o modelo prevê o que prevê — contribuições do próprio modelo."""
    explicacao = inferencia.explicar()
    if not explicacao:
        return Resposta('Sem modelo treinado, não há previsão para explicar.')
    pontos = [
        f'{item["descricao"]}: {item["sentido"]} a previsão em '
        f'{abs(item["contribuicao"]):.1f} (valor observado: {item["valor"]})'
        for item in explicacao['contribuicoes']
    ]
    return Resposta(
        f'A previsão de amanhã parte de um dia médio de {explicacao["valor_base"]} incidentes e '
        f'é ajustada pelos fatores abaixo — são os que o próprio modelo usou para chegar ao '
        f'número, não uma justificativa escrita depois.',
        pontos, 'forecast:index', 'Abrir Forecast Engine',
    )


def _risco_ola_ml():
    risco = inferencia.risco_ola_global()
    equipes = inferencia.risco_ola(dimensao='equipe', limite=5)
    if not risco:
        return Resposta('Modelo de risco de OLA ainda não treinado.')
    pontos = [
        f'{item["chave"]}: risco {item["risco"]}% · {item["volume_previsto"]} incidentes '
        f'previstos · {item["violacoes_esperadas"]} violações esperadas'
        for item in (equipes or [])
    ]
    return Resposta(
        f'O risco de ao menos uma violação de OLA amanhã é {risco["faixa"]} '
        f'({risco["risco"]}%). Ele junta duas estimativas: a chance de um incidente estourar o '
        f'prazo ({risco["probabilidade_incidente"]}%) e o volume previsto para o dia '
        f'({risco["volume_previsto"]} incidentes).',
        pontos, 'alerts:index', 'Abrir Alert Center',
    )


INTENCOES = [
    (r'modelo|machine learning|algoritmo|acur|métrica|metrica|mae|auc|treinad', _modelos),
    (r'por que|porque|explica|fator|contribu|shap', _explicacao_previsao),
    (r'risco de ola|risco de sla|violar ola|perda de ola|probabilidade de viol', _risco_ola_ml),
    (r'health|saúde|saude|score|caiu|caindo', _health),
    (r'mudou|mudan|últimas|ultimas|24h|desde', _mudancas),
    (r'risco|falhar|violar|ola|sla', _riscos),
    (r'prioriz|priorid|fazer agora|o que devo|ação|acao', _prioridades),
    (r'anomal|desvio|estranho|fora do padrão|fora do padrao', _anomalias),
    (r'previs|forecast|amanhã|amanha|próximos|proximos', _previsao),
    (r'executiv|resumo|sumário|sumario', _resumo_executivo),
]


def responder(pergunta):
    texto = (pergunta or '').strip().lower()
    if not texto:
        return Resposta('Faça uma pergunta sobre a operação.', SUGESTOES)

    for padrao, handler in INTENCOES:
        if re.search(padrao, texto):
            return handler()

    return Resposta(
        'Ainda não sei responder isso. Consigo explicar o Operational Health, o que mudou no período, '
        'riscos de violação de OLA, prioridades de ação, anomalias detectadas, a previsão de volume, '
        'quais modelos de ML estão em uso e por que o modelo chegou à previsão que fez — sempre a '
        'partir dos dados carregados e dos modelos treinados.',
        SUGESTOES,
    )
