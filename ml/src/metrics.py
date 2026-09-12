"""Métricas de avaliação, com o significado operacional junto do número.

Regressão de contagem não se avalia com "acurácia": o que importa é de quantos incidentes o
modelo erra (MAE), o quanto ele erra feio (RMSE, que pune o erro grande), e se ele erra menos
que a alternativa boba (MASE, contra a baseline sazonal). MAPE entra com ressalva — explode em
dia de volume baixo, então só é calculado onde o real é diferente de zero.
"""

import numpy as np
from sklearn.metrics import (
    average_precision_score, brier_score_loss, confusion_matrix, f1_score, precision_score,
    r2_score, recall_score, roc_auc_score,
)

# Interpretação exibida na tela de transparência — a banca não deveria precisar procurar.
GLOSSARIO = {
    'mae': 'Erro absoluto médio: de quantos incidentes o modelo erra, em média, por dia.',
    'rmse': 'Raiz do erro quadrático médio: pune erro grande mais que erro pequeno.',
    'mape': 'Erro percentual absoluto médio, calculado só nos dias com volume maior que zero.',
    'r2': 'Fração da variação do volume explicada pelo modelo (1,0 = perfeito; 0 = média).',
    'mase': 'Erro do modelo dividido pelo da baseline sazonal. Abaixo de 1, o modelo ganha.',
    'vies': 'Erro médio com sinal: positivo = o modelo vem subestimando o volume.',
    'accuracy': 'Fração de acertos sobre o total.',
    'precision': 'Dos casos apontados como positivos, quantos eram de fato.',
    'recall': 'Dos casos positivos reais, quantos o modelo encontrou.',
    'f1': 'Média harmônica entre precisão e recall.',
    'roc_auc': 'Probabilidade de o modelo ordenar um positivo acima de um negativo (0,5 = acaso).',
    'pr_auc': 'Área sob a curva precisão-recall; é a métrica honesta quando a classe é rara.',
    'brier': 'Erro quadrático da probabilidade: mede calibração (quanto menor, melhor).',
}


def _limpar(y, p):
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    return y, p


def regressao(y_real, y_previsto, y_baseline=None):
    """MAE/RMSE/MAPE/R²/viés e, quando há baseline, o MASE e o ganho relativo."""
    y, p = _limpar(y_real, y_previsto)
    erro = y - p
    nao_zero = y > 0

    resultado = {
        'mae': float(np.mean(np.abs(erro))),
        'rmse': float(np.sqrt(np.mean(erro ** 2))),
        'mape': float(np.mean(np.abs(erro[nao_zero] / y[nao_zero])) * 100) if nao_zero.any() else None,
        'r2': float(r2_score(y, p)) if len(y) > 1 and np.std(y) else None,
        'vies': float(np.mean(erro)),
        'n': int(len(y)),
    }

    if y_baseline is not None:
        _, b = _limpar(y_real, y_baseline)
        mae_baseline = float(np.mean(np.abs(y - b)))
        resultado['mae_baseline'] = mae_baseline
        resultado['mase'] = resultado['mae'] / mae_baseline if mae_baseline else None
        resultado['ganho_vs_baseline'] = (
            (1 - resultado['mae'] / mae_baseline) * 100 if mae_baseline else None
        )
    return resultado


def classificacao(y_real, probabilidade, limiar=0.5):
    """Métricas de classificação sobre probabilidade — inclui PR-AUC e Brier.

    Com classe rara, ROC-AUC sozinha engana: um modelo que erra toda a classe positiva ainda
    pode exibir AUC alta. PR-AUC e a taxa-base ao lado deixam isso visível.
    """
    y = np.asarray(y_real).astype(int)
    prob = np.asarray(probabilidade, dtype=float)
    previsto = (prob >= limiar).astype(int)

    resultado = {
        'accuracy': float((previsto == y).mean()),
        'precision': float(precision_score(y, previsto, zero_division=0)),
        'recall': float(recall_score(y, previsto, zero_division=0)),
        'f1': float(f1_score(y, previsto, zero_division=0)),
        'brier': float(brier_score_loss(y, prob)),
        'limiar': float(limiar),
        'taxa_base': float(y.mean()),
        'positivos': int(y.sum()),
        'n': int(len(y)),
    }
    # AUC não existe quando só há uma classe no recorte avaliado.
    if 0 < y.sum() < len(y):
        resultado['roc_auc'] = float(roc_auc_score(y, prob))
        resultado['pr_auc'] = float(average_precision_score(y, prob))
    else:
        resultado['roc_auc'] = None
        resultado['pr_auc'] = None

    matriz = confusion_matrix(y, previsto, labels=[0, 1])
    resultado['matriz_confusao'] = {
        'vn': int(matriz[0, 0]), 'fp': int(matriz[0, 1]),
        'fn': int(matriz[1, 0]), 'vp': int(matriz[1, 1]),
    }
    return resultado


def melhor_limiar(y_real, probabilidade, passos=200):
    """Limiar que maximiza o F1 na validação.

    Com 1% de positivos, o corte natural de 0,5 nunca dispara: o modelo acerta tudo prevendo
    "não viola" e vira um alarme que nunca toca. O corte é escolhido na validação e depois
    aplicado ao teste — escolher no teste seria ajustar ao gabarito.
    """
    y = np.asarray(y_real).astype(int)
    prob = np.asarray(probabilidade, dtype=float)
    if not y.sum():
        return 0.5
    candidatos = np.linspace(prob.min(), prob.max(), passos)[1:-1]
    if not len(candidatos):
        return 0.5
    pontuacoes = [f1_score(y, (prob >= c).astype(int), zero_division=0) for c in candidatos]
    return float(candidatos[int(np.argmax(pontuacoes))])


def arredondar(dicionario, casas=4):
    """Números curtos para JSON e tela, sem perder a informação."""
    saida = {}
    for chave, valor in dicionario.items():
        if isinstance(valor, float):
            saida[chave] = round(valor, casas)
        elif isinstance(valor, dict):
            saida[chave] = arredondar(valor, casas)
        else:
            saida[chave] = valor
    return saida
