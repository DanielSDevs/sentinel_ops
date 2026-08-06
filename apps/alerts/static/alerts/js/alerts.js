// Risk Alert Center — alertas simulados até a integração com Celery/Redis
document.addEventListener('DOMContentLoaded', () => {
    const { formatarData } = window.SentinelOps;
    const lista = document.querySelector('[data-alert-list]');
    if (!lista) return;

    const alertas = [
        { titulo: 'Aumento anômalo de incidentes previsto para amanhã', origem: 'Forecast Engine', nivel: 'Alto', classe: 'badge--danger' },
        { titulo: 'Latência elevada no serviço de autenticação', origem: 'Ops Monitor', nivel: 'Médio', classe: 'badge--warning' },
        { titulo: 'SLA próximo do limite na fila P2', origem: 'Ops Monitor', nivel: 'Médio', classe: 'badge--warning' },
        { titulo: 'Pipeline de ingestão concluído com atraso', origem: 'Data Pipeline', nivel: 'Baixo', classe: 'badge--info' },
    ];

    lista.innerHTML = alertas.map((alerta) => `
        <li class="alert-item">
            <div class="alert-item__info">
                <span class="alert-item__title">${alerta.titulo}</span>
                <span class="alert-item__meta">${alerta.origem} &middot; ${formatarData(new Date())}</span>
            </div>
            <span class="badge ${alerta.classe}">${alerta.nivel}</span>
        </li>
    `).join('');
});
