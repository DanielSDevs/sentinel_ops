"""Alert Center e Decision Center.

Um alerta aqui nunca é só uma notificação: ele responde **o que aconteceu / por que importa /
qual o impacto esperado / o que fazer**. Alerta que não sustenta uma decisão não é gerado.

O Decision Center consolida tudo em uma fila única de ações, ordenada por prioridade calculada
— não por ordem de chegada.
"""

from dataclasses import dataclass, field
from datetime import datetime, time

from django.utils import timezone

from apps.core.models import Incidente
from apps.core.tempo import referencia_temporal
from apps.intelligence.services import anomaly, base, insights, risk
from apps.intelligence.services.base import DIM
from apps.ml.services import inferencia


@dataclass
class Alerta:
    slug: str
    titulo: str
    o_que: str
    por_que: str
    impacto: str
    acao: str
    faixa: str          # critical | warning | attention
    origem: str
    quando: object
    confianca: int | None = None
    evidencia: str = ''
    tags: list = field(default_factory=list)

    @property
    def rotulo_faixa(self):
        return {'critical': 'Risco alto', 'warning': 'Atenção', 'attention': 'Informativo'}[self.faixa]


def _alertas_de_risco():
    alertas = []
    for item in risk.calcular(DIM.PRODUTO, limite=4):
        if item.faixa not in ('critical', 'warning'):
            continue
        alertas.append(Alerta(
            slug=f'risco-{item.chave}',
            titulo=f'Produto {item.chave} com risco de violação de OLA',
            o_que=(
                f'{item.volume_kpi} incidentes elegíveis nos últimos 14 dias, com '
                f'{item.criticos} de prioridade crítica ou alta.'
            ),
            por_que=(
                f'Historicamente {item.probabilidade * 100:.1f}% dos incidentes elegíveis deste '
                f'produto violaram o OLA — bem acima da média da operação.'
            ),
            impacto=f'{item.violacoes} violações acumuladas nos últimos 90 dias.',
            acao=item.acao,
            faixa=item.faixa,
            origem='Risk Radar',
            quando=referencia_temporal(),
            evidencia='Campos `Entrou para KPI?` e `KPI Violado?` agregados por produto',
            tags=[item.chave],
        ))
    return alertas


def _como_datahora(valor):
    """Anomalias são datadas por dia; o resto carrega timestamp. Normalizar evita que a ordenação
    compare tipos diferentes e que o template quebre ao formatar hora sobre um `date`."""
    if isinstance(valor, datetime):
        return valor
    return timezone.make_aware(datetime.combine(valor, time.min))


def _alertas_de_anomalia():
    alertas = []
    for anom in anomaly.detectar_por_dimensao(DIM.FAMILIA, limite=3):
        alertas.append(Alerta(
            slug=f'anomalia-{anom.chave}-{anom.data}',
            titulo=f'Comportamento anômalo no sinal "{anom.chave}"',
            o_que=(
                f'{anom.observado} ocorrências em {anom.data:%d/%m}, contra faixa esperada de '
                f'{anom.esperado_min}–{anom.esperado_max}.'
            ),
            por_que=(
                f'Desvio de {anom.desvio_pct:+.0f}% em relação ao padrão dos 21 dias anteriores '
                f'desta mesma série.'
            ),
            impacto='Pode indicar degradação em curso ou mudança de configuração recente.',
            acao='Verificar se houve mudança/deploy na janela e se o desvio persiste.',
            faixa=anom.faixa,
            origem='Anomaly Detection',
            quando=_como_datahora(anom.data),
            confianca=anom.confianca,
            evidencia='desvio contra o padrão móvel de 21 dias da própria série',
        ))
    return alertas


def _alerta_ruido_vs_sinal():
    """Anti-alarme-falso: volume bruto subindo por ruído não é incidente de verdade.

    Foi o achado central da análise do dataset e é o tipo de distinção que evita mobilizar
    a operação por um problema que não existe.
    """
    atual, anterior = base.periodo_e_anterior(7)
    var_total = base.variacao_percentual(atual['total'], anterior['total'])
    var_kpi = base.variacao_percentual(atual['total_kpi'], anterior['total_kpi'])
    pct_ruido = base.taxa(atual['total'] - atual['total_kpi'], atual['total'])

    if var_total is None or var_total < 20 or pct_ruido < 0.5:
        return None
    if var_kpi is not None and var_kpi >= 10:
        return None  # o sinal real também subiu — aí é alerta de verdade, tratado em outro lugar

    return Alerta(
        slug='ruido-sem-risco',
        titulo=f'Volume total subiu {var_total:.0f}%, mas sem aumento de risco real',
        o_que=(
            f'O volume bruto passou de {anterior["total"]} para {atual["total"]} incidentes, '
            f'enquanto os elegíveis a KPI ficaram em {atual["total_kpi"]}.'
        ),
        por_que=(
            f'{pct_ruido * 100:.0f}% do volume é ruído de monitoramento, que não entra no cálculo '
            'de OLA. O aumento não representa pressão contratual.'
        ),
        impacto='Consome capacidade de triagem sem impacto em SLA.',
        acao='Não escalar como incidente. Revisar thresholds de alerta da origem automática.',
        faixa='attention',
        origem='Forecast Engine',
        quando=referencia_temporal(),
        evidencia='Comparação entre `total` e `total_kpi` na MetricaDiaria',
    )


def _alertas_criticos_ativos():
    ativos = (
        Incidente.objects
        .filter(prioridade__in=[1, 2], status__in=[Incidente.Status.ABERTO, Incidente.Status.AGUARDANDO])
        .select_related('produto', 'equipe', 'familia_sinal')
        .order_by('-aberto_em')[:5]
    )
    alertas = []
    for inc in ativos:
        produto = inc.produto.codigo if inc.produto else 'sem produto'
        alertas.append(Alerta(
            slug=f'critico-{inc.numero}',
            titulo=f'Incidente {inc.get_prioridade_display()} aberto em {produto}',
            o_que=inc.descricao[:160],
            por_que=f'Prioridade {inc.prioridade} tem meta de OLA de {inc.sla_minutos // 60}h.',
            impacto=f'Atribuído a {inc.equipe.nome}; conta para o KPI.' if inc.entrou_kpi else 'Não conta para o KPI.',
            acao='Acompanhar até resolução e verificar incidentes semelhantes no histórico.',
            faixa='critical' if inc.prioridade == 1 else 'warning',
            origem=f'Equipe {inc.equipe.nome}',
            quando=inc.aberto_em,
            tags=[inc.numero],
        ))
    return alertas


def _alertas_de_pico_previsto(limite=3):
    """Alertas preventivos vindos dos modelos de ML — o único bloco que fala do futuro.

    A diferença para os demais é o tempo verbal: os outros alertas reagem ao que já aconteceu,
    este antecipa. Por isso ele carrega sempre a probabilidade e o modelo que a produziu — um
    alerta sobre o futuro sem a incerteza junto vira promessa.
    """
    alertas = []
    for pico in inferencia.picos_previstos(limite=limite) or []:
        probabilidade = pico['probabilidade']
        alertas.append(Alerta(
            slug=f'pico-{pico["dimensao"]}-{pico["chave"]}-{pico["data"]}',
            titulo=f'Pico previsto em {pico["chave"]} para {pico["data"]:%d/%m}',
            o_que=(
                f'O modelo projeta {pico["valor"]} incidentes elegíveis em {pico["dia_semana"]} '
                f'({pico["data"]:%d/%m}, D+{pico["horizonte"]}), contra média recente de '
                f'{pico["media_recente"]}/dia.'
            ),
            por_que=(
                f'Há {probabilidade * 100:.0f}% de chance de esse dia ficar entre os mais '
                f'carregados já vistos nesta {pico["dimensao"]}'
                + (f' — {pico["variacao"]:+.0f}% sobre o normal.' if pico['variacao'] is not None else '.')
            ),
            impacto=(
                f'Volume acima do padrão pressiona a fila e aumenta a chance de estouro de OLA. '
                f'Pela composição recente, cerca de {pico["p2_esperados"]} dos incidentes do dia '
                f'devem ser P2 ({pico["share_p2"] * 100:.0f}% da carga desta '
                f'{pico["dimensao"]} na última semana).'
            ),
            acao=(
                f'Reforçar a escala responsável por {pico["chave"]} em {pico["data"]:%d/%m} e '
                f'zerar a fila pendente na véspera.'
            ),
            faixa='critical' if probabilidade >= 0.7 else 'warning',
            origem='Forecast Engine (ML)',
            quando=referencia_temporal(),
            confianca=round(probabilidade * 100),
            evidencia='Classificador de pico + modelo de volume, treinados em ml/models/',
            tags=[pico['chave'], f'D+{pico["horizonte"]}'],
        ))
    return alertas


def _alertas_de_ola_previsto(limite=2):
    """Risco de perda de OLA em D+1, composto pelo classificador de violação e pelo volume."""
    alertas = []
    for item in inferencia.risco_ola(dimensao='equipe', limite=limite) or []:
        if item['faixa'] == 'baixo':
            continue
        fatores = '; '.join(
            f'{fator["descricao"]} ({fator["sentido"]} o risco)' for fator in item['fatores'][:3]
        )
        alertas.append(Alerta(
            slug=f'ola-{item["chave"]}',
            titulo=f'Equipe {item["chave"]} com {item["risco"]}% de risco de violar OLA amanhã',
            o_que=(
                f'{item["volume_previsto"]} incidentes elegíveis previstos para amanhã, com '
                f'probabilidade individual de violação de {item["probabilidade_incidente"]}%.'
            ),
            por_que=(
                f'Fatores que o modelo aponta: {fatores}.' if fatores
                else 'Composição entre volume previsto e probabilidade histórica de violação.'
            ),
            impacto=f'{item["violacoes_esperadas"]} violações esperadas no dia.',
            acao=(
                f'Revisar capacidade da equipe {item["chave"]} para amanhã e priorizar os '
                f'chamados P2 na entrada da fila.'
            ),
            faixa='critical' if item['faixa'] == 'alto' else 'warning',
            origem='Modelo de risco de OLA (ML)',
            quando=referencia_temporal(),
            confianca=round(item['risco']),
            evidencia='previsão de risco de OLA × previsão de volume — modelos em ml/models/',
            tags=[item['chave']],
        ))
    return alertas


ORDEM_FAIXA = {'critical': 0, 'warning': 1, 'attention': 2}


def alertas_ativos():
    alertas = [
        *_alertas_criticos_ativos(),
        *_alertas_de_risco(),
        *_alertas_de_anomalia(),
        *_alertas_de_pico_previsto(),
        *_alertas_de_ola_previsto(),
    ]
    ruido = _alerta_ruido_vs_sinal()
    if ruido:
        alertas.append(ruido)

    alertas.sort(key=lambda a: (ORDEM_FAIXA[a.faixa], -_como_datahora(a.quando).timestamp()))
    return alertas


def fila_de_decisao(limite=8):
    """Decision Center — ações candidatas ordenadas por prioridade calculada."""
    acoes = []

    for insight in insights.gerar(limite=6):
        acoes.append({
            'titulo': insight.acao,
            'motivo': insight.titulo,
            'impacto': insight.impacto_texto,
            'evidencia': insight.evidencia,
            'prioridade': insight.prioridade,
            'faixa': insight.faixa,
            'confianca': insight.confianca_pct,
            'origem': ROTULO_ORIGEM.get(insight.tipo, 'Operational Insights'),
            'link': insight.link,
            'link_rotulo': insight.link_rotulo,
        })

    for item in risk.calcular(DIM.EQUIPE, limite=3):
        if item.faixa not in ('critical', 'warning'):
            continue
        acoes.append({
            'titulo': item.acao,
            'motivo': f'Equipe {item.chave} com risco {item.rotulo_faixa.lower()}',
            'impacto': f'{item.violacoes} violações de OLA em 90 dias · {item.volume_kpi} elegíveis em 14 dias',
            'evidencia': 'Taxa histórica de violação por equipe',
            'prioridade': item.score,
            'faixa': item.faixa,
            'confianca': None,
            'origem': 'Risk Radar',
            'link': 'intelligence:risk',
            'link_rotulo': 'Abrir Risk Radar',
        })

    vistos, unicas = set(), []
    for acao in sorted(acoes, key=lambda a: a['prioridade'], reverse=True):
        if acao['titulo'] in vistos:
            continue
        vistos.add(acao['titulo'])
        unicas.append(acao)
    return unicas[:limite]


ROTULO_ORIGEM = {
    'critico': 'Operational Insights',
    'padrao': 'Anomaly Detection',
    'eficiencia': 'Operational Insights',
    'ola': 'Risk Radar',
    'capacidade': 'Forecast Engine',
}
