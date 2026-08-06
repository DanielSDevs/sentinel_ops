// Ops Monitor — dados simulados até a integração com Prometheus/Grafana
document.addEventListener('DOMContentLoaded', () => {
    const { aleatorio, formatarData } = window.SentinelOps;

    function atualizarMetricas() {
        const metricas = {
            abertos: aleatorio(12, 60),
            criticos: aleatorio(0, 8),
            sla: `${aleatorio(90, 99)}%`,
        };
        Object.entries(metricas).forEach(([chave, valor]) => {
            const el = document.querySelector(`[data-metric="${chave}"]`);
            if (el) el.textContent = valor;
        });
    }

    function atualizarServicos() {
        const tbody = document.querySelector('[data-services-table]');
        if (!tbody) return;

        const servicos = ['API Gateway', 'Banco de Dados', 'Fila de Mensagens', 'Autenticação', 'Data Pipeline'];
        const status = [
            { rotulo: 'Operacional', classe: 'badge--success' },
            { rotulo: 'Degradado', classe: 'badge--warning' },
            { rotulo: 'Indisponível', classe: 'badge--danger' },
        ];

        tbody.innerHTML = servicos.map((servico) => {
            // Maior probabilidade de status saudável
            const s = status[aleatorio(0, 9) > 7 ? aleatorio(1, 2) : 0];
            return `
                <tr>
                    <td>${servico}</td>
                    <td><span class="badge ${s.classe}">${s.rotulo}</span></td>
                    <td>${aleatorio(20, 400)} ms</td>
                    <td>${formatarData(new Date())}</td>
                </tr>
            `;
        }).join('');
    }

    atualizarMetricas();
    atualizarServicos();

    // Simula atualização em tempo real a cada 10 segundos
    setInterval(() => {
        atualizarMetricas();
        atualizarServicos();
    }, 10000);
});
