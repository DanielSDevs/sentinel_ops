// Utilitários compartilhados do SentinelOps
window.SentinelOps = window.SentinelOps || {};

/**
 * Formata uma data para o padrão brasileiro (dd/mm/aaaa hh:mm).
 */
window.SentinelOps.formatarData = function (data) {
    return new Intl.DateTimeFormat('pt-BR', {
        dateStyle: 'short',
        timeStyle: 'short',
    }).format(data instanceof Date ? data : new Date(data));
};

/**
 * Debounce: adia a execução de `fn` até que parem as chamadas por `delay` ms.
 */
window.SentinelOps.debounce = function (fn, delay = 300) {
    let timer;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
};

/**
 * Retorna um número inteiro aleatório entre min e max (inclusivo).
 * Usado nos mocks de dados dos dashboards.
 */
window.SentinelOps.aleatorio = function (min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
};
