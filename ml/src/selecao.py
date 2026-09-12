"""Competição entre algoritmos: treina no treino, escolhe pela validação, reporta no teste.

O protocolo é sempre o mesmo, para regressão e para classificação:

1. cada família de algoritmo treina cada configuração da sua grade **só com o treino**;
2. a escolha do vencedor sai da **validação** — o teste não participa da decisão;
3. o vencedor é reajustado em treino + validação (mais dado, mesma configuração);
4. só então o **teste** é lido, uma única vez, e vira o número publicado.

Trocar 2 por 3 é o erro mais comum em trabalho de ML aplicado: escolher o modelo pelo teste e
reportar esse mesmo teste infla o resultado e não sobra amostra nenhuma para desmentir.
"""

import time

import numpy as np
import pandas as pd

from . import metrics, models_zoo


def _instanciar(definicao, parametros):
    """Fábrica + `set_params`: é o que faz `modelo__alpha` chegar dentro de um Pipeline."""
    estimador = definicao['estimador']()
    return estimador.set_params(**parametros) if parametros else estimador


def competir(candidatos, X_treino, y_treino, X_validacao, y_validacao,
             avaliar, chave_metrica, maior_melhor=False, baseline_validacao=None):
    """Roda a grade de todos os candidatos e devolve (melhor, tabela de comparação).

    `avaliar(modelo, X, y)` devolve o dicionário de métricas daquele tipo de problema.
    """
    tabela, melhor = [], None

    for nome, definicao in candidatos.items():
        for parametros in definicao['grade']:
            modelo = _instanciar(definicao, parametros)
            inicio = time.perf_counter()
            modelo.fit(X_treino, y_treino)
            segundos = time.perf_counter() - inicio

            resultado = avaliar(modelo, X_validacao, y_validacao)
            linha = {
                'candidato': nome,
                'tipo': definicao['tipo'],
                'algoritmo': models_zoo.nome_algoritmo(modelo),
                'hiperparametros': parametros,
                'segundos_treino': round(segundos, 2),
                'validacao': metrics.arredondar(resultado),
            }
            tabela.append(linha)

            pontuacao = resultado.get(chave_metrica)
            if pontuacao is None:
                continue
            referencia = melhor['pontuacao'] if melhor else None
            venceu = (
                referencia is None
                or (pontuacao > referencia if maior_melhor else pontuacao < referencia)
            )
            if venceu:
                melhor = {
                    'nome': nome, 'definicao': definicao, 'parametros': parametros,
                    'pontuacao': pontuacao, 'modelo': modelo, 'linha': linha,
                }

    if melhor is None:
        raise RuntimeError('Nenhum candidato produziu métrica válida na validação.')
    melhor['linha']['vencedor'] = True
    return melhor, tabela


def avaliador_regressao(coluna_baseline='mesmo_dow_1'):
    """Avalia regressão já comparando com a baseline sazonal presente nas features."""
    def avaliar(modelo, X, y):
        previsto = modelo.predict(X)
        baseline = X[coluna_baseline].to_numpy() if coluna_baseline in X.columns else None
        return metrics.regressao(y, np.clip(previsto, 0, None), baseline)
    return avaliar


def avaliador_classificacao(limiar=0.5):
    def avaliar(modelo, X, y):
        prob = modelo.predict_proba(X)[:, 1]
        return metrics.classificacao(y, prob, limiar)
    return avaliar


def reajustar(melhor, X, y):
    """Reajusta a configuração vencedora sobre treino + validação."""
    modelo = _instanciar(melhor['definicao'], melhor['parametros'])
    modelo.fit(X, y)
    return modelo


def comparacao_para_tabela(tabela, metricas=('mae', 'rmse', 'mase')):
    """Achata a tabela de comparação para leitura no notebook."""
    linhas = []
    for item in tabela:
        linha = {
            'candidato': item['candidato'],
            'tipo': item['tipo'],
            'algoritmo': item['algoritmo'],
            'vencedor': item.get('vencedor', False),
        }
        linha.update({m: item['validacao'].get(m) for m in metricas})
        linhas.append(linha)
    return pd.DataFrame(linhas)


def resumo_comparacao(tabela, metricas, maior_melhor=False):
    """Uma linha por família (a melhor configuração dela) — é o que a tela mostra."""
    melhor_por_familia = {}
    chave = metricas[0]
    for item in tabela:
        atual = melhor_por_familia.get(item['candidato'])
        valor = item['validacao'].get(chave)
        if valor is None:
            continue
        if atual is None or (
            valor > atual['validacao'][chave] if maior_melhor else valor < atual['validacao'][chave]
        ):
            melhor_por_familia[item['candidato']] = item
    return [
        {
            'candidato': item['candidato'],
            'tipo': item['tipo'],
            'algoritmo': item['algoritmo'],
            'vencedor': item.get('vencedor', False),
            'metricas': {m: item['validacao'].get(m) for m in metricas},
        }
        for item in melhor_por_familia.values()
    ]
