"""Forecast Engine — camada de leitura dos modelos de ML de volume.

O motor de previsão **é** o modelo treinado em `ml/` (XGBoost / LightGBM / Random Forest, o que
tiver vencido a comparação); este módulo não calcula previsão nenhuma, só organiza o que os
modelos devolvem para as telas e escreve a leitura em português.

A heurística que ocupava este arquivo — nível recente × perfil de dia da semana — não sumiu:
virou **baseline** dentro do pipeline de ML (`Baseline média 7d` e `Baseline sazonal`), medida na
mesma partição e na mesma métrica que os modelos. É assim que dá para afirmar que o ML acrescenta
alguma coisa em vez de apenas supor.

Quando não há modelo treinado, tudo aqui devolve `None` e as telas caem no estado vazio. Não
existe fallback para média: previsão sem modelo não é previsão.
"""

from datetime import timedelta

from apps.core.models import FamiliaSinal
from apps.core.tempo import data_referencia
from apps.intelligence.services import base
from apps.intelligence.services.base import DIM
from apps.ml.services import catalogo, inferencia

DIAS_HISTORICO_GRAFICO = 28


def disponivel():
    return inferencia.disponivel('model_d1') or inferencia.disponivel('model_d7')


def resumo(dias=7):
    """Bloco de previsão usado pelo Command Center, Daily Report e Copilot."""
    dados = inferencia.resumo_global(dias=dias)
    if not dados:
        return None

    cartao = catalogo.modelo('model_d1') or catalogo.modelo('model_d7')
    dados['modelo'] = cartao
    dados['avaliacao'] = (cartao or {}).get('metricas', {})
    return dados


def historico(dias=DIAS_HISTORICO_GRAFICO):
    """Série real recente, para o gráfico começar antes da previsão."""
    return base.serie_diaria(dias, campo='total_kpi')


def interpretar(dados):
    """A leitura em texto do que o modelo previu — o gráfico sozinho não decide nada."""
    if not dados:
        return ''

    partes = []
    d1 = dados['d1']
    partes.append(
        f'Para amanhã ({d1["dia_semana"]}, {d1["data"]:%d/%m}) são esperados cerca de '
        f'{d1["valor"]} incidentes elegíveis a KPI, com faixa provável entre '
        f'{d1["minimo"]} e {d1["maximo"]}.'
    )

    variacao = dados.get('variacao_d1')
    if variacao is not None and abs(variacao) >= 5:
        direcao = 'acima' if variacao > 0 else 'abaixo'
        partes.append(
            f'Isso é {abs(variacao):.0f}% {direcao} da média dos últimos 7 dias '
            f'({dados["media_recente"]}/dia).'
        )
    else:
        partes.append(f'Fica em linha com a média recente ({dados["media_recente"]}/dia).')

    pico = dados['pico']
    if pico.get('probabilidade_pico') and pico['probabilidade_pico'] >= 0.5:
        partes.append(
            f'O dia de maior pressão deve ser {pico["dia_semana"]} ({pico["data"]:%d/%m}), com '
            f'chance de {pico["probabilidade_pico"] * 100:.0f}% de ficar entre os dias mais '
            f'carregados do histórico — é onde reforçar escala.'
        )
    elif pico['valor'] > dados['media_recente'] * 1.15:
        partes.append(
            f'O maior volume previsto cai em {pico["dia_semana"]} ({pico["data"]:%d/%m}), com '
            f'{pico["valor"]} incidentes.'
        )

    explicacao = dados.get('explicacao')
    if explicacao and explicacao['contribuicoes']:
        principal = explicacao['contribuicoes'][0]
        partes.append(
            f'O fator que mais pesou nessa previsão foi {principal["descricao"]} '
            f'({principal["sentido"]} a estimativa em {abs(principal["contribuicao"]):.1f} '
            f'incidentes).'
        )

    cartao = dados.get('modelo')
    if cartao:
        teste = cartao['metricas'].get('teste', {})
        caminho = cartao['metricas'].get('walk_forward')
        if caminho:
            partes.append(
                f'Testada em {caminho["janelas"]} semanas seguidas, a previsão errou em média '
                f'{caminho["mae"]} incidentes por dia, contra {caminho["mae_baseline"]} de uma '
                f'estimativa simples pelo mesmo dia da semana anterior — ficou melhor em '
                f'{caminho["vitorias_sobre_baseline"]} das {caminho["janelas"]} semanas.'
            )
        elif teste.get('mae') is not None:
            partes.append(
                f'Em dias que o modelo não tinha visto, o erro médio foi de {teste["mae"]} '
                f'incidentes por dia.'
            )
    return ' '.join(partes)


def calendario_risco(dias=21):
    """Dias futuros ordenados por volume previsto, com a probabilidade de pico do classificador."""
    previsoes = inferencia.prever(dias=dias)
    if not previsoes:
        return []

    valores = [p['valor'] for p in previsoes]
    maximo = max(valores) or 1
    for previsao in previsoes:
        previsao['intensidade'] = round((previsao['valor'] / maximo) * 100)
        probabilidade = previsao.get('probabilidade_pico')
        if probabilidade is None:
            previsao['faixa'] = 'healthy'
        elif probabilidade >= 0.66:
            previsao['faixa'] = 'critical'
        elif probabilidade >= 0.4:
            previsao['faixa'] = 'warning'
        else:
            previsao['faixa'] = 'healthy'
    return previsoes


def picos(limite=6):
    """Onde e quando o pico deve aparecer, segundo o classificador."""
    return inferencia.picos_previstos(limite=limite) or []


def por_dimensao(dimensao='produto', limite=6):
    """Previsão por produto, equipe ou categoria — o "onde" da previsão."""
    return inferencia.previsao_por_dimensao(dimensao, limite=limite)


def tendencia_por_dimensao(dimensao=DIM.FAMILIA, dias=28, limite=6):
    """Crescimento/redução observado por família de sinal, produto ou equipe.

    Descritivo, não preditivo: compara duas metades do período. Fica aqui porque a tela mostra
    as duas coisas lado a lado — o que já mudou e o que o modelo espera.
    """
    nomes = dict(FamiliaSinal.objects.values_list('slug', 'nome')) if dimensao == DIM.FAMILIA else {}
    metade = dias // 2
    recentes = {l['chave']: l for l in base.agregado_por_chave(metade, dimensao, minimo_total=1)}
    anteriores = {
        l['chave']: l
        for l in base.agregado_por_chave(metade, dimensao, ate=data_referencia() - timedelta(days=metade))
    }

    linhas = []
    for chave, recente in recentes.items():
        anterior = anteriores.get(chave, {'total_kpi': 0})
        linhas.append({
            'chave': chave,
            'nome': nomes.get(chave, chave),
            'recentes': recente['total_kpi'],
            'variacao': base.variacao_percentual(recente['total_kpi'], anterior.get('total_kpi', 0)),
        })

    linhas.sort(key=lambda x: x['recentes'], reverse=True)
    return linhas[:limite]
