"""Treino dos modelos da plataforma.

Uma função por modelo, todas com a mesma espinha: preparar → dividir no tempo → competir na
validação → reajustar → medir no teste → explicar → registrar. `executar_tudo()` roda a
sequência inteira e é o que `manage.py treinar_modelos` e o notebook de comparação chamam.

Os números que saem daqui são os únicos que a plataforma exibe. Não há caminho no código em que
uma métrica seja escrita à mão.
"""

import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from . import config, dataset, explain, features, metrics, models_zoo, registry, selecao, splits

COBERTURA_ALVO = 0.80
JANELAS_WALK_FORWARD = 12


# ---------------------------------------------------------------------------------------------
# Preparação
# ---------------------------------------------------------------------------------------------

def preparar_base(banco=None, verbose=True):
    """Carrega incidentes, corta o período de backfill e monta painel e matriz de features."""
    incidentes = dataset.carregar_incidentes(banco)
    if incidentes.empty:
        vazio = pd.DataFrame()
        return {'incidentes': incidentes, 'painel': vazio, 'matriz': vazio,
                'inicio_regime': None, 'fim': None}

    diaria = (
        incidentes[incidentes['entrou_kpi']].groupby('data').size()
        .reindex(pd.date_range(incidentes['data'].min(), incidentes['data'].max()), fill_value=0)
    )
    inicio = splits.detectar_inicio_regime(diaria)
    incidentes = incidentes[incidentes['data'] >= inicio]

    painel = features.painel_diario(incidentes)
    # Nenhuma entidade com histórico suficiente: base recém-criada, ou recorte curto demais para
    # as janelas de 28 dias. Seguir daqui produziria uma matriz de features sem sentido.
    if painel.empty:
        return {'incidentes': incidentes, 'painel': painel, 'matriz': pd.DataFrame(),
                'inicio_regime': inicio, 'fim': incidentes['data'].max()}

    matriz = features.construir_features(painel)

    if verbose:
        print(f'Regime operacional detectado a partir de {inicio:%Y-%m-%d} '
              f'({len(incidentes):,} incidentes, {painel["chave"].nunique()} entidades).')
        print(f'Matriz de features: {matriz.shape[0]:,} linhas × '
              f'{len(features.colunas_de_features(matriz))} features.')

    return {
        'incidentes': incidentes,
        'painel': painel,
        'matriz': matriz,
        'inicio_regime': inicio,
        'fim': incidentes['data'].max(),
    }


def _sem_constantes(X_treino, X_outros):
    """Remove colunas constantes no treino: não informam nada e sujam a importância."""
    uteis = [c for c in X_treino.columns if X_treino[c].nunique(dropna=False) > 1]
    return [X_treino[uteis]] + [X[uteis] for X in X_outros]


# ---------------------------------------------------------------------------------------------
# Previsão de volume (D+1 e D+7)
# ---------------------------------------------------------------------------------------------

def _margens_relativas(residuos, escala, chaves, cobertura=COBERTURA_ALVO, minimo=30):
    """Faixa da previsão como fração da escala da entidade, não como número absoluto.

    Um erro de 8 incidentes é irrelevante no volume global e é o dobro do movimento típico de um
    produto pequeno. Guardar a margem relativa (|erro| ÷ nível recente) deixa a mesma regra
    valer para as duas escalas; na hora de prever ela volta a ser absoluta multiplicada pelo
    nível daquela entidade.
    """
    relativo = np.abs(residuos) / np.maximum(escala, 1.0)
    geral = float(np.quantile(relativo, cobertura))
    por_chave = {}
    for valor in pd.unique(chaves):
        recorte = relativo[chaves == valor]
        if len(recorte) >= minimo:
            por_chave[str(valor)] = float(np.quantile(recorte, cobertura))
    return {'geral': round(geral, 4), 'por_chave': {k: round(v, 4) for k, v in por_chave.items()}}


def _avaliar_walk_forward(definicao, parametros, matriz, colunas, janelas=JANELAS_WALK_FORWARD):
    """Retreina o vencedor a cada origem semanal e mede o erro fora da amostra em cada uma.

    É mais caro que avaliar uma partição só, e é o que responde à pergunta que a operação faz:
    "quanto esse modelo erra tipicamente quando roda todo dia?" — em vez de "quanto ele errou
    naquela semana específica".
    """
    resultados = []
    for corte, mascara_treino, mascara_teste in splits.particoes_walk_forward(matriz, janelas):
        modelo = selecao._instanciar(definicao, parametros)
        X_treino = matriz.loc[mascara_treino, colunas]
        X_teste = matriz.loc[mascara_teste, colunas]
        y_treino = matriz.loc[mascara_treino, 'alvo']
        y_teste = matriz.loc[mascara_teste, 'alvo']
        modelo.fit(X_treino, y_treino)
        previsto = np.clip(modelo.predict(X_teste), 0, None)
        base = X_teste['mesmo_dow_1'].to_numpy() if 'mesmo_dow_1' in X_teste else None
        resultado = metrics.regressao(y_teste, previsto, base)
        resultado['origem'] = corte.date().isoformat()
        resultados.append(resultado)

    if not resultados:
        return None
    return {
        'janelas': len(resultados),
        'mae': round(float(np.mean([r['mae'] for r in resultados])), 3),
        'rmse': round(float(np.mean([r['rmse'] for r in resultados])), 3),
        'mae_baseline': round(float(np.mean([r['mae_baseline'] for r in resultados])), 3),
        'mase': round(float(np.mean([r['mase'] for r in resultados if r['mase']])), 3),
        'dispersao_mae': round(float(np.std([r['mae'] for r in resultados])), 3),
        'vitorias_sobre_baseline': int(
            sum(1 for r in resultados if r['mae'] < r['mae_baseline'])
        ),
        'por_origem': [
            {'origem': r['origem'], 'mae': round(r['mae'], 2),
             'mae_baseline': round(r['mae_baseline'], 2), 'n': r['n']}
            for r in resultados
        ],
    }


def treinar_volume(base, nome, titulo, objetivo, horizontes, ordem, notebook):
    """Treina um modelo de volume para os horizontes pedidos e registra o artefato."""
    matriz = base['matriz'][base['matriz']['horizonte'].isin(list(horizontes))].copy()
    matriz = matriz.reset_index(drop=True)
    treino, validacao, teste = splits.dividir(matriz)

    colunas = features.colunas_de_features(matriz)
    X = matriz[colunas].astype(float).fillna(0.0).replace([np.inf, -np.inf], 0.0)
    if len(horizontes) == 1:
        # Com horizonte único a coluna vira constante — sai para não poluir a importância.
        X = X.drop(columns=['horizonte'])
    y = matriz['alvo'].astype(float)

    X_treino, X_validacao, X_teste = X[treino], X[validacao], X[teste]
    y_treino, y_validacao, y_teste = y[treino], y[validacao], y[teste]

    melhor, tabela = selecao.competir(
        models_zoo.candidatos_regressao(),
        X_treino, y_treino, X_validacao, y_validacao,
        avaliar=selecao.avaliador_regressao(), chave_metrica='mae',
    )

    # Reajuste em treino + validação: mais dado para o modelo que já foi escolhido.
    X_ajuste = pd.concat([X_treino, X_validacao])
    y_ajuste = pd.concat([y_treino, y_validacao])
    modelo = selecao.reajustar(melhor, X_ajuste, y_ajuste)

    previsto_teste = np.clip(modelo.predict(X_teste), 0, None)
    resultado_teste = metrics.regressao(
        y_teste, previsto_teste, X_teste['mesmo_dow_1'].to_numpy(),
    )

    # O MAE do painel inteiro mistura escalas: 1,9 incidentes/dia é pouco no volume global e
    # muito num produto de 3/dia. Sem este recorte o número médio engana quem lê.
    identificacao_teste = matriz.loc[teste]
    por_dimensao = {}
    for dimensao, indices in identificacao_teste.groupby('dimensao').groups.items():
        posicao = identificacao_teste.index.get_indexer(indices)
        por_dimensao[dimensao] = metrics.arredondar(metrics.regressao(
            y_teste.to_numpy()[posicao], previsto_teste[posicao],
            X_teste['mesmo_dow_1'].to_numpy()[posicao],
        ))
    por_horizonte = {}
    if len(horizontes) > 1:
        for h, indices in identificacao_teste.groupby('horizonte').groups.items():
            posicao = identificacao_teste.index.get_indexer(indices)
            por_horizonte[int(h)] = metrics.arredondar(metrics.regressao(
                y_teste.to_numpy()[posicao], previsto_teste[posicao],
                X_teste['mesmo_dow_1'].to_numpy()[posicao],
            ))

    # Faixa da previsão: calibrada na validação (fora do treino), medida no teste.
    previsto_validacao = np.clip(melhor['modelo'].predict(X_validacao), 0, None)
    margens = _margens_relativas(
        y_validacao.to_numpy() - previsto_validacao,
        X_validacao['media_7'].to_numpy(),
        matriz.loc[validacao, 'dimensao'].to_numpy(),
    )
    escala_teste = np.maximum(X_teste['media_7'].to_numpy(), 1.0)
    margem_teste = np.array([
        margens['por_chave'].get(d, margens['geral'])
        for d in matriz.loc[teste, 'dimensao']
    ]) * escala_teste
    cobertura = float(np.mean(np.abs(y_teste.to_numpy() - previsto_teste) <= margem_teste))

    # Previsões do teste gravadas linha a linha: são previsões genuinamente fora da amostra,
    # feitas por um modelo que não viu esses dias, e com o valor real já conhecido. É o que
    # popula a tela de histórico previsão × real sem esperar a plataforma rodar por um mês.
    saida = identificacao_teste[['dimensao', 'chave', 'data_origem', 'data_alvo', 'horizonte']].copy()
    saida['real'] = y_teste.to_numpy()
    saida['previsto'] = np.round(previsto_teste, 2)
    saida['margem'] = np.round(margem_teste, 2)
    saida['modelo'] = nome
    saida['algoritmo'] = models_zoo.nome_algoritmo(modelo)
    config.garantir_diretorios()
    saida.to_csv(config.DADOS_DIR / f'previsoes_teste_{nome}.csv', index=False)

    caminho_walk = _avaliar_walk_forward(
        melhor['definicao'], melhor['parametros'], matriz.assign(**{c: X[c] for c in X.columns}),
        list(X.columns),
    )

    importancia_permutacao = explain.importancia_por_permutacao(
        modelo, X_validacao, y_validacao, scoring='neg_mean_absolute_error',
    )
    explicador = explain.criar_explicador(modelo, X_ajuste)

    baseline = next(
        (linha for linha in tabela if linha['candidato'] == 'Baseline sazonal'), None,
    )
    cartao = {
        'titulo': titulo,
        'objetivo': objetivo,
        'tipo': 'regressao',
        'ordem': ordem,
        'notebook': notebook,
        'algoritmo': models_zoo.nome_algoritmo(modelo),
        'candidato': melhor['nome'],
        'hiperparametros': melhor['parametros'],
        'alvo': f'{config.ALVO_VOLUME} em t+{min(horizontes)}'
                + (f'..t+{max(horizontes)}' if len(horizontes) > 1 else ''),
        'horizontes': list(horizontes),
        'features': list(X.columns),
        'n_features': len(X.columns),
        'entidades': int(matriz['chave'].nunique()),
        'metrica_principal': 'mae',
        'particoes': splits.descricao(matriz),
        'metricas': {
            'validacao': metrics.arredondar(melhor['linha']['validacao']),
            'teste': metrics.arredondar(resultado_teste),
            'teste_por_dimensao': por_dimensao,
            'teste_por_horizonte': por_horizonte,
            'walk_forward': caminho_walk,
        },
        'baseline': {
            'nome': 'Baseline sazonal (mesmo dia da semana anterior)',
            'validacao': baseline['validacao'] if baseline else None,
            'mae_teste': round(resultado_teste['mae_baseline'], 3),
        },
        'ganho_vs_baseline': round(resultado_teste['ganho_vs_baseline'], 1),
        'comparacao': selecao.resumo_comparacao(tabela, ('mae', 'rmse', 'mase', 'r2')),
        'comparacao_completa': [
            {k: v for k, v in linha.items() if k != 'modelo'} for linha in tabela
        ],
        'importancias': explain.importancias_do_modelo(modelo, list(X.columns)),
        'importancia_permutacao': importancia_permutacao,
        'faixa': {
            'cobertura_alvo': COBERTURA_ALVO,
            'cobertura_teste': round(cobertura, 3),
            'margens_relativas': margens,
        },
        'linhas_treino': int(len(X_ajuste)),
        'glossario': {k: v for k, v in metrics.GLOSSARIO.items()
                      if k in ('mae', 'rmse', 'mape', 'r2', 'mase', 'vies')},
    }

    return registry.salvar(nome, modelo, cartao, extras={
        'explicador': explicador,
        'colunas': list(X.columns),
        'margens': margens,
    })


# ---------------------------------------------------------------------------------------------
# Classificação de pico operacional
# ---------------------------------------------------------------------------------------------

def treinar_pico(base, nome='model_pico', ordem=3,
                 notebook='modelos_ml/06_deteccao_de_anomalias.ipynb', percentil=0.80):
    """Classifica se o dia previsto será de pico para aquela entidade.

    O limiar de pico é o percentil 80 do volume **de cada entidade, calculado só no treino**:
    pico é um conceito relativo (80 incidentes é rotina no global e catástrofe num produto), e
    calcular o limiar sobre a série inteira colocaria informação do teste dentro do rótulo.
    """
    matriz = base['matriz'][base['matriz']['horizonte'] <= 7].copy().reset_index(drop=True)
    treino, validacao, teste = splits.dividir(matriz)

    limiares = (
        matriz[treino].groupby(['dimensao', 'chave'])['alvo'].quantile(percentil)
        .rename('limiar_pico')
    )
    matriz = matriz.merge(limiares, on=['dimensao', 'chave'], how='left')
    matriz['limiar_pico'] = matriz['limiar_pico'].fillna(matriz['alvo'].quantile(percentil))
    alvo = (matriz['alvo'] >= np.maximum(matriz['limiar_pico'], 1)).astype(int)

    colunas = [c for c in features.colunas_de_features(matriz) if c != 'limiar_pico']
    X = matriz[colunas].astype(float).fillna(0.0).replace([np.inf, -np.inf], 0.0)

    X_treino, X_validacao, X_teste = X[treino], X[validacao], X[teste]
    y_treino, y_validacao, y_teste = alvo[treino], alvo[validacao], alvo[teste]

    melhor, tabela = selecao.competir(
        models_zoo.candidatos_classificacao(
            peso_positivo=models_zoo.peso_para_desbalanceamento(y_treino),
            coluna_regra='razao_7_28',
        ),
        X_treino, y_treino, X_validacao, y_validacao,
        avaliar=selecao.avaliador_classificacao(), chave_metrica='pr_auc', maior_melhor=True,
    )

    modelo_base = melhor['modelo']
    modelo, calibracao = _calibrar(modelo_base, X_validacao, y_validacao, X_teste, y_teste)
    limiar = metrics.melhor_limiar(y_validacao, modelo.predict_proba(X_validacao)[:, 1])
    prob_teste = modelo.predict_proba(X_teste)[:, 1]
    resultado_teste = metrics.classificacao(y_teste, prob_teste, limiar)

    cartao = {
        'titulo': 'Risco de pico operacional',
        'objetivo': 'Probabilidade de o volume do dia previsto entrar no topo 20% da entidade.',
        'tipo': 'classificacao',
        'ordem': ordem,
        'notebook': notebook,
        'algoritmo': models_zoo.nome_algoritmo(modelo),
        'candidato': melhor['nome'],
        'hiperparametros': melhor['parametros'],
        'alvo': f'volume em t+h ≥ percentil {int(percentil * 100)} da própria entidade',
        'features': list(X.columns),
        'n_features': len(X.columns),
        'metrica_principal': 'pr_auc',
        'limiar_decisao': round(limiar, 4),
        'particoes': splits.descricao(matriz),
        'metricas': {
            'validacao': metrics.arredondar(melhor['linha']['validacao']),
            'teste': metrics.arredondar(resultado_teste),
        },
        'baseline': next(
            ({'nome': l['candidato'], 'validacao': l['validacao']}
             for l in tabela if l['candidato'] == 'Baseline (prevalência)'), None,
        ),
        'comparacao': selecao.resumo_comparacao(
            tabela, ('pr_auc', 'roc_auc', 'f1', 'recall'), maior_melhor=True,
        ),
        'calibracao': calibracao,
        'importancias': explain.importancias_do_modelo(modelo_base, list(X.columns)),
        'importancia_permutacao': explain.importancia_por_permutacao(
            modelo, X_validacao, y_validacao, scoring='average_precision',
        ),
        'limiares_pico': {
            f'{d}|{c}': round(float(v), 2) for (d, c), v in limiares.items()
        },
        'linhas_treino': int(treino.sum() + validacao.sum()),
        'glossario': {k: v for k, v in metrics.GLOSSARIO.items()
                      if k in ('precision', 'recall', 'f1', 'roc_auc', 'pr_auc', 'brier')},
    }
    return registry.salvar(nome, modelo, cartao, extras={
        'explicador': explain.criar_explicador(modelo_base, X_treino),
        'colunas': list(X.columns),
        'limiar': limiar,
        'limiares_pico': limiares.to_dict(),
    })


# ---------------------------------------------------------------------------------------------
# Risco de violação de OLA
# ---------------------------------------------------------------------------------------------

def _calibrar(modelo_base, X_validacao, y_validacao, X_teste, y_teste):
    """Calibra a probabilidade do classificador na validação (Platt) e mede o efeito no teste.

    Sem isso, o número que a plataforma chamaria de "probabilidade" seria ficção: treinar com
    `class_weight='balanced'` (necessário, já que a violação de OLA é ~1% dos casos) faz o modelo
    aprender sobre uma proporção artificial de classes, e a saída sai numa escala que não
    corresponde a frequência nenhuma do mundo real — a média das probabilidades previstas dava
    32% onde a taxa observada é 1%.

    O ranking dos casos continua o mesmo (ROC-AUC não muda); o que muda é a escala poder ser
    lida como probabilidade e composta com o volume previsto. O Brier antes e depois fica
    registrado no cartão como evidência de que a calibração fez efeito.
    """
    calibrado = CalibratedClassifierCV(
        FrozenEstimator(modelo_base), method='sigmoid',
    ).fit(X_validacao, y_validacao)

    antes = metrics.classificacao(y_teste, modelo_base.predict_proba(X_teste)[:, 1])
    depois = metrics.classificacao(y_teste, calibrado.predict_proba(X_teste)[:, 1])
    diagnostico = {
        'metodo': 'Platt (sigmoide) ajustada na validação',
        'brier_antes': round(antes['brier'], 5),
        'brier_depois': round(depois['brier'], 5),
        'probabilidade_media_antes': round(
            float(modelo_base.predict_proba(X_teste)[:, 1].mean()), 5,
        ),
        'probabilidade_media_depois': round(
            float(calibrado.predict_proba(X_teste)[:, 1].mean()), 5,
        ),
        'taxa_real_teste': round(float(np.mean(y_teste)), 5),
    }
    return calibrado, diagnostico


def _contribuicoes_medias(explicador, X, colunas, grupos, limite=5):
    """SHAP médio por grupo — o que empurra o risco de cada equipe/produto, em média."""
    if explicador is None:
        return {}
    valores = np.asarray(explicador.shap_values(X))
    if valores.ndim == 3:
        valores = valores[..., -1]
    quadro = pd.DataFrame(valores, columns=colunas, index=grupos.index)
    saida = {}
    for chave, bloco in quadro.groupby(grupos.to_numpy()):
        medias = bloco.mean()
        topo = medias.reindex(medias.abs().sort_values(ascending=False).index)[:limite]
        saida[str(chave)] = [
            {
                'feature': feature,
                'descricao': explain.descrever(feature),
                'contribuicao': round(float(valor), 4),
                'sentido': 'aumenta' if valor > 0 else 'reduz',
            }
            for feature, valor in topo.items()
        ]
    return saida


def treinar_ola(base, nome='model_ola', ordem=4, notebook='modelos_ml/05_risco_de_ola.ipynb',
                dias_teste=45, dias_validacao=45):
    """Classificador de violação de OLA por incidente, com o que se sabe na abertura.

    A classe é rara (≈1% dos elegíveis). Isso muda três coisas, todas explícitas no cartão:
    o peso da classe positiva no treino, o limiar de decisão escolhido na validação (0,5 nunca
    dispararia) e a métrica principal — PR-AUC, não acurácia: prever "não viola" sempre já
    acerta 99% dos casos e não serve para nada.
    """
    X, y, identificacao = features.features_incidentes(base['incidentes'], base['painel'])
    datas = identificacao['data']

    fim = datas.max()
    inicio_teste = fim - pd.Timedelta(days=dias_teste - 1)
    inicio_validacao = inicio_teste - pd.Timedelta(days=dias_validacao)
    treino, validacao, teste = (
        datas < inicio_validacao,
        (datas >= inicio_validacao) & (datas < inicio_teste),
        datas >= inicio_teste,
    )

    X_treino, X_validacao, X_teste = _sem_constantes(X[treino], [X[validacao], X[teste]])
    y_treino, y_validacao, y_teste = y[treino], y[validacao], y[teste]

    melhor, tabela = selecao.competir(
        models_zoo.candidatos_classificacao(
            peso_positivo=models_zoo.peso_para_desbalanceamento(y_treino),
            coluna_regra='taxa_violacao_equipe',
        ),
        X_treino, y_treino, X_validacao, y_validacao,
        avaliar=selecao.avaliador_classificacao(), chave_metrica='pr_auc', maior_melhor=True,
    )

    modelo_base = melhor['modelo']
    modelo, calibracao = _calibrar(modelo_base, X_validacao, y_validacao, X_teste, y_teste)
    limiar = metrics.melhor_limiar(y_validacao, modelo.predict_proba(X_validacao)[:, 1])
    prob_teste = modelo.predict_proba(X_teste)[:, 1]
    resultado_teste = metrics.classificacao(y_teste, prob_teste, limiar)

    # Probabilidade média por equipe e por produto nos incidentes recentes: é o que compõe,
    # junto com o volume previsto, o risco de OLA de amanhã por entidade.
    recentes = identificacao['data'] >= fim - pd.Timedelta(days=28)
    colunas = list(X_treino.columns)
    X_recentes = X.loc[recentes, colunas]
    prob_recentes = modelo.predict_proba(X_recentes)[:, 1]
    perfil = identificacao.loc[recentes].assign(probabilidade=prob_recentes)
    probabilidade_media = {
        'global': float(perfil['probabilidade'].mean()),
        'equipe': perfil.groupby('equipe')['probabilidade'].mean().round(5).to_dict(),
        'produto': perfil.groupby('produto')['probabilidade'].mean().round(5).to_dict(),
    }

    # Por que cada equipe carrega o risco que carrega: SHAP médio dos incidentes recentes dela.
    # Calculado aqui (uma vez, no treino) em vez de a cada request — e ainda assim vindo do
    # modelo, não de uma narrativa escrita à mão sobre o número.
    explicador = explain.criar_explicador(modelo_base, X_treino)
    contribuicoes = {
        'equipe': _contribuicoes_medias(explicador, X_recentes, colunas, perfil['equipe']),
        'produto': _contribuicoes_medias(explicador, X_recentes, colunas, perfil['produto']),
    }

    cartao = {
        'titulo': 'Risco de violação de OLA',
        'objetivo': 'Probabilidade de um incidente elegível estourar o prazo de OLA da sua prioridade.',
        'tipo': 'classificacao',
        'ordem': ordem,
        'notebook': notebook,
        'algoritmo': models_zoo.nome_algoritmo(modelo),
        'candidato': melhor['nome'],
        'hiperparametros': melhor['parametros'],
        'alvo': 'KPI Violado? = Sim, entre os incidentes elegíveis a KPI',
        'features': colunas,
        'n_features': len(colunas),
        'metrica_principal': 'pr_auc',
        'limiar_decisao': round(limiar, 4),
        'desbalanceamento': {
            'positivos_treino': int(y_treino.sum()),
            'taxa_positivos': round(float(y.mean()), 5),
            'peso_positivo': round(models_zoo.peso_para_desbalanceamento(y_treino), 1),
        },
        'particoes': {
            'treino': {'linhas': int(treino.sum()), 'inicio': str(datas[treino].min().date()),
                       'fim': str(datas[treino].max().date()),
                       'positivos': int(y_treino.sum())},
            'validacao': {'linhas': int(validacao.sum()),
                          'inicio': str(datas[validacao].min().date()),
                          'fim': str(datas[validacao].max().date()),
                          'positivos': int(y_validacao.sum())},
            'teste': {'linhas': int(teste.sum()), 'inicio': str(datas[teste].min().date()),
                      'fim': str(datas[teste].max().date()), 'positivos': int(y_teste.sum())},
        },
        'metricas': {
            'validacao': metrics.arredondar(melhor['linha']['validacao']),
            'teste': metrics.arredondar(resultado_teste),
        },
        'baseline': next(
            ({'nome': l['candidato'], 'validacao': l['validacao']}
             for l in tabela if l['candidato'] == 'Baseline (taxa histórica)'), None,
        ),
        'comparacao': selecao.resumo_comparacao(
            tabela, ('pr_auc', 'roc_auc', 'f1', 'recall'), maior_melhor=True,
        ),
        'calibracao': calibracao,
        'importancias': explain.importancias_do_modelo(modelo_base, colunas),
        'importancia_permutacao': explain.importancia_por_permutacao(
            modelo, X_validacao, y_validacao, scoring='average_precision',
        ),
        'probabilidade_media': probabilidade_media,
        'contribuicoes': contribuicoes,
        'linhas_treino': int(treino.sum() + validacao.sum()),
        'glossario': {k: v for k, v in metrics.GLOSSARIO.items()
                      if k in ('precision', 'recall', 'f1', 'roc_auc', 'pr_auc', 'brier')},
    }
    return registry.salvar(nome, modelo, cartao, extras={
        'explicador': explicador,
        'colunas': colunas,
        'limiar': limiar,
        'probabilidade_media': probabilidade_media,
        'contribuicoes': contribuicoes,
    })


# ---------------------------------------------------------------------------------------------
# Detecção de anomalias (não supervisionada)
# ---------------------------------------------------------------------------------------------

def treinar_anomalia(base, nome='model_anomalia', ordem=5,
                     notebook='modelos_ml/06_deteccao_de_anomalias.ipynb', contaminacao=0.05):
    """Isolation Forest sobre o comportamento diário de cada entidade.

    Não há rótulo de "dia anômalo" no dataset — então não há AUC a reportar, e inventar uma
    seria pior que não ter. O que se reporta é a concordância com o z-score de 21 dias que a
    plataforma já usava: onde os dois apontam junto, a evidência é forte; onde divergem, o
    Isolation Forest costuma estar vendo combinação de fatores (volume normal com severidade e
    MTTR altos) que a régua de uma variável só não enxerga.
    """
    contexto = features.historico_painel(base['painel'])
    colunas = [
        'lag_0', 'media_7', 'razao_7_28', 'var_7_vs_7', 'desvio_7', 'cv_28',
        'criticos_media_7', 'share_criticos_7', 'p2_media_7', 'violacoes_media_7',
        'taxa_violacao_media_7', 'mttr_medio_media_7', 'delta_mttr',
        'itens_distintos_media_7', 'familias_distintas_media_7',
    ]
    quadro = contexto.dropna(subset=colunas).reset_index(drop=True)
    X = quadro[colunas].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    escala = StandardScaler().fit(X)
    modelo = IsolationForest(
        n_estimators=300, contamination=contaminacao, random_state=config.SEMENTE, n_jobs=-1,
    ).fit(escala.transform(X))

    pontuacao = -modelo.score_samples(escala.transform(X))
    limiar = float(np.quantile(pontuacao, 1 - contaminacao))

    # Comparação com o z-score móvel de 21 dias, que é a régua que a plataforma já usava.
    serie = quadro.assign(pontuacao=pontuacao)
    z = (
        serie.groupby(['dimensao', 'chave'])['lag_0']
        .transform(lambda s: (s - s.rolling(21, min_periods=10).mean())
                   / s.rolling(21, min_periods=10).std().replace(0, np.nan))
    )
    marcado_if = pontuacao >= limiar
    marcado_z = z.abs() >= 2.5
    ambos = int((marcado_if & marcado_z.fillna(False)).sum())

    cartao = {
        'titulo': 'Detecção de anomalias',
        'objetivo': 'Marcar dias cujo comportamento operacional foge do padrão da própria entidade.',
        'tipo': 'anomalia',
        'ordem': ordem,
        'notebook': notebook,
        'algoritmo': 'IsolationForest',
        'candidato': 'Isolation Forest',
        'hiperparametros': {'n_estimators': 300, 'contamination': contaminacao},
        'alvo': 'não supervisionado — sem rótulo de anomalia no dataset',
        'features': colunas,
        'n_features': len(colunas),
        'metrica_principal': 'concordancia_zscore',
        'metricas': {
            'dias_avaliados': int(len(quadro)),
            'marcados': int(marcado_if.sum()),
            'marcados_zscore': int(marcado_z.fillna(False).sum()),
            'concordancia_zscore': round(
                ambos / max(int(marcado_if.sum()), 1), 3,
            ),
            'limiar_pontuacao': round(limiar, 4),
        },
        'observacoes': [
            'Modelo não supervisionado: o dataset não traz rótulo de anomalia, então não há '
            'precisão nem recall a reportar — qualquer número desse tipo seria inventado.',
            'A concordância com o z-score móvel de 21 dias é o que se pode medir: mostra o '
            'quanto o modelo reencontra o que a régua univariada já via.',
        ],
        'linhas_treino': int(len(X)),
    }
    return registry.salvar(nome, modelo, cartao, extras={
        'escala': escala, 'colunas': colunas, 'limiar': limiar,
    })


# ---------------------------------------------------------------------------------------------
# Clusterização de produtos
# ---------------------------------------------------------------------------------------------

def treinar_clusters(base, nome='model_cluster', ordem=6,
                     notebook='modelos_ml/02_engenharia_de_features.ipynb', k_maximo=7):
    """Agrupa produtos por comportamento operacional (não por volume).

    O k não é escolhido no olho: roda de 2 a `k_maximo` e fica com o de maior silhueta, que é a
    métrica que a clusterização de fato tem. O perfil de cada grupo vai junto no cartão, senão
    o número do cluster não diz nada a quem opera.
    """
    painel = base['painel']
    produtos = painel[painel['dimensao'] == 'produto']

    perfil = produtos.groupby('chave').agg(
        volume_medio=('total_kpi', 'mean'),
        desvio=('total_kpi', 'std'),
        share_criticos=('criticos', 'sum'),
        total_kpi=('total_kpi', 'sum'),
        violacoes=('violacoes', 'sum'),
        mttr=('mttr_medio', 'mean'),
        dias_ativos=('total_kpi', lambda s: float((s > 0).mean())),
        ruido=('monitoramento', 'sum'),
        bruto=('total', 'sum'),
    )
    perfil['share_criticos'] = perfil['share_criticos'] / perfil['total_kpi'].replace(0, np.nan)
    perfil['taxa_violacao'] = perfil['violacoes'] / perfil['total_kpi'].replace(0, np.nan)
    perfil['share_ruido'] = perfil['ruido'] / perfil['bruto'].replace(0, np.nan)
    perfil['instabilidade'] = perfil['desvio'] / perfil['volume_medio'].replace(0, np.nan)

    colunas = ['volume_medio', 'share_criticos', 'taxa_violacao', 'mttr', 'dias_ativos',
               'share_ruido', 'instabilidade']
    X = perfil[colunas].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    escala = StandardScaler().fit(X)
    Z = escala.transform(X)

    avaliacao = []
    melhor = None
    for k in range(2, min(k_maximo, len(X) - 1) + 1):
        modelo = KMeans(n_clusters=k, n_init=20, random_state=config.SEMENTE).fit(Z)
        silhueta = float(silhouette_score(Z, modelo.labels_))
        avaliacao.append({'k': k, 'silhueta': round(silhueta, 4),
                          'inercia': round(float(modelo.inertia_), 2)})
        if melhor is None or silhueta > melhor['silhueta']:
            melhor = {'k': k, 'silhueta': silhueta, 'modelo': modelo}

    rotulos = pd.Series(melhor['modelo'].labels_, index=perfil.index, name='cluster')
    grupos = perfil.assign(cluster=rotulos).groupby('cluster')[colunas].mean().round(3)

    cartao = {
        'titulo': 'Clusterização de produtos',
        'objetivo': 'Agrupar produtos com comportamento operacional semelhante.',
        'tipo': 'clusterizacao',
        'ordem': ordem,
        'notebook': notebook,
        'algoritmo': 'KMeans',
        'candidato': 'K-Means',
        'hiperparametros': {'n_clusters': melhor['k'], 'n_init': 20},
        'alvo': 'não supervisionado — agrupamento por perfil operacional',
        'features': colunas,
        'n_features': len(colunas),
        'metrica_principal': 'silhueta',
        'metricas': {
            'silhueta': round(melhor['silhueta'], 4),
            'k_escolhido': melhor['k'],
            'produtos': int(len(perfil)),
            'busca_k': avaliacao,
        },
        'grupos': {
            str(cluster): {
                'produtos': sorted(rotulos[rotulos == cluster].index.tolist()),
                'perfil': linha.to_dict(),
            }
            for cluster, linha in grupos.iterrows()
        },
        'linhas_treino': int(len(X)),
    }
    return registry.salvar(nome, melhor['modelo'], cartao, extras={
        'escala': escala, 'colunas': colunas, 'rotulos': rotulos.to_dict(),
    })


# ---------------------------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------------------------

def executar_tudo(banco=None, verbose=True):
    """Roda o pipeline completo e grava `ml/models/registry.json`."""
    inicio = time.perf_counter()
    config.garantir_diretorios()
    base = preparar_base(banco, verbose=verbose)

    cartoes = []
    etapas = (
        ('model_d1', lambda: treinar_volume(
            base, 'model_d1', 'Previsão D+1',
            'Volume de incidentes elegíveis a KPI no dia seguinte.',
            horizontes=[1], ordem=1, notebook='modelos_ml/03_previsao_volume_d1.ipynb')),
        ('model_d7', lambda: treinar_volume(
            base, 'model_d7', 'Previsão D+7',
            'Volume diário dos próximos 7 dias, por entidade.',
            horizontes=list(range(1, 8)), ordem=2,
            notebook='modelos_ml/04_previsao_volume_d7.ipynb')),
        ('model_pico', lambda: treinar_pico(base)),
        ('model_ola', lambda: treinar_ola(base)),
        ('model_anomalia', lambda: treinar_anomalia(base)),
        ('model_cluster', lambda: treinar_clusters(base)),
    )

    for nome, executar in etapas:
        marca = time.perf_counter()
        if verbose:
            print(f'\n> {nome}...')
        cartao = executar()
        cartao['segundos_treino_total'] = round(time.perf_counter() - marca, 1)
        cartoes.append(cartao)
        if verbose:
            principal = cartao['metrica_principal']
            valor = (cartao['metricas'].get('teste') or cartao['metricas']).get(principal)
            print(f'  {cartao["algoritmo"]} · {principal}={valor} '
                  f'({cartao["segundos_treino_total"]}s)')

    resumo = {
        'executado_em': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'segundos': round(time.perf_counter() - inicio, 1),
        'inicio_regime': base['inicio_regime'].date().isoformat(),
        'fim_dados': base['fim'].date().isoformat(),
        'incidentes': int(len(base['incidentes'])),
        'entidades': int(base['painel'].groupby(['dimensao', 'chave']).ngroups),
        'linhas_matriz': int(len(base['matriz'])),
        'modelos': [
            {'nome': c['nome'], 'titulo': c['titulo'], 'algoritmo': c['algoritmo'],
             'metrica_principal': c['metrica_principal'],
             'valor': (c['metricas'].get('teste') or c['metricas']).get(c['metrica_principal'])}
            for c in cartoes
        ],
    }
    registry.salvar_indice(resumo)
    if verbose:
        print(f'\nPipeline concluído em {resumo["segundos"]}s. '
              f'Artefatos em {config.MODELOS_DIR}.')
    return resumo, cartoes
