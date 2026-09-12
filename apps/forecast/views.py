from django.shortcuts import render

from apps.intelligence.services.base import DIM
from apps.ml.services import catalogo, inferencia

from . import services


def index(request):
    previsao = services.resumo()
    return render(request, 'forecast/index.html', {
        'previsao': previsao,
        'historico': services.historico() if previsao else [],
        'interpretacao': services.interpretar(previsao),
        'picos': services.picos(limite=5) if previsao else [],
        'modelo_pico': catalogo.modelo('model_pico'),
        'por_prioridade': services.por_dimensao('prioridade', limite=5) if previsao else [],
        # Produto, equipe e ativo respondem a mesma pergunta ("onde"), com a mesma tabela — daí
        # virem de um laço só no template em vez de três blocos copiados.
        'blocos_dimensao': [
            {
                'titulo': 'produto', 'coluna': 'Produto',
                'nota': 'onde o volume deve cair',
                'linhas': services.por_dimensao('produto', limite=8) if previsao else [],
            },
            {
                'titulo': 'equipe', 'coluna': 'Equipe',
                'nota': 'quem vai receber a carga',
                'linhas': services.por_dimensao('equipe', limite=6) if previsao else [],
            },
            {
                'titulo': 'item de configuração', 'coluna': 'Ativo',
                'nota': 'apenas os ativos com série densa o bastante para prever',
                'linhas': services.por_dimensao('item', limite=6) if previsao else [],
            },
        ],
        'risco_ola': inferencia.risco_ola_global(),
        'calendario': services.calendario_risco(dias=21) if previsao else [],
        'tendencias': services.tendencia_por_dimensao(DIM.FAMILIA),
    })


def model_performance(request):
    """Desempenho dos modelos de previsão de volume, lado a lado com as baselines."""
    cartoes = [c for c in catalogo.modelos() if c['tipo'] == 'regressao']

    for cartao in cartoes:
        teste = cartao['metricas'].get('teste', {})
        serie_global = (cartao['metricas'].get('teste_por_dimensao') or {}).get('global', {})
        # A ressalva sobre a virada de ano precisa aparecer sempre que o modelo perder em alguma
        # das leituras — no agregado ou só na série global. Esconder porque o número médio ficou
        # bom seria o tipo de silêncio conveniente que a tela existe para evitar.
        cartao['perde_da_baseline'] = (
            (teste.get('mase') or 0) >= 1
            or (serie_global.get('ganho_vs_baseline') or 0) < 0
        )
        cartao['global'] = serie_global

    return render(request, 'forecast/model_performance.html', {
        'cartoes': cartoes,
        'todos': catalogo.modelos(),
        'execucao': catalogo.execucao(),
        'glossario': catalogo.glossario(),
    })
