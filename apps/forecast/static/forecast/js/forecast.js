// Forecast Engine — dados simulados até a integração com os modelos preditivos
document.addEventListener('DOMContentLoaded', () => {
    const { aleatorio } = window.SentinelOps;

    const dias = ['Qui', 'Sex', 'Sáb', 'Dom', 'Seg', 'Ter', 'Qua'];
    const previsoes = dias.map(() => aleatorio(20, 90));

    const d1 = document.querySelector('[data-forecast="d1"]');
    const d7 = document.querySelector('[data-forecast="d7"]');
    if (d1) d1.textContent = previsoes[0];
    if (d7) d7.textContent = previsoes.reduce((soma, valor) => soma + valor, 0);

    const chart = document.querySelector('[data-forecast-chart]');
    if (!chart) return;

    const maximo = Math.max(...previsoes);
    previsoes.forEach((valor, i) => {
        const bar = document.createElement('div');
        bar.className = 'forecast-chart__bar';
        bar.innerHTML = `
            <div class="forecast-chart__fill" style="height: 0%" title="${valor} incidentes"></div>
            <span class="forecast-chart__label">${dias[i]}</span>
        `;
        chart.appendChild(bar);
        requestAnimationFrame(() => {
            bar.querySelector('.forecast-chart__fill').style.height =
                `${Math.round((valor / maximo) * 100)}%`;
        });
    });
});
