// Interações da página inicial
document.addEventListener('DOMContentLoaded', () => {
    // Entrada suave dos cards de módulo
    document.querySelectorAll('.module-card').forEach((card, i) => {
        card.style.opacity = '0';
        card.style.transition = `opacity 0.4s ease ${i * 0.08}s, transform 0.15s ease`;
        requestAnimationFrame(() => {
            card.style.opacity = '1';
        });
    });
});
