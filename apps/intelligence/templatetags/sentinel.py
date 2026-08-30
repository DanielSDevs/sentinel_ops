"""Componentes visuais gerados no servidor.

Os gráficos são SVG montados aqui, sem biblioteca externa: mantém a página leve, funciona sem
JavaScript e evita a dependência de CDN (que o projeto não usa em nenhum outro lugar).
"""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

CORES_SEVERIDADE = {
    'healthy': 'var(--sev-healthy)',
    'attention': 'var(--sev-attention)',
    'warning': 'var(--sev-warning)',
    'critical': 'var(--sev-critical)',
    'neutral': 'var(--sev-neutral)',
}


@register.filter
def cor_severidade(faixa):
    return CORES_SEVERIDADE.get(faixa, CORES_SEVERIDADE['neutral'])


@register.filter
def classe_variacao(valor):
    """Classe de cor para uma variação onde subir é ruim."""
    if valor is None:
        return 'sev-neutral'
    if valor > 2:
        return 'sev-critical'
    if valor < -2:
        return 'sev-healthy'
    return 'sev-neutral'


@register.simple_tag
def dial(score, faixa, tamanho=150):
    """Anel de progresso do Operational Health Score."""
    raio = tamanho / 2 - 12
    circunferencia = 2 * 3.14159 * raio
    preenchido = circunferencia * (max(0, min(100, score)) / 100)
    centro = tamanho / 2
    cor = CORES_SEVERIDADE.get(faixa, CORES_SEVERIDADE['neutral'])

    return mark_safe(f'''
<svg class="dial__svg" width="{tamanho}" height="{tamanho}" viewBox="0 0 {tamanho} {tamanho}"
     role="img" aria-label="Operational Health Score: {score} de 100">
  <circle class="dial__track" cx="{centro}" cy="{centro}" r="{raio}" fill="none" stroke-width="10"/>
  <circle class="dial__value" cx="{centro}" cy="{centro}" r="{raio}" fill="none" stroke-width="10"
          stroke="{cor}" stroke-dasharray="{circunferencia:.1f}"
          stroke-dashoffset="{circunferencia - preenchido:.1f}"
          transform="rotate(-90 {centro} {centro})"/>
  <text class="dial__num" x="{centro}" y="{centro - 6}">{score}</text>
  <text class="dial__den" x="{centro}" y="{centro + 22}">/ 100</text>
</svg>''')


@register.simple_tag
def sparkline(valores, cor='var(--sev-neutral)', largura=120, altura=34):
    """Minigráfico de tendência — mostra a forma da série, não valores exatos."""
    valores = [float(v) for v in (valores or [])]
    if len(valores) < 2:
        return mark_safe('<span class="mono sev-neutral" style="font-size:.72rem">sem série</span>')

    minimo, maximo = min(valores), max(valores)
    amplitude = (maximo - minimo) or 1
    passo = largura / (len(valores) - 1)

    pontos = [
        (i * passo, altura - 3 - ((v - minimo) / amplitude) * (altura - 6))
        for i, v in enumerate(valores)
    ]
    linha = ' '.join(f'{x:.1f},{y:.1f}' for x, y in pontos)
    area = f'0,{altura} ' + linha + f' {largura},{altura}'
    ultimo_x, ultimo_y = pontos[-1]

    return mark_safe(f'''
<svg class="spark" width="{largura}" height="{altura}" viewBox="0 0 {largura} {altura}" aria-hidden="true">
  <polygon class="spark__area" points="{area}" fill="{cor}"/>
  <polyline class="spark__linha" points="{linha}" stroke="{cor}"/>
  <circle class="spark__ponto" cx="{ultimo_x:.1f}" cy="{ultimo_y:.1f}" fill="{cor}"/>
</svg>''')


@register.simple_tag
def barra_contribuicao(pontos_perdidos, peso):
    """Quanto do peso máximo de um fator foi de fato perdido."""
    proporcao = (pontos_perdidos / peso) if peso else 0
    if proporcao >= 0.66:
        cor = CORES_SEVERIDADE['critical']
    elif proporcao >= 0.33:
        cor = CORES_SEVERIDADE['warning']
    elif proporcao > 0:
        cor = CORES_SEVERIDADE['attention']
    else:
        cor = CORES_SEVERIDADE['healthy']
    return mark_safe(
        f'<div class="contrib__bar"><div class="contrib__fill" '
        f'style="width:{min(100, proporcao * 100):.0f}%;background:{cor}"></div></div>'
    )


@register.simple_tag
def grafo_blast(tempestade, largura=440, altura=190):
    """Blast radius: nó raiz e as famílias de sinal que se desdobraram dele."""
    familias = tempestade.familias[:5]
    if not familias:
        return ''

    centro_x, centro_y = 78, altura / 2
    partes = [
        f'<svg viewBox="0 0 {largura} {altura}" width="100%" height="{altura}" '
        f'role="img" aria-label="Desdobramentos do incidente {tempestade.pai.numero}">'
    ]

    espaco = altura / (len(familias) + 1)
    maximo = max(q for _, q in familias) or 1

    for i, (nome, quantidade) in enumerate(familias):
        y = espaco * (i + 1)
        x = largura - 150
        espessura = 1 + (quantidade / maximo) * 4
        partes.append(
            f'<path class="grafo__aresta" d="M{centro_x + 34} {centro_y} '
            f'C{centro_x + 110} {centro_y}, {x - 60} {y}, {x - 8} {y}" '
            f'fill="none" stroke-width="{espessura:.1f}"/>'
        )
        partes.append(f'<rect class="grafo__no" x="{x - 4}" y="{y - 13}" width="140" height="26" rx="13"/>')
        partes.append(f'<text class="grafo__rotulo" x="{x + 66}" y="{y + 1}">{nome[:16]}</text>')
        partes.append(f'<text class="grafo__peso" x="{x + 66}" y="{y + 12}">{quantidade} incidentes</text>')

    partes.append(f'<circle class="grafo__no-raiz" cx="{centro_x}" cy="{centro_y}" r="34"/>')
    partes.append(f'<text class="grafo__rotulo" x="{centro_x}" y="{centro_y - 3}">{tempestade.pai.numero[:10]}</text>')
    partes.append(
        f'<text class="grafo__peso" x="{centro_x}" y="{centro_y + 11}">{tempestade.total_filhos} filhos</text>'
    )
    partes.append('</svg>')
    return mark_safe(''.join(partes))


@register.simple_tag
def timeline(pontos, altura=46):
    """Linha do tempo de estados — altura normalizada pelo maior valor da própria janela."""
    pontos = list(pontos or [])
    if not pontos:
        return mark_safe('<p class="vazio">Sem série no período.</p>')

    maximo = max((p.valor for p in pontos), default=0) or 1
    barras = []
    for ponto in pontos:
        proporcao = ponto.valor / maximo
        px = max(4, round(proporcao * altura))
        barras.append(
            f'<div class="timeline__barra timeline__barra--{ponto.estado}" '
            f'style="height:{px}px" '
            f'title="{ponto.data:%d/%m} · {ponto.valor} incidentes · z={ponto.z}"></div>'
        )

    return mark_safe(
        f'<div class="timeline" style="height:{altura}px">{"".join(barras)}</div>'
        f'<div class="timeline__legenda"><span>{pontos[0].data:%d/%m}</span>'
        f'<span>pico {maximo}</span><span>{pontos[-1].data:%d/%m}</span></div>'
    )


@register.filter
def indice(sequencia, posicao):
    try:
        return sequencia[posicao]
    except (IndexError, KeyError, TypeError):
        return None
