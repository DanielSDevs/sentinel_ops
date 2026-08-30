// Agent Copilot — respostas simuladas até a integração com o backend do chatbot
document.addEventListener('DOMContentLoaded', () => {
    const mensagens = document.querySelector('[data-chat-messages]');
    const form = document.querySelector('[data-chat-form]');
    const input = document.querySelector('[data-chat-input]');
    if (!mensagens || !form || !input) return;

    const respostas = [
        {
            padrao: /resumo/i,
            texto: 'Resumo do dia: 34 incidentes abertos, 3 críticos (P1/P2). SLA em 96% de conformidade. Previsão D+1 indica leve alta no volume.',
        },
        {
            padrao: /cr[íi]tico/i,
            texto: 'Há 3 incidentes críticos no momento: 2 relacionados a latência no API Gateway e 1 de indisponibilidade parcial no serviço de autenticação.',
        },
        {
            padrao: /previs|forecast/i,
            texto: 'A previsão D+1 é de 42 incidentes e a D+7 acumula 310. A tendência é de alta em relação à semana anterior — recomendo reforçar o plantão.',
        },
        {
            padrao: /sla/i,
            texto: 'O SLA geral está em 96% de conformidade. A fila P2 está próxima do limite — há um alerta ativo no Risk Alert Center.',
        },
    ];

    function adicionarMensagem(texto, tipo) {
        const div = document.createElement('div');
        div.className = `copilot-message copilot-message--${tipo}`;
        div.textContent = texto;
        mensagens.appendChild(div);
        mensagens.scrollTop = mensagens.scrollHeight;
    }

    function mostrarDigitando() {
        const div = document.createElement('div');
        div.className = 'copilot-typing';
        div.innerHTML = '<span class="copilot-typing__dot"></span><span class="copilot-typing__dot"></span><span class="copilot-typing__dot"></span>';
        mensagens.appendChild(div);
        mensagens.scrollTop = mensagens.scrollHeight;
        return div;
    }

    form.addEventListener('submit', (evento) => {
        evento.preventDefault();
        const pergunta = input.value.trim();
        if (!pergunta) return;

        adicionarMensagem(pergunta, 'user');
        input.value = '';

        const digitando = mostrarDigitando();
        const resposta = respostas.find((r) => r.padrao.test(pergunta));
        setTimeout(() => {
            digitando.remove();
            adicionarMensagem(
                resposta
                    ? resposta.texto
                    : 'Ainda estou aprendendo sobre esse assunto. Tente perguntar sobre "resumo do dia", "incidentes críticos", "previsão" ou "SLA".',
                'bot',
            );
        }, 700);
    });
});
