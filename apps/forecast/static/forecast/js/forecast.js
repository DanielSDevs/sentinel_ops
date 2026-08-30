// Forecast Engine — os valores vêm do servidor (apps/forecast/services.py); aqui só animamos o preenchimento das barras
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.forecast-chart__fill').forEach((fill) => {
        const altura = fill.dataset.altura || 0;
        requestAnimationFrame(() => {
            fill.style.height = `${altura}%`;
        });
    });
});
