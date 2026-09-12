"""Telas de transparência dos modelos: o catálogo, o cartão de cada modelo e o histórico."""

from django.http import Http404
from django.shortcuts import render

from apps.ml.models import PrevisaoRegistrada
from apps.ml.services import catalogo, inferencia


def modelos(request):
    """"Modelos de Machine Learning": o que foi treinado, com quê, e como se saiu."""
    cartoes = catalogo.modelos()
    return render(request, 'ml/modelos.html', {
        'tabela': catalogo.tabela_resumo(),
        'cartoes': cartoes,
        'execucao': catalogo.execucao(),
        'glossario': catalogo.glossario(),
        'clusters': inferencia.clusters(),
    })


def detalhe(request, nome):
    """Cartão completo de um modelo: comparação, partições, métricas e importâncias."""
    cartao = catalogo.modelo(nome)
    if not cartao:
        raise Http404(f'Modelo "{nome}" não encontrado.')

    comparacao = cartao.get('comparacao') or []
    metricas_comparadas = sorted({
        chave for linha in comparacao for chave in (linha.get('metricas') or {})
    }, key=lambda c: ('mae', 'rmse', 'mase', 'r2', 'pr_auc', 'roc_auc', 'f1', 'recall').index(c)
        if c in ('mae', 'rmse', 'mase', 'r2', 'pr_auc', 'roc_auc', 'f1', 'recall') else 99)

    importancias = cartao.get('importancias') or []
    permutacao = cartao.get('importancia_permutacao') or []

    return render(request, 'ml/detalhe.html', {
        'cartao': cartao,
        'comparacao': comparacao,
        'metricas_comparadas': metricas_comparadas,
        'importancias': importancias[:12],
        'maximo_importancia': max((i['importancia'] for i in importancias[:12]), default=1),
        'permutacao': permutacao[:12],
        'maximo_permutacao': max((i['queda_metrica'] for i in permutacao[:12]), default=1),
        'rotulo': catalogo.rotulo_metrica,
    })


def previsoes(request):
    """Histórico previsão × real: o monitoramento contínuo que a métrica de treino não dá."""
    modelo = request.GET.get('modelo') or ''
    consulta = PrevisaoRegistrada.objects.all()
    if modelo:
        consulta = consulta.filter(modelo=modelo)

    registros = list(consulta[:120])
    avaliados = [r for r in registros if r.valor_real is not None]

    erros = [r.erro_absoluto for r in avaliados]
    dentro = [r.dentro_da_faixa for r in avaliados if r.dentro_da_faixa is not None]
    maximo = max(
        [r.valor_previsto for r in registros] + [r.valor_real or 0 for r in registros] + [1],
    )

    return render(request, 'ml/previsoes.html', {
        'registros': registros,
        'modelo_filtrado': modelo,
        'modelos_disponiveis': sorted(
            PrevisaoRegistrada.objects.values_list('modelo', flat=True).distinct(),
        ),
        'resumo': {
            'avaliadas': len(avaliados),
            'pendentes': len(registros) - len(avaliados),
            'mae': round(sum(erros) / len(erros), 2) if erros else None,
            'dentro_da_faixa': (
                round(sum(1 for d in dentro if d) / len(dentro) * 100, 1) if dentro else None
            ),
        },
        'maximo': maximo,
    })
