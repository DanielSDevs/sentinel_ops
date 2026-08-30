// Comportamentos globais do SentinelOps
document.addEventListener('DOMContentLoaded', () => {
    // Sidebar responsiva (drawer no mobile)
    const toggle = document.querySelector('[data-sidebar-toggle]');
    const sidebar = document.querySelector('[data-sidebar]');
    const scrim = document.querySelector('[data-sidebar-scrim]');

    function fecharSidebar() {
        sidebar.classList.remove('sidebar--open');
        toggle.setAttribute('aria-expanded', 'false');
    }

    if (toggle && sidebar) {
        toggle.addEventListener('click', () => {
            const open = sidebar.classList.toggle('sidebar--open');
            toggle.setAttribute('aria-expanded', String(open));
        });
    }

    if (scrim) {
        scrim.addEventListener('click', fecharSidebar);
    }

    // Marca o link ativo da sidebar conforme a URL atual
    document.querySelectorAll('.sidebar__link').forEach((link) => {
        const href = link.getAttribute('href');
        if (href === window.location.pathname ||
            (href !== '/' && window.location.pathname.startsWith(href))) {
            link.classList.add('sidebar__link--active');
        }
    });

    // Fecha alertas
    document.querySelectorAll('[data-alert-close]').forEach((btn) => {
        btn.addEventListener('click', () => btn.closest('.alert').remove());
    });

    // Entrada escalonada (stagger) dos cartões em grid
    document.querySelectorAll('.grid').forEach((grid) => {
        Array.from(grid.children).forEach((card, i) => {
            card.style.setProperty('--stagger-delay', `${i * 0.08}s`);
            card.classList.add('animate-in');
        });
    });
});
