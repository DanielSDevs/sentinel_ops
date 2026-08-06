// Reporting Hub — relatórios simulados até a geração real via Celery
document.addEventListener('DOMContentLoaded', () => {
    const tbody = document.querySelector('[data-reports-table]');
    if (!tbody) return;

    const relatorios = [
        { nome: 'Report Diário de Operações', periodo: 'Hoje', status: 'Disponível', classe: 'badge--success' },
        { nome: 'Report Semanal Consolidado', periodo: 'Últimos 7 dias', status: 'Disponível', classe: 'badge--success' },
        { nome: 'Consolidação Executiva Mensal', periodo: 'Mês atual', status: 'Em geração', classe: 'badge--warning' },
    ];

    tbody.innerHTML = relatorios.map((relatorio) => `
        <tr>
            <td>${relatorio.nome}</td>
            <td>${relatorio.periodo}</td>
            <td><span class="badge ${relatorio.classe}">${relatorio.status}</span></td>
            <td><button type="button" class="btn btn--outline btn--sm" disabled title="Disponível em sprints futuras">Baixar</button></td>
        </tr>
    `).join('');
});
