"""Forecast Engine.

Modelo: **nível recente × perfil do dia da semana**, ancorado no mesmo dia da semana anterior.
Simples e interpretável de propósito — o operador precisa entender de onde veio o número.

Separar as duas partes é o que faz o modelo reagir a mudança de patamar sem perder o formato da
semana: o nível sai dos últimos `DIAS_NIVEL` dias (adapta rápido), enquanto o perfil de semana
continua estimado no histórico longo, onde há amostra suficiente para separar segunda de domingo.
A versão anterior (média de 90 dias por dia da semana × fator de tendência) misturava as duas
coisas na mesma média e demorava semanas para acompanhar uma queda de volume.

O intervalo de previsão **não é arbitrado**: é o erro que o modelo de fato comete naquele dia da
semana em backtest walk-forward. Sábado erra menos que terça em valor absoluto, e a faixa mostra
isso em vez de aplicar a mesma margem a todos os dias.
"""

from collections import defaultdict
from datetime import timedelta

from django.core.cache import cache

from apps.core.models import Incidente
from apps.core.tempo import data_referencia
from apps.intelligence.services import base
from apps.intelligence.services.base import DIM

HISTORICO_PADRAO = 90
HORIZONTE_PADRAO = 7
DIAS_SEMANA = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']

# Dias que definem o nível atual da operação. 14 foi o que minimizou o MAE em walk-forward
# sobre 24 janelas do dataset real — janelas mais curtas oscilam com ruído, mais longas
# demoram a acompanhar mudança de patamar.
DIAS_NIVEL = 14

# Peso do termo de repetição (mesmo dia da semana anterior) na mistura. Ancora a previsão no
# último dado real observado; 0,35 venceu a baseline sazonal em 18 das 24 janelas testadas.
PESO_SEMANA_PASSADA = 0.35

JANELAS_BACKTEST = 12
JANELAS_DETALHE = 3
CACHE_TTL = 300

# Cobertura que a faixa da previsão se propõe a ter. A margem é o percentil empírico do erro
# absoluto no backtest, não ±1σ: os resíduos são assimétricos, e a faixa de um desvio padrão
# cobria só 55% dos dias reais — prometia mais do que entregava.
#
# A cobertura medida fora da amostra fica abaixo deste alvo (o backtest reporta as duas em
# `cobertura_alvo` e `cobertura`), porque o erro do período recente é maior que o do período em
# que a margem foi calibrada. Subir o alvo não resolve: a 95% a faixa vai a ±31 sobre uma
# previsão de ~34 e ainda assim não chega aos 80%. Faixa larga demais não informa nada — o
# caminho honesto é mostrar a cobertura que a faixa de fato entrega.
COBERTURA_ALVO = 0.80


def _perfil_semana(serie):
    """Índice multiplicativo por dia da semana: quanto o dia rende em relação à média da série.

    Domingo em torno de 0,4 e terça em torno de 1,3 dizem a mesma coisa que duas médias
    absolutas, mas continuam válidos quando o patamar da operação muda.
    """
    por_dia = defaultdict(list)
    for data, valor in serie:
        por_dia[data.weekday()].append(valor)
    media_geral = base.media(v for _, v in serie)
    if not media_geral:
        return {}
    return {dia: base.media(valores) / media_geral for dia, valores in por_dia.items() if valores}


def _modelo(serie):
    """Devolve a função que estima o volume de uma data.

    A previsão mistura duas leituras independentes do mesmo dia: a estrutural (nível × perfil) e
    a repetição do valor observado 7 dias antes. Nenhuma das duas olha para o futuro: o termo de
    repetição só existe enquanto `data - 7 dias` estiver dentro da série de ajuste, o que cobre
    exatamente o horizonte de D+1 a D+7. Além disso ele sai da conta e sobra a parte estrutural.
    """
    perfil = _perfil_semana(serie)
    nivel = base.media(v for _, v in serie[-DIAS_NIVEL:])
    historico = dict(serie)

    def prever(data):
        estrutural = nivel * perfil.get(data.weekday(), 1.0)
        semana_passada = historico.get(data - timedelta(days=7))
        if semana_passada is None:
            return max(0.0, estrutural)
        return max(
            0.0,
            estrutural * (1 - PESO_SEMANA_PASSADA) + semana_passada * PESO_SEMANA_PASSADA,
        )

    return prever


def _margens(absolutos, absolutos_por_dia_semana, minimo_amostras=3):
    """Margem da faixa: percentil `COBERTURA_ALVO` do erro absoluto, geral e por dia da semana.

    Dias da semana com poucas observações caem na margem geral — estimar um percentil com duas
    amostras produziria uma faixa que só parece precisa.
    """
    margens = {'geral': round(base.percentil(absolutos, COBERTURA_ALVO), 1)}
    for dia, erros in absolutos_por_dia_semana.items():
        if len(erros) >= minimo_amostras:
            margens[dia] = round(base.percentil(erros, COBERTURA_ALVO), 1)
    return margens


def backtest(historico_dias=HISTORICO_PADRAO, horizonte=HORIZONTE_PADRAO,
             janelas=JANELAS_BACKTEST, apenas_kpi=True):
    """Walk-forward: `janelas` origens consecutivas, cada uma prevendo `horizonte` dias à frente.

    Cada origem treina apenas com os dias anteriores a ela e é avaliada em dias que nunca viu —
    a origem seguinte só então incorpora o que aconteceu. É o mesmo ciclo que a plataforma vive
    em produção, onde a previsão é refeita a cada dia com o dado que acabou de chegar.

    A versão anterior media uma única janela de 21 dias, e a última janela do dataset cai sobre o
    Natal: bastava esse recorte para o MAE reportado dobrar. Agregar várias origens mede o erro
    típico, e `dispersao_mae` mostra o quanto ele varia de período para período.

    Alimenta a tela Model Performance e a faixa da previsão.
    """
    campo = 'total_kpi' if apenas_kpi else 'total'
    chave_cache = (
        f'sentinelops:forecast:backtest:{historico_dias}:{horizonte}:{janelas}:{campo}:'
        f'{data_referencia():%Y%m%d}'
    )
    em_cache = cache.get(chave_cache)
    if em_cache is not None:
        return em_cache

    serie = base.serie_diaria(historico_dias + horizonte * janelas, campo=campo)

    residuos, absolutos, percentuais, naive_abs, pontos, resumo_janelas = [], [], [], [], [], []
    # Por dia da semana, não por passo do horizonte: como as origens andam de 7 em 7 dias, o
    # passo N cai sempre no mesmo dia da semana, e o que a série realmente separa é sábado de
    # terça — não D+1 de D+7. Um sábado de 20 incidentes não merece a mesma faixa que uma
    # terça de 90.
    absolutos_por_dia_semana = defaultdict(list)
    cobertos = avaliados_cobertura = 0

    for indice in range(janelas):
        fim_teste = len(serie) - horizonte * (janelas - 1 - indice)
        inicio_teste = fim_teste - horizonte
        treino = serie[max(0, inicio_teste - historico_dias):inicio_teste]
        teste = serie[inicio_teste:fim_teste]
        # Janela sem histórico com que aprender (base recém-importada, série ainda curta).
        if len(teste) < horizonte or len(treino) < DIAS_NIVEL * 2 or not sum(v for _, v in treino):
            continue

        prever_data = _modelo(treino)
        conhecido = dict(treino)
        # A faixa desta janela vem só do erro das janelas anteriores — medir a cobertura com a
        # margem calculada sobre os próprios pontos avaliados seria autoindulgente.
        margens = _margens(absolutos, absolutos_por_dia_semana) if len(absolutos) >= horizonte else None
        abs_janela, abs_naive_janela = [], []

        for data, real in teste:
            estimado = prever_data(data)
            erro = real - estimado
            residuos.append(erro)
            abs_janela.append(abs(erro))
            if real:
                percentuais.append(abs(erro) / real)
            if margens is not None:
                avaliados_cobertura += 1
                cobertos += 1 if abs(erro) <= margens.get(data.weekday(), margens['geral']) else 0
            absolutos_por_dia_semana[data.weekday()].append(abs(erro))

            anterior = conhecido.get(data - timedelta(days=7))
            if anterior is not None:
                abs_naive_janela.append(abs(real - anterior))

            pontos.append({
                'data': data,
                'real': real,
                'previsto': round(estimado, 1),
                'erro': round(erro, 1),
                'janela': indice + 1,
            })

        absolutos += abs_janela
        naive_abs += abs_naive_janela
        resumo_janelas.append({
            'inicio': teste[0][0],
            'fim': teste[-1][0],
            'mae': round(base.media(abs_janela), 2),
            'mae_naive': round(base.media(abs_naive_janela), 2) if abs_naive_janela else None,
        })

    if not resumo_janelas:
        return None

    mae = base.media(absolutos)
    mae_naive = base.media(naive_abs) if naive_abs else None
    resultado = {
        'mae': round(mae, 2),
        'rmse': round((base.media([e ** 2 for e in residuos])) ** 0.5, 2),
        'mape': round(base.media(percentuais) * 100, 1) if percentuais else None,
        'mae_naive': round(mae_naive, 2) if mae_naive is not None else None,
        # MASE < 1 = erra menos que a baseline sazonal. Escala-livre, ao contrário do MAPE, que
        # explode quando o volume real do dia é pequeno.
        'mase': round(mae / mae_naive, 2) if mae_naive else None,
        # Erro médio com sinal: negativo = o modelo vem superestimando o volume.
        'vies': round(base.media(residuos), 2),
        'desvio_residuos': round(base.desvio_padrao(residuos), 2),
        'margens': _margens(absolutos, absolutos_por_dia_semana),
        'cobertura_alvo': round(COBERTURA_ALVO * 100),
        'cobertura': (
            round(cobertos / avaliados_cobertura * 100, 1) if avaliados_cobertura else None
        ),
        'dispersao_mae': round(base.desvio_padrao([j['mae'] for j in resumo_janelas]), 2),
        'vitorias_naive': sum(
            1 for j in resumo_janelas if j['mae_naive'] is not None and j['mae'] < j['mae_naive']
        ),
        'janelas': len(resumo_janelas),
        'resumo_janelas': resumo_janelas,
        'horizonte': horizonte,
        'dias_teste': len(residuos),
        'dias_treino': historico_dias,
        'pontos': pontos[-JANELAS_DETALHE * horizonte:],
        'campo': campo,
    }
    cache.set(chave_cache, resultado, CACHE_TTL)
    return resultado


def prever(dias_futuros=HORIZONTE_PADRAO, historico_dias=HISTORICO_PADRAO, apenas_kpi=True):
    campo = 'total_kpi' if apenas_kpi else 'total'
    serie = base.serie_diaria(historico_dias, campo=campo)
    # A série vem densa (dias sem registro viram 0), então "sem histórico" não é lista vazia e
    # sim série toda zerada. Prever 0 com faixa 0–0 pareceria uma previsão confiante de silêncio;
    # devolver nada faz a tela cair no estado vazio, que é o honesto.
    if not sum(v for _, v in serie):
        return []

    prever_data = _modelo(serie)
    avaliacao = backtest(historico_dias, apenas_kpi=apenas_kpi)
    margens = avaliacao['margens'] if avaliacao else {'geral': 0}

    hoje = data_referencia()
    previsoes = []
    for i in range(1, dias_futuros + 1):
        data = hoje + timedelta(days=i)
        valor = prever_data(data)
        # Faixa do próprio dia da semana quando o backtest tem amostra para ela.
        margem = margens.get(data.weekday(), margens['geral'])
        previsoes.append({
            'data': data,
            'dia_semana': DIAS_SEMANA[data.weekday()],
            'valor': round(valor),
            'minimo': max(0, round(valor - margem)),
            'maximo': round(valor + margem),
        })
    return previsoes


def resumo(apenas_kpi=True):
    previsoes = prever(7, apenas_kpi=apenas_kpi)
    if not previsoes:
        return None

    avaliacao = backtest(apenas_kpi=apenas_kpi)
    serie = base.serie_diaria(28, campo='total_kpi' if apenas_kpi else 'total')
    recente = base.media(v for _, v in serie[-7:])
    anterior = base.media(v for _, v in serie[-14:-7])
    tendencia = base.variacao_percentual(recente, anterior)

    d1 = previsoes[0]
    d7_total = sum(p['valor'] for p in previsoes)
    pico = max(previsoes, key=lambda p: p['valor'])

    return {
        'd1': d1,
        'd7_total': d7_total,
        'd7_min': sum(p['minimo'] for p in previsoes),
        'd7_max': sum(p['maximo'] for p in previsoes),
        'previsoes': previsoes,
        'tendencia': tendencia,
        'pico': pico,
        'avaliacao': avaliacao,
        'media_recente': round(recente, 1),
    }


def interpretar(dados):
    """Transforma a previsão em leitura acionável — o gráfico sozinho não decide nada."""
    if not dados:
        return ''

    partes = []
    d1 = dados['d1']
    partes.append(
        f'Para amanhã ({d1["dia_semana"]}, {d1["data"]:%d/%m}) a estimativa é de {d1["valor"]} '
        f'incidentes elegíveis, com faixa provável entre {d1["minimo"]} e {d1["maximo"]}.'
    )

    tendencia = dados['tendencia']
    if tendencia is not None and abs(tendencia) >= 5:
        direcao = 'alta' if tendencia > 0 else 'queda'
        partes.append(f'A média diária vem em {direcao} de {abs(tendencia):.0f}% frente à semana anterior.')
    else:
        partes.append('A média diária está estável frente à semana anterior.')

    pico = dados['pico']
    if pico['valor'] > dados['media_recente'] * 1.15:
        partes.append(
            f'O maior volume previsto cai em {pico["dia_semana"]} ({pico["data"]:%d/%m}), '
            f'com {pico["valor"]} incidentes — é o dia que merece reforço de escala.'
        )

    avaliacao = dados['avaliacao']
    if avaliacao and avaliacao['mae_naive']:
        contexto = (
            f'{avaliacao["janelas"]} janelas de {avaliacao["horizonte"]} dias em walk-forward'
        )
        if avaliacao['mae'] < avaliacao['mae_naive']:
            ganho = round((1 - avaliacao['mae'] / avaliacao['mae_naive']) * 100)
            partes.append(
                f'Em backtest ({contexto}), o modelo erra {ganho}% menos que simplesmente repetir '
                f'o mesmo dia da semana anterior (MAE {avaliacao["mae"]} vs. '
                f'{avaliacao["mae_naive"]}), vencendo essa baseline em '
                f'{avaliacao["vitorias_naive"]} das {avaliacao["janelas"]} janelas.'
            )
        else:
            partes.append(
                f'Em backtest ({contexto}) o modelo não supera a baseline sazonal '
                f'(MAE {avaliacao["mae"]} vs. {avaliacao["mae_naive"]}) — trate a previsão como '
                f'referência, não como garantia.'
            )
        if avaliacao['vies'] and abs(avaliacao['vies']) >= avaliacao['mae'] * 0.3:
            direcao = 'subestimou' if avaliacao['vies'] > 0 else 'superestimou'
            partes.append(
                f'Atenção ao viés: no período avaliado o modelo {direcao} o volume em '
                f'{abs(avaliacao["vies"]):.1f} incidentes/dia em média.'
            )
    return ' '.join(partes)


def calendario_risco(dias=28):
    """Risk Calendar — dias futuros ordenados por volume previsto e risco relativo."""
    previsoes = prever(dias)
    if not previsoes:
        return []
    valores = [p['valor'] for p in previsoes]
    maximo = max(valores) or 1
    limiar_alto = base.media(valores) * 1.15

    for p in previsoes:
        p['intensidade'] = round((p['valor'] / maximo) * 100)
        p['faixa'] = 'critical' if p['valor'] >= limiar_alto * 1.15 else (
            'warning' if p['valor'] >= limiar_alto else 'healthy'
        )
    return previsoes


def tendencia_por_dimensao(dimensao=DIM.FAMILIA, dias=28, limite=6):
    """Crescimento/redução por família de sinal, produto ou equipe."""
    from apps.core.models import FamiliaSinal

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
        variacao = base.variacao_percentual(recente['total_kpi'], anterior.get('total_kpi', 0))
        linhas.append({
            'chave': chave,
            'nome': nomes.get(chave, chave),
            'recentes': recente['total_kpi'],
            'variacao': variacao,
        })

    linhas.sort(key=lambda x: x['recentes'], reverse=True)
    return linhas[:limite]
