"""Catálogo de modelos: o que a tela de transparência lê.

Tudo aqui vem dos cartões gravados pelo treino (`ml/models/*.json`). Não existe métrica montada
nesta camada — se o cartão não traz o número, a tela mostra "—". É o que sustenta a promessa de
que o que a banca vê na tela é o que o `.ipynb` produziu.
"""

from pathlib import Path

from django.conf import settings

from ml.src import metrics, registry

# Métrica de capa de cada tipo de modelo, com o rótulo que faz sentido para quem lê.
ROTULOS = {
    'mae': 'MAE', 'rmse': 'RMSE', 'mape': 'MAPE', 'r2': 'R²', 'mase': 'MASE',
    'pr_auc': 'PR-AUC', 'roc_auc': 'ROC-AUC', 'f1': 'F1', 'precision': 'Precisão',
    'recall': 'Recall', 'accuracy': 'Acurácia', 'brier': 'Brier',
    'silhueta': 'Silhueta', 'concordancia_zscore': 'Concordância com z-score',
}

TIPOS = {
    'regressao': 'Regressão',
    'classificacao': 'Classificação',
    'anomalia': 'Detecção de anomalias',
    'clusterizacao': 'Clusterização',
}


def _valor_principal(cartao):
    """Valor da métrica de capa, preferindo sempre o teste (a partição nunca usada na escolha)."""
    chave = cartao.get('metrica_principal')
    metricas = cartao.get('metricas', {})
    for particao in ('teste', 'validacao'):
        bloco = metricas.get(particao)
        if isinstance(bloco, dict) and bloco.get(chave) is not None:
            return bloco[chave], particao
    if metricas.get(chave) is not None:
        return metricas[chave], 'treino'
    return None, None


def _notebook(cartao):
    caminho = cartao.get('notebook')
    if not caminho:
        return None
    existe = (Path(settings.BASE_DIR) / caminho).exists()
    return {'caminho': caminho, 'existe': existe, 'nome': Path(caminho).name}


def enriquecer(cartao):
    """Cartão + o que a tela precisa saber além dele (artefato presente, rótulos, notebook)."""
    valor, particao = _valor_principal(cartao)
    return {
        **cartao,
        'tipo_legivel': TIPOS.get(cartao.get('tipo'), cartao.get('tipo')),
        'metrica_rotulo': ROTULOS.get(cartao.get('metrica_principal'), cartao.get('metrica_principal')),
        'metrica_valor': valor,
        'metrica_particao': particao,
        'artefato_existe': registry.existe(cartao['nome']),
        'notebook': _notebook(cartao),
        'glossario': cartao.get('glossario') or {},
        # Chaves sempre presentes: modelo não supervisionado não tem comparação nem importância,
        # e o template não deveria precisar saber disso para não quebrar.
        'importancias': cartao.get('importancias') or [],
        'importancia_permutacao': cartao.get('importancia_permutacao') or [],
        'comparacao': cartao.get('comparacao') or [],
        'observacoes': cartao.get('observacoes') or [],
    }


def modelos():
    """Todos os modelos treinados, na ordem de exibição definida no treino."""
    return [enriquecer(cartao) for cartao in registry.listar()]


def modelo(nome):
    cartao = registry.cartao(nome)
    return enriquecer(cartao) if cartao else None


def execucao():
    """Resumo da última execução do pipeline (`ml/models/registry.json`)."""
    return registry.indice()


def treinado():
    return bool(registry.listar())


def rotulo_metrica(chave):
    return ROTULOS.get(chave, chave)


def glossario():
    return metrics.GLOSSARIO


def tabela_resumo():
    """A tabela pedida pela banca: modelo → objetivo → algoritmo → métrica → resultado."""
    linhas = []
    for cartao in modelos():
        linhas.append({
            'nome': cartao['nome'],
            'titulo': cartao['titulo'],
            'objetivo': cartao['objetivo'],
            'algoritmo': cartao['algoritmo'],
            'tipo': cartao['tipo_legivel'],
            'metrica': cartao['metrica_rotulo'],
            'valor': cartao['metrica_valor'],
            'particao': cartao['metrica_particao'],
            'ganho': cartao.get('ganho_vs_baseline'),
            'notebook': cartao['notebook'],
            'treinado_em': cartao.get('treinado_em'),
            'disponivel': cartao['artefato_existe'],
        })
    return linhas
