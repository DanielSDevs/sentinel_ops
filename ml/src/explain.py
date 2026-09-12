"""Explicabilidade: por que o modelo previu o que previu.

Três camadas, com propósitos diferentes:

- **Importância do modelo** (ganho nas árvores / coeficiente no linear): barata, global, mas
  enviesada a favor de variáveis de alta cardinalidade.
- **Importância por permutação**: mede a piora real da métrica ao embaralhar a coluna, medida
  *na validação*. É a que entra no cartão do modelo, porque responde à pergunta certa — "sem
  essa feature, quanto o modelo piora?".
- **SHAP local**: a contribuição assinada de cada feature *naquela previsão*. É o que a tela
  mostra quando o operador pergunta por que amanhã deveria ser pior que hoje.

Nada aqui inventa rótulo: `DESCRICOES` só traduz o nome técnico da feature para português;
o valor e o sinal vêm sempre do modelo.
"""

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.pipeline import Pipeline

DESCRICOES = {
    'lag_0': 'volume de ontem (último dia observado)',
    'media_7': 'média dos últimos 7 dias',
    'media_14': 'média dos últimos 14 dias',
    'media_28': 'média dos últimos 28 dias',
    'media_3': 'média dos últimos 3 dias',
    'soma_7': 'total dos últimos 7 dias',
    'soma_14': 'total dos últimos 14 dias',
    'soma_30': 'total dos últimos 30 dias',
    'mesmo_dow_1': 'mesmo dia da semana, 1 semana atrás',
    'mesmo_dow_2': 'mesmo dia da semana, 2 semanas atrás',
    'mesmo_dow_3': 'mesmo dia da semana, 3 semanas atrás',
    'mesmo_dow_4': 'mesmo dia da semana, 4 semanas atrás',
    'media_mesmo_dow_4': 'média do mesmo dia da semana nas últimas 4 semanas',
    'razao_7_28': 'nível recente contra o mês (7d ÷ 28d)',
    'var_7_vs_7': 'variação da última semana contra a anterior',
    'delta_media_7_14': 'aceleração do volume (média 7d − média 14d)',
    'desvio_7': 'oscilação da última semana',
    'desvio_28': 'oscilação do último mês',
    'cv_28': 'instabilidade relativa do último mês',
    'dias_ativos_28': 'dias com incidente no último mês',
    'media_expandida': 'patamar histórico da entidade',
    'share_global_28': 'peso da entidade no volume total',
    'criticos_media_7': 'média de críticos (P1/P2) na semana',
    'p2_media_7': 'média de incidentes P2 na semana',
    'p3_media_7': 'média de incidentes P3 na semana',
    'p2_media_28': 'média de incidentes P2 no mês',
    'p3_media_28': 'média de incidentes P3 no mês',
    'share_criticos_7': 'participação de críticos na semana',
    'share_p2_7': 'participação de P2 na semana',
    'share_p3_7': 'participação de P3 na semana',
    'share_ruido_7': 'participação de chamados de monitoramento',
    'violacoes_media_7': 'violações de OLA por dia na semana',
    'violacoes_media_28': 'violações de OLA por dia no mês',
    'taxa_violacao_media_7': 'taxa de violação de OLA na semana',
    'taxa_violacao_media_28': 'taxa de violação de OLA no mês',
    'delta_taxa_violacao': 'piora da taxa de violação (7d vs 28d)',
    'mttr_medio_media_7': 'tempo médio de resolução na semana',
    'mttr_medio_media_28': 'tempo médio de resolução no mês',
    'delta_mttr': 'aumento do tempo de resolução (7d vs 28d)',
    'itens_distintos_media_7': 'ativos distintos afetados por dia',
    'familias_distintas_media_7': 'famílias de sintoma distintas por dia',
    'resolvidos_media_7': 'incidentes resolvidos por dia na semana',
    'monitoramento_media_7': 'chamados automáticos por dia na semana',
    'total_media_7': 'volume bruto médio na semana',
    'dow': 'dia da semana previsto',
    'dow_origem': 'dia da semana da origem da previsão',
    'dia_mes': 'dia do mês',
    'mes': 'mês',
    'semana_ano': 'semana do ano',
    'fim_de_semana': 'é fim de semana',
    'feriado': 'é feriado',
    'vespera_feriado': 'véspera de feriado',
    'pos_feriado': 'dia seguinte a feriado',
    'horizonte': 'distância da previsão (dias à frente)',
    'min_7': 'mínimo da última semana',
    'max_7': 'máximo da última semana',
    'max_28': 'máximo do último mês',
    'prioridade': 'prioridade do incidente',
    'hora': 'hora de abertura',
    'aberto_manual': 'aberto manualmente (não por monitoramento)',
    'sla_minutos': 'prazo de OLA da prioridade',
    'taxa_violacao_equipe': 'histórico de violação da equipe',
    'taxa_violacao_produto': 'histórico de violação do produto',
    'taxa_violacao_categoria': 'histórico de violação da categoria',
    'taxa_violacao_familia': 'histórico de violação da família de sinal',
    'carga_equipe_24h': 'incidentes abertos na equipe nas últimas 24h',
    'carga_equipe_1h': 'incidentes abertos na equipe na última hora',
    'volume_ci_7d': 'incidentes do mesmo ativo na última semana',
    'mttr_equipe_28d': 'tempo médio de resolução da equipe no mês',
    'volume_equipe_7d': 'volume da equipe na última semana',
}


def descrever(feature):
    """Nome legível de uma feature. Cai no próprio nome técnico quando não há tradução."""
    if feature in DESCRICOES:
        return DESCRICOES[feature]
    if feature.startswith('dim_'):
        return f'entidade do tipo {feature[4:]}'
    if feature.startswith('lag_'):
        return f'volume de {feature[4:]} dia(s) antes da origem'
    # Contexto anexado ao incidente: `equipe_p2_media_7` é a mesma feature de painel, só que
    # lida na véspera para a equipe daquele chamado.
    for prefixo, rotulo in (('equipe_', 'da equipe'), ('produto_', 'do produto')):
        if feature.startswith(prefixo):
            return f'{descrever(feature[len(prefixo):])} — {rotulo}'
    if feature.startswith('taxa_violacao_'):
        return f'histórico de violação por {feature[len("taxa_violacao_"):].replace("_", " ")}'
    if feature.endswith('_media_7'):
        return f'{feature[:-8].replace("_", " ")} — média da semana'
    if feature.endswith('_media_28'):
        return f'{feature[:-9].replace("_", " ")} — média do mês'
    return feature.replace('_', ' ')


def _estimador_final(modelo):
    """Desembrulha Pipeline e calibrador até chegar ao estimador que carrega os pesos.

    O modelo servido em produção é o calibrado, e o calibrador não expõe importância nem árvore —
    ele só reescala a saída. Para explicar, o que interessa é o estimador de dentro; é o mesmo
    modelo, com a mesma ordenação de casos.
    """
    if isinstance(modelo, Pipeline):
        return _estimador_final(modelo.steps[-1][1])
    # Por nome de classe, e não por atributo: um RandomForest também tem `estimator_` (o molde
    # da árvore), e desembrulhar por ali devolveria uma árvore só no lugar da floresta.
    if type(modelo).__name__ in ('CalibratedClassifierCV', 'FrozenEstimator'):
        interno = getattr(modelo, 'estimator', None)
        if interno is not None:
            return _estimador_final(interno)
    return modelo


def importancias_do_modelo(modelo, features, limite=20):
    """Importância interna: ganho acumulado (árvores) ou coeficiente padronizado (linear)."""
    estimador = _estimador_final(modelo)
    if hasattr(estimador, 'feature_importances_'):
        valores = np.asarray(estimador.feature_importances_, dtype=float)
        tipo = 'ganho'
    elif hasattr(estimador, 'coef_'):
        valores = np.abs(np.ravel(estimador.coef_)).astype(float)
        tipo = 'coeficiente'
    else:
        return []
    total = valores.sum() or 1.0
    ordem = np.argsort(valores)[::-1][:limite]
    return [
        {
            'feature': features[i],
            'descricao': descrever(features[i]),
            'importancia': round(float(valores[i] / total), 4),
            'tipo': tipo,
        }
        for i in ordem
    ]


def importancia_por_permutacao(modelo, X, y, scoring, repeticoes=5, semente=42, limite=20):
    """Quanto a métrica piora ao embaralhar cada coluna — medido fora do treino."""
    resultado = permutation_importance(
        modelo, X, y, scoring=scoring, n_repeats=repeticoes, random_state=semente, n_jobs=1,
    )
    medias = resultado.importances_mean
    ordem = np.argsort(medias)[::-1][:limite]
    return [
        {
            'feature': X.columns[i],
            'descricao': descrever(X.columns[i]),
            'queda_metrica': round(float(medias[i]), 5),
            'desvio': round(float(resultado.importances_std[i]), 5),
        }
        for i in ordem
    ]


class ExplicadorLinear:
    """Contribuição por feature de um modelo linear, no mesmo formato do explicador de árvore.

    Para um modelo linear a contribuição de Shapley tem forma fechada: é `coeficiente × (valor
    padronizado − média do treino)`. Não é aproximação nem heurística — é o valor exato sob a
    hipótese de independência entre features, a mesma que o `LinearExplainer` do SHAP assume.

    Existe porque nem todo vencedor é árvore: no risco de OLA quem ganhou foi a regressão
    logística, e deixar justamente esse modelo sem explicação seria perder a explicabilidade no
    lugar em que ela mais importa.
    """

    def __init__(self, modelo, X_referencia):
        self._preparo = modelo[:-1] if isinstance(modelo, Pipeline) else None
        estimador = _estimador_final(modelo)
        self.coeficientes = np.ravel(estimador.coef_).astype(float)
        intercepto = np.ravel(getattr(estimador, 'intercept_', [0.0]))[0]
        referencia = self._transformar(X_referencia)
        self.media = referencia.mean(axis=0)
        self.expected_value = float(intercepto + float(self.coeficientes @ self.media))

    def _transformar(self, X):
        if self._preparo is None:
            return np.asarray(X, dtype=float)
        return np.asarray(self._preparo.transform(X), dtype=float)

    def shap_values(self, X):
        return (self._transformar(X) - self.media) * self.coeficientes


def criar_explicador(modelo, X_referencia):
    """Explicador de contribuição local: SHAP de árvore quando dá, forma fechada linear quando não.

    Guardar o explicador junto do artefato evita reconstruí-lo a cada request — e deixa explícito
    quais modelos conseguem explicar previsão individual e quais não.
    """
    estimador = _estimador_final(modelo)
    if hasattr(estimador, 'feature_importances_'):
        try:
            import shap

            return shap.TreeExplainer(estimador)
        except Exception:
            return None
    if hasattr(estimador, 'coef_'):
        try:
            return ExplicadorLinear(modelo, X_referencia)
        except Exception:
            return None
    return None


def contribuicoes_shap(explicador, X_linha, features, limite=6):
    """Contribuição assinada de cada feature para uma previsão específica.

    Positivo = empurrou a previsão para cima. É literalmente o valor de Shapley calculado sobre
    a árvore treinada — não uma reconstrução narrativa do número.
    """
    if explicador is None:
        return []
    valores = explicador.shap_values(X_linha)
    valores = np.asarray(valores)
    if valores.ndim == 3:          # classificação binária devolve (n, features, classes)
        valores = valores[..., -1]
    linha = valores[0] if valores.ndim == 2 else valores
    ordem = np.argsort(np.abs(linha))[::-1][:limite]
    return [
        {
            'feature': features[i],
            'descricao': descrever(features[i]),
            'contribuicao': round(float(linha[i]), 3),
            'valor': round(float(np.asarray(X_linha)[0][i]), 3),
            'sentido': 'aumenta' if linha[i] > 0 else 'reduz',
        }
        for i in ordem
    ]


def resumo_importancias(cartao, limite=6):
    """Top features do cartão, preferindo permutação (mais confiável) sobre a interna."""
    fonte = cartao.get('importancia_permutacao') or cartao.get('importancias') or []
    return list(fonte)[:limite]


def tabela_importancias(cartoes):
    """Junta as importâncias de vários modelos para a tela comparativa."""
    return pd.DataFrame([
        {'modelo': c['nome'], **item}
        for c in cartoes for item in (c.get('importancias') or [])
    ])
