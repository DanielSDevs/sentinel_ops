"""Catálogo de algoritmos candidatos e o protocolo de seleção.

Todo modelo da plataforma passa por aqui, e todos competem no mesmo tabuleiro: as mesmas
features, o mesmo recorte de treino, a mesma validação. Quem vence, vence por métrica medida —
não por preferência.

As baselines entram como estimadores de verdade (e não como um cálculo à parte) justamente para
que a comparação seja honesta: mesma partição, mesma métrica, mesma tabela.
"""

import numpy as np
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.base import BaseEstimator, ClassifierMixin, RegressorMixin
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRegressor

from . import config


class PreverColuna(BaseEstimator, RegressorMixin):
    """Baseline que devolve o valor de uma coluna já presente nas features.

    Com `mesmo_dow_1` vira a baseline sazonal ingênua (repetir o mesmo dia da semana anterior),
    que é a referência clássica em série temporal e o padrão de comparação do MASE.
    """

    def __init__(self, coluna='mesmo_dow_1'):
        self.coluna = coluna

    def fit(self, X, y=None):
        self.indice_ = list(X.columns).index(self.coluna)
        return self

    def predict(self, X):
        return np.asarray(X.iloc[:, self.indice_], dtype=float)


class RegraProbabilidade(BaseEstimator, ClassifierMixin):
    """Baseline de classificação que usa uma taxa histórica já calculada como probabilidade.

    É a "regra fixa" que a plataforma usaria sem ML — está aqui para mostrar o quanto o ML
    acrescenta (ou não) sobre ela.
    """

    def __init__(self, coluna='taxa_violacao_media_28'):
        self.coluna = coluna

    def fit(self, X, y):
        self.indice_ = list(X.columns).index(self.coluna)
        self.classes_ = np.array([0, 1])
        return self

    def predict_proba(self, X):
        p = np.clip(np.asarray(X.iloc[:, self.indice_], dtype=float), 0.0, 1.0)
        return np.column_stack([1 - p, p])

    def predict(self, X):
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


def candidatos_regressao(semente=config.SEMENTE, baseline='mesmo_dow_1'):
    """Famílias candidatas para previsão de volume, com uma grade pequena de hiperparâmetros.

    A grade é curta de propósito: com dezenas de milhares de linhas e ~85 features, o ganho de uma
    busca exaustiva é menor que o risco de escolher ruído da validação.

    **Toda floresta tem `max_depth` limitado.** É restrição de deploy, aplicada igualmente a todos
    os candidatos *antes* da competição: sem teto, uma Random Forest sobre 110 mil linhas gera um
    artefato de centenas de megabytes, que a aplicação teria de desserializar a cada processo. O
    limite entra como parte da definição do candidato, não como desempate posterior — desempatar
    depois de ver o resultado seria escolher o modelo pela conveniência.
    """
    return {
        'Baseline sazonal': {
            'tipo': 'baseline',
            'estimador': lambda: PreverColuna(coluna=baseline),
            'grade': [{}],
        },
        'Baseline média 7d': {
            'tipo': 'baseline',
            'estimador': lambda: PreverColuna(coluna='media_7'),
            'grade': [{}],
        },
        'Ridge': {
            'tipo': 'linear',
            'estimador': lambda: Pipeline([
                ('escala', StandardScaler()),
                ('modelo', Ridge(random_state=semente)),
            ]),
            'grade': [{'modelo__alpha': a} for a in (1.0, 10.0, 100.0)],
        },
        'Random Forest': {
            'tipo': 'arvore',
            'estimador': lambda: RandomForestRegressor(random_state=semente, n_jobs=-1),
            'grade': [
                {'n_estimators': 300, 'min_samples_leaf': 5, 'max_features': 0.5, 'max_depth': 10},
                {'n_estimators': 300, 'min_samples_leaf': 5, 'max_features': 0.5, 'max_depth': 14},
                {'n_estimators': 300, 'min_samples_leaf': 10, 'max_features': 0.8, 'max_depth': 18},
            ],
        },
        'XGBoost': {
            'tipo': 'arvore',
            'estimador': lambda: XGBRegressor(
                random_state=semente, n_jobs=-1, tree_method='hist',
                objective='reg:squarederror',
            ),
            'grade': [
                {'n_estimators': 600, 'learning_rate': 0.05, 'max_depth': 5,
                 'subsample': 0.8, 'colsample_bytree': 0.8, 'reg_lambda': 1.0},
                {'n_estimators': 400, 'learning_rate': 0.08, 'max_depth': 4,
                 'subsample': 0.9, 'colsample_bytree': 0.7, 'reg_lambda': 2.0},
                {'n_estimators': 900, 'learning_rate': 0.03, 'max_depth': 6,
                 'subsample': 0.8, 'colsample_bytree': 0.6, 'reg_lambda': 3.0},
            ],
        },
        'LightGBM': {
            'tipo': 'arvore',
            'estimador': lambda: LGBMRegressor(random_state=semente, n_jobs=-1, verbose=-1),
            'grade': [
                {'n_estimators': 600, 'learning_rate': 0.05, 'num_leaves': 31,
                 'subsample': 0.8, 'subsample_freq': 1, 'colsample_bytree': 0.8},
                {'n_estimators': 400, 'learning_rate': 0.08, 'num_leaves': 15,
                 'min_child_samples': 40, 'colsample_bytree': 0.7},
            ],
        },
    }


def candidatos_classificacao(semente=config.SEMENTE, peso_positivo=1.0,
                             coluna_regra='taxa_violacao_media_28'):
    """Famílias candidatas para os modelos de risco (violação de OLA, pico operacional).

    `peso_positivo` carrega o desbalanceamento da classe: sem ele, um alvo com 1% de positivos
    é "resolvido" prevendo sempre a classe negativa.
    """
    return {
        'Baseline (taxa histórica)': {
            'tipo': 'baseline',
            'estimador': lambda: RegraProbabilidade(coluna=coluna_regra),
            'grade': [{}],
        },
        'Baseline (prevalência)': {
            'tipo': 'baseline',
            'estimador': lambda: DummyClassifier(strategy='prior', random_state=semente),
            'grade': [{}],
        },
        'Regressão logística': {
            'tipo': 'linear',
            'estimador': lambda: Pipeline([
                ('escala', StandardScaler()),
                ('modelo', LogisticRegression(
                    max_iter=2000, class_weight='balanced', random_state=semente,
                )),
            ]),
            'grade': [{'modelo__C': c} for c in (0.05, 0.5, 5.0)],
        },
        'Random Forest': {
            'tipo': 'arvore',
            'estimador': lambda: RandomForestClassifier(
                random_state=semente, n_jobs=-1, class_weight='balanced_subsample',
            ),
            'grade': [
                {'n_estimators': 300, 'min_samples_leaf': 20, 'max_features': 0.5, 'max_depth': 10},
                {'n_estimators': 300, 'min_samples_leaf': 20, 'max_features': 0.5, 'max_depth': 14},
                {'n_estimators': 300, 'min_samples_leaf': 10, 'max_features': 0.8, 'max_depth': 8},
            ],
        },
        'XGBoost': {
            'tipo': 'arvore',
            'estimador': lambda: XGBClassifier(
                random_state=semente, n_jobs=-1, tree_method='hist',
                objective='binary:logistic', eval_metric='aucpr',
                scale_pos_weight=peso_positivo,
            ),
            'grade': [
                {'n_estimators': 400, 'learning_rate': 0.05, 'max_depth': 4,
                 'subsample': 0.8, 'colsample_bytree': 0.8, 'reg_lambda': 2.0},
                {'n_estimators': 250, 'learning_rate': 0.08, 'max_depth': 3,
                 'subsample': 0.9, 'colsample_bytree': 0.7, 'reg_lambda': 5.0},
            ],
        },
        'LightGBM': {
            'tipo': 'arvore',
            'estimador': lambda: LGBMClassifier(
                random_state=semente, n_jobs=-1, verbose=-1, class_weight='balanced',
            ),
            'grade': [
                {'n_estimators': 400, 'learning_rate': 0.05, 'num_leaves': 15,
                 'min_child_samples': 30, 'colsample_bytree': 0.8},
                {'n_estimators': 250, 'learning_rate': 0.08, 'num_leaves': 7,
                 'min_child_samples': 60},
            ],
        },
    }


def peso_para_desbalanceamento(y):
    """`scale_pos_weight` = negativos/positivos, a receita padrão do XGBoost."""
    positivos = float(np.sum(y))
    negativos = float(len(y) - positivos)
    return negativos / positivos if positivos else 1.0


def nome_algoritmo(estimador):
    """Nome legível do algoritmo — é o que a tela de transparência exibe.

    Desembrulha Pipeline e calibrador: quem aprendeu foi a regressão logística, e é isso que a
    banca precisa ler. Exibir `CalibratedClassifierCV` esconderia o algoritmo de verdade atrás
    do invólucro que só reescala a probabilidade.
    """
    if isinstance(estimador, Pipeline):
        return nome_algoritmo(estimador.steps[-1][1])
    if type(estimador).__name__ in ('CalibratedClassifierCV', 'FrozenEstimator'):
        interno = getattr(estimador, 'estimator', None)
        if interno is not None:
            sufixo = ' (calibrado)' if type(estimador).__name__ == 'CalibratedClassifierCV' else ''
            return nome_algoritmo(interno) + sufixo
    return type(estimador).__name__
