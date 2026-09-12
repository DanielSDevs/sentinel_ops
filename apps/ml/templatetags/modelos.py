"""Gráficos SVG dos modelos de ML, no mesmo estilo do resto da plataforma.

Segue a decisão que já vale no projeto: SVG montado no servidor, sem biblioteca de charts no
cliente. O que muda aqui é o que precisa ser desenhado — histórico e previsão na mesma escala,
faixa de incerteza como área, e contribuição SHAP como barra com sinal (para a esquerda quando
a feature empurra a previsão para baixo).
"""

from django import template
from django.utils.safestring import mark_safe

from apps.ml.services import inferencia

register = template.Library()

CORES = {
    'historico': 'var(--sev-neutral)',
    'previsao': 'var(--color-primary)',
    'pico': 'var(--sev-critical)',
    'positivo': 'var(--sev-critical)',
    'negativo': 'var(--sev-healthy)',
}


@register.simple_tag
def grafico_previsao(historico, previsoes, largura=760, altura=250):
    """Histórico observado, previsão e faixa provável, num eixo só — com leitura por dia.

    Três decisões que valem explicar:

    **Escala única para as duas séries.** Previsão desenhada em eixo próprio parece sempre
    dramática. Aqui o operador vê se o que o modelo projeta cabe no que já aconteceu.

    **Sem `preserveAspectRatio="none"`.** A versão anterior usava, e o SVG era esticado na
    horizontal para preencher a largura do card: o ângulo das linhas mentia sobre a inclinação
    real e os rótulos saíam achatados. Com o padrão (`xMidYMid meet`) a escala é uniforme.

    **Interação sem biblioteca.** Cada dia ganha uma faixa invisível de captura; o resto é um
    arquivo de 60 linhas em `forecast/js/grafico.js` que move um cursor e preenche a legenda
    flutuante. Continua não havendo chart library no cliente — o desenho vem pronto do
    servidor, e o JS só lê atributos que já estão no HTML.
    """
    historico = list(historico or [])
    previsoes = list(previsoes or [])
    if not historico or not previsoes:
        return ''

    valores = [v for _, v in historico]
    valores += [p['maximo'] for p in previsoes] + [p['minimo'] for p in previsoes]
    maximo = max(valores) or 1
    minimo = 0

    margem_esq, margem_dir, margem_topo, margem_base = 36, 10, 18, 28
    area_largura = largura - margem_esq - margem_dir
    area_altura = altura - margem_topo - margem_base
    total = len(historico) + len(previsoes)
    passo = area_largura / max(total - 1, 1)

    def x(indice):
        return margem_esq + indice * passo

    def y(valor):
        proporcao = (valor - minimo) / (maximo - minimo) if maximo > minimo else 0
        return margem_topo + area_altura - proporcao * area_altura

    pontos_historico = ' '.join(
        f'{x(i):.1f},{y(v):.1f}' for i, (_, v) in enumerate(historico)
    )
    deslocamento = len(historico) - 1
    pontos_previsao = [(x(deslocamento + 1 + i), y(p['valor'])) for i, p in enumerate(previsoes)]
    # A previsão começa no último ponto real: sem isso a linha aparece flutuando no vazio.
    ligacao = f'{x(deslocamento):.1f},{y(historico[-1][1]):.1f} ' + ' '.join(
        f'{px:.1f},{py:.1f}' for px, py in pontos_previsao
    )

    topo = [(x(deslocamento + 1 + i), y(p['maximo'])) for i, p in enumerate(previsoes)]
    base = [(x(deslocamento + 1 + i), y(p['minimo'])) for i, p in enumerate(previsoes)]
    faixa = ' '.join(f'{px:.1f},{py:.1f}' for px, py in topo + list(reversed(base)))

    marcadores = ''.join(
        f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="{CORES["pico"]}"/>'
        for (px, py), p in zip(pontos_previsao, previsoes) if p.get('pico')
    )

    # Eixo de datas dos dois lados do corte. A versão anterior rotulava só a previsão, então
    # metade do gráfico não tinha referência temporal nenhuma.
    passo_rotulo = max(1, round(total / 9))
    dias = [(i, d, 'observado') for i, (d, _) in enumerate(historico)]
    dias += [(deslocamento + 1 + i, p['data'], 'previsao') for i, p in enumerate(previsoes)]
    rotulos = ''.join(
        f'<text class="graf__rotulo" x="{x(i):.1f}" y="{altura - 8:.0f}" '
        f'text-anchor="middle">{d:%d/%m}</text>'
        for i, d, _ in dias if i % passo_rotulo == 0 or i == total - 1
    )

    escala = ''.join(
        f'<text class="graf__eixo" x="{margem_esq - 7}" y="{y(valor) + 3:.1f}" '
        f'text-anchor="end">{valor:.0f}</text>'
        f'<line class="graf__grade" x1="{margem_esq}" y1="{y(valor):.1f}" '
        f'x2="{largura - margem_dir}" y2="{y(valor):.1f}"/>'
        for valor in (0, maximo / 2, maximo)
    )

    separador_x = x(deslocamento)
    corte = (
        f'<line class="graf__corte" x1="{separador_x:.1f}" y1="{margem_topo}" '
        f'x2="{separador_x:.1f}" y2="{margem_topo + area_altura}"/>'
        f'<text class="graf__marco" x="{separador_x + 4:.1f}" y="{margem_topo + 9}">'
        f'daqui para frente é previsão</text>'
    )

    # Uma faixa de captura por dia: o cursor acompanha o dia mais próximo do ponteiro em vez
    # de exigir mira sobre a linha, que num gráfico de 250px de altura é impossível.
    alvos = []
    for indice, data, tipo in dias:
        if tipo == 'observado':
            valor = historico[indice][1]
            extra = ''
        else:
            p = previsoes[indice - deslocamento - 1]
            valor = p['valor']
            extra = (f' data-min="{p["minimo"]}" data-max="{p["maximo"]}"'
                     f' data-pico="{"1" if p.get("pico") else ""}"')
        alvos.append(
            f'<rect class="graf__alvo" x="{x(indice) - passo / 2:.1f}" y="{margem_topo}" '
            f'width="{passo:.1f}" height="{area_altura:.1f}" '
            f'data-x="{x(indice):.1f}" data-y="{y(valor):.1f}" '
            f'data-data="{data:%d/%m}" data-valor="{valor}" data-tipo="{tipo}"{extra}/>'
        )

    cursor = (
        f'<g class="graf__cursor" hidden>'
        f'<line y1="{margem_topo}" y2="{margem_topo + area_altura:.1f}"/>'
        f'<circle r="4"/></g>'
    )

    return mark_safe(f'''
<div class="graf-wrap" data-grafico>
  <svg class="graf" width="100%" viewBox="0 0 {largura} {altura}"
       role="img" aria-label="Histórico e previsão de volume de incidentes">
    {escala}
    <polygon class="graf__faixa" points="{faixa}" fill="{CORES['previsao']}"/>
    <polyline class="graf__linha" points="{pontos_historico}" stroke="{CORES['historico']}"/>
    {corte}
    <polyline class="graf__linha graf__linha--previsao" points="{ligacao}"
              stroke="{CORES['previsao']}"/>
    {marcadores}
    {rotulos}
    {cursor}
    <g class="graf__alvos">{''.join(alvos)}</g>
  </svg>
  <div class="graf-tip" hidden aria-live="polite"></div>
</div>''')


@register.simple_tag
def barra_shap(contribuicao, maximo):
    """Barra com sinal: direita quando a feature empurra a previsão para cima, esquerda quando
    a puxa para baixo. O zero fica no centro, então o sentido é lido antes do número."""
    maximo = abs(maximo) or 1
    proporcao = min(abs(contribuicao) / maximo, 1.0) * 50
    cor = CORES['positivo'] if contribuicao > 0 else CORES['negativo']
    if contribuicao > 0:
        estilo = f'left:50%;width:{proporcao:.1f}%'
    else:
        estilo = f'right:50%;width:{proporcao:.1f}%'
    return mark_safe(
        f'<div class="shap__bar"><div class="shap__zero"></div>'
        f'<div class="shap__fill" style="{estilo};background:{cor}"></div></div>'
    )


@register.simple_tag
def barra_metrica(valor, maximo, cor='var(--color-primary)'):
    """Barra simples de proporção, usada nas tabelas de importância de features."""
    proporcao = min((valor / maximo) if maximo else 0, 1.0) * 100
    return mark_safe(
        f'<div class="contrib__bar"><div class="contrib__fill" '
        f'style="width:{proporcao:.1f}%;background:{cor}"></div></div>'
    )


@register.filter
def rotulo_chance(probabilidade):
    """Traduz probabilidade em faixa lida pela operação — a tela mostra a palavra, não o número."""
    if probabilidade is None:
        return '—'
    if probabilidade >= inferencia.FAIXA_ALTO:
        return 'ALTA'
    if probabilidade >= inferencia.FAIXA_MEDIO:
        return 'MÉDIA'
    return 'BAIXA'


@register.filter
def classe_chance(probabilidade):
    if probabilidade is None:
        return 'neutral'
    if probabilidade >= inferencia.FAIXA_ALTO:
        return 'critical'
    if probabilidade >= inferencia.FAIXA_MEDIO:
        return 'warning'
    return 'healthy'


@register.filter
def faixa_risco(faixa):
    return {'alto': 'critical', 'medio': 'warning', 'baixo': 'healthy'}.get(faixa, 'neutral')


@register.filter
def porcentagem(valor, casas=1):
    if valor is None:
        return '—'
    return f'{valor * 100:.{casas}f}%'


@register.filter
def chave_de(dicionario, chave):
    """Acesso por chave em dicionário dentro do template (o Django não tem sintaxe para isso)."""
    if not isinstance(dicionario, dict):
        return None
    return dicionario.get(chave) or dicionario.get(str(chave))


@register.filter
def sinal(valor):
    if valor is None:
        return ''
    return '+' if valor > 0 else ''
