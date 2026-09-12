/* Forecast Engine — leitura por dia no gráfico de previsão.
 *
 * Continua valendo a decisão do projeto: o gráfico é desenhado no servidor e chega pronto no
 * HTML. Este arquivo não calcula nem busca nada — lê atributos `data-*` que já estão na
 * página e move um cursor. Se o JS não carregar, o gráfico continua correto e legível; só
 * não responde ao ponteiro.
 *
 * A conversão de coordenadas é feita via getBoundingClientRect do próprio marcador, depois de
 * posicioná-lo em unidades do viewBox. Assim a legenda acompanha o SVG em qualquer largura,
 * sem replicar no cliente a matemática de escala que o servidor já fez.
 */
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-grafico]').forEach(montar);
});

function montar(wrap) {
    const svg = wrap.querySelector('svg');
    const cursor = wrap.querySelector('.graf__cursor');
    const linha = cursor && cursor.querySelector('line');
    const ponto = cursor && cursor.querySelector('circle');
    const tip = wrap.querySelector('.graf-tip');
    if (!svg || !cursor || !tip) return;

    const alvos = svg.querySelectorAll('.graf__alvo');
    if (!alvos.length) return;

    alvos.forEach((alvo) => {
        alvo.addEventListener('pointerenter', () => destacar(alvo));
        alvo.addEventListener('pointerdown', () => destacar(alvo));
    });
    svg.addEventListener('pointerleave', esconder);

    function destacar(alvo) {
        const d = alvo.dataset;
        const previsto = d.tipo === 'previsao';
        const cor = previsto ? 'var(--color-primary)' : 'var(--sev-neutral)';

        linha.setAttribute('x1', d.x);
        linha.setAttribute('x2', d.x);
        ponto.setAttribute('cx', d.x);
        ponto.setAttribute('cy', d.y);
        ponto.style.stroke = d.pico ? 'var(--sev-critical)' : cor;
        cursor.hidden = false;

        const linhas = [`<span class="graf-tip__dia">${d.data}</span>`];
        if (previsto) {
            linhas.push(
                `<div class="graf-tip__linha">esperado <strong>${d.valor}</strong> incidentes</div>`,
                `<div class="graf-tip__linha">faixa provável <strong>${d.min}–${d.max}</strong></div>`
            );
            if (d.pico) {
                linhas.push('<div class="graf-tip__linha">chance alta de pico neste dia</div>');
            }
        } else {
            linhas.push(`<div class="graf-tip__linha">registrados <strong>${d.valor}</strong> incidentes</div>`);
        }
        tip.innerHTML = linhas.join('');
        tip.hidden = false;
        posicionar();
    }

    function posicionar() {
        const caixa = ponto.getBoundingClientRect();
        const area = wrap.getBoundingClientRect();
        const centro = caixa.left + caixa.width / 2 - area.left;
        const metade = tip.offsetWidth / 2;
        // Mantém a legenda dentro do card: nos dias das pontas ela encostaria fora da tela.
        const limitado = Math.min(Math.max(centro, metade + 2), area.width - metade - 2);
        tip.style.left = `${limitado}px`;
        tip.style.top = `${caixa.top - area.top - 10}px`;
    }

    function esconder() {
        cursor.hidden = true;
        tip.hidden = true;
    }
}
