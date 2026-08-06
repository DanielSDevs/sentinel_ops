// Comportamentos globais do SentinelOps
document.addEventListener('DOMContentLoaded', () => {
    // Menu responsivo (hambúrguer)
    const toggle = document.querySelector('[data-nav-toggle]');
    const menu = document.querySelector('[data-nav-menu]');

    if (toggle && menu) {
        toggle.addEventListener('click', () => {
            const open = menu.classList.toggle('navbar__menu--open');
            toggle.setAttribute('aria-expanded', String(open));
        });
    }

    // Marca o link ativo da navbar conforme a URL atual
    document.querySelectorAll('.navbar__link').forEach((link) => {
        const href = link.getAttribute('href');
        if (href === window.location.pathname ||
            (href !== '/' && window.location.pathname.startsWith(href))) {
            link.classList.add('navbar__link--active');
        }
    });

    // Fecha alertas
    document.querySelectorAll('[data-alert-close]').forEach((btn) => {
        btn.addEventListener('click', () => btn.closest('.alert').remove());
    });
});
