"""Model registry em arquivo: o artefato treinado e o cartão do modelo, lado a lado.

Cada modelo gera dois arquivos em `ml/models/`:

- `<nome>.pkl`  — o estimador treinado (joblib), carregado só na hora de prever;
- `<nome>.json` — o cartão do modelo: algoritmo, features, partições, métricas, comparação com
  as outras famílias, importâncias e data do treino.

A separação existe porque a tela de transparência do Django precisa ler métricas sem
desserializar modelo nenhum — e porque o JSON é legível e versionável em diff, enquanto o .pkl
é um binário opaco. Se o .pkl sumir, a plataforma sabe dizer "modelo não treinado" em vez de
mostrar número velho.
"""

import json
from datetime import datetime, timezone

import joblib

from . import config

VERSAO_FORMATO = 1


def caminho_artefato(nome):
    return config.MODELOS_DIR / f'{nome}.pkl'


def caminho_cartao(nome):
    return config.MODELOS_DIR / f'{nome}.json'


def _serializavel(valor):
    """JSON não aceita Timestamp, numpy nem Path — converte antes de gravar."""
    if isinstance(valor, dict):
        return {str(k): _serializavel(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_serializavel(v) for v in valor]
    if hasattr(valor, 'item') and getattr(valor, 'size', 1) == 1:
        try:
            return valor.item()
        except (ValueError, AttributeError):
            pass
    if hasattr(valor, 'isoformat'):
        return valor.isoformat()
    if isinstance(valor, (str, int, float, bool)) or valor is None:
        return valor
    return str(valor)


def salvar(nome, modelo, metadados, extras=None):
    """Grava artefato + cartão. `extras` vai só para o .pkl (limiares, explicador, encoders)."""
    config.garantir_diretorios()
    cartao = {
        'nome': nome,
        'versao_formato': VERSAO_FORMATO,
        'treinado_em': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        **metadados,
    }
    # Compressão em nível 3: ensembles de árvore têm muita estrutura repetida e encolhem bem,
    # e o artefato precisa caber no versionamento e no pacote de deploy.
    joblib.dump(
        {'modelo': modelo, 'cartao': cartao, **(extras or {})},
        caminho_artefato(nome), compress=3,
    )
    caminho_cartao(nome).write_text(
        json.dumps(_serializavel(cartao), ensure_ascii=False, indent=2), encoding='utf-8',
    )
    return cartao


def carregar(nome):
    """Artefato completo (modelo + cartão + extras). Levanta se o modelo não foi treinado."""
    caminho = caminho_artefato(nome)
    if not caminho.exists():
        raise FileNotFoundError(
            f'Modelo "{nome}" não treinado: {caminho} não existe. '
            f'Rode `python manage.py treinar_modelos`.'
        )
    return joblib.load(caminho)


def existe(nome):
    return caminho_artefato(nome).exists()


def cartao(nome):
    """Só os metadados, sem carregar o estimador."""
    caminho = caminho_cartao(nome)
    if not caminho.exists():
        return None
    return json.loads(caminho.read_text(encoding='utf-8'))


def listar():
    """Cartões de todos os modelos treinados, em ordem de exibição."""
    if not config.MODELOS_DIR.exists():
        return []
    cartoes = []
    for caminho in sorted(config.MODELOS_DIR.glob('*.json')):
        if caminho.name == 'registry.json':
            continue
        cartoes.append(json.loads(caminho.read_text(encoding='utf-8')))
    return sorted(cartoes, key=lambda c: c.get('ordem', 99))


def salvar_indice(resumo):
    """`registry.json`: um resumo da execução de treino inteira (o que rodou, quando, com o quê)."""
    config.garantir_diretorios()
    caminho = config.MODELOS_DIR / 'registry.json'
    caminho.write_text(
        json.dumps(_serializavel(resumo), ensure_ascii=False, indent=2), encoding='utf-8',
    )
    return caminho


def indice():
    caminho = config.MODELOS_DIR / 'registry.json'
    if not caminho.exists():
        return None
    return json.loads(caminho.read_text(encoding='utf-8'))
