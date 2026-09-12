"""Separação treino / validação / teste e avaliação walk-forward.

**O corte é pela data do alvo, não pela data de origem.** Uma linha de origem 20/11 com
horizonte 7 enxerga 27/11; cortar pela origem deixaria essa linha no treino enquanto o dia 27
já pertence à validação — o modelo teria visto o gabarito. Cortar pelo alvo elimina a
sobreposição por construção, em qualquer horizonte.

`validacao` existe para escolher o modelo e os hiperparâmetros; `teste` é tocado uma única vez,
no fim, para reportar. Nenhuma decisão do pipeline olha o teste.
"""

import numpy as np
import pandas as pd

from . import config


def limites(datas, dias_teste=config.DIAS_TESTE, dias_validacao=config.DIAS_VALIDACAO):
    fim = pd.Timestamp(datas.max())
    inicio_teste = fim - pd.Timedelta(days=dias_teste - 1)
    inicio_validacao = inicio_teste - pd.Timedelta(days=dias_validacao)
    return inicio_validacao, inicio_teste


def dividir(matriz, dias_teste=config.DIAS_TESTE, dias_validacao=config.DIAS_VALIDACAO,
            coluna='data_alvo'):
    """Devolve três máscaras booleanas alinhadas com `matriz`."""
    inicio_validacao, inicio_teste = limites(matriz[coluna], dias_teste, dias_validacao)
    alvo = matriz[coluna]
    treino = alvo < inicio_validacao
    validacao = (alvo >= inicio_validacao) & (alvo < inicio_teste)
    teste = alvo >= inicio_teste
    return treino, validacao, teste


def descricao(matriz, coluna='data_alvo'):
    """Resumo das partições para o notebook e para a tela de transparência."""
    treino, validacao, teste = dividir(matriz, coluna=coluna)
    partes = {}
    for nome, mascara in (('treino', treino), ('validacao', validacao), ('teste', teste)):
        recorte = matriz.loc[mascara, coluna]
        partes[nome] = {
            'linhas': int(mascara.sum()),
            'inicio': recorte.min().date().isoformat() if len(recorte) else None,
            'fim': recorte.max().date().isoformat() if len(recorte) else None,
        }
    return partes


def origens_walk_forward(matriz, janelas=12, passo=7, coluna='data_alvo'):
    """Datas de corte de um backtest walk-forward: cada origem treina só com o próprio passado.

    É a avaliação que imita o ciclo real da plataforma — todo dia chega dado novo e a previsão é
    refeita. Uma janela única mediria um recorte: a última semana do dataset cai no Natal, e só
    isso já dobraria o erro reportado.
    """
    fim = pd.Timestamp(matriz[coluna].max())
    return [fim - pd.Timedelta(days=passo * i) for i in range(janelas, 0, -1)]


def particoes_walk_forward(matriz, janelas=12, passo=7, coluna='data_alvo'):
    """Gera (máscara de treino, máscara de teste) para cada origem do walk-forward."""
    for corte in origens_walk_forward(matriz, janelas, passo, coluna):
        treino = matriz[coluna] < corte
        teste = (matriz[coluna] >= corte) & (matriz[coluna] < corte + pd.Timedelta(days=passo))
        if treino.sum() and teste.sum():
            yield corte, treino, teste


def detectar_inicio_regime(serie_diaria, janela=28, fracao=0.25):
    """Primeiro dia a partir do qual a operação está em regime, para não treinar sobre backfill.

    O extrato da Locaweb começa em 2023, mas até o fim de 2024 registra menos de um incidente
    elegível por dia — é carga histórica parcial, não operação. Treinar com esse trecho ensinaria
    ao modelo um patamar que não existe mais e contaminaria toda média de longo prazo.

    A regra: o primeiro dia a partir do qual a mediana móvel de `janela` dias nunca mais cai
    abaixo de `fracao` da mediana do período final. Sai uma data derivada dos dados, não
    escolhida a dedo.
    """
    serie = pd.Series(serie_diaria).sort_index()
    referencia = serie.tail(janela * 3).median()
    if not referencia:
        return serie.index.min()
    limiar = referencia * fracao
    movel = serie.rolling(janela, min_periods=janela).median()
    abaixo = movel[movel < limiar]
    if abaixo.empty:
        return serie.index.min()
    posicao = serie.index.get_loc(abaixo.index.max())
    return serie.index[min(posicao + 1, len(serie) - 1)]


def indice_temporal_ordenado(matriz, coluna='data_alvo'):
    """Ordem cronológica estável — usada por `TimeSeriesSplit` e pelos gráficos do notebook."""
    return np.argsort(matriz[coluna].to_numpy(), kind='stable')
