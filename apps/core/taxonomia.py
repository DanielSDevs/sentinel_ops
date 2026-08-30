"""Taxonomia de sinais derivada da `Descrição resumida`.

A descrição é o único campo textual não anonimizado do dataset. Ela carrega o sintoma técnico
real do alerta ("Free disk space is less than 10% on volume /", "Processor load is too high"),
o que permite agrupar 18 mil descrições distintas em poucas famílias analisáveis.

As regras são avaliadas **em ordem** — a primeira que casar vence. Por isso as mais específicas
vêm antes das genéricas: "Check Application Monitoring" é um catch-all da ferramenta de
monitoração e só deve capturar o que nenhuma regra técnica mais precisa reconheceu.
"""

import re

# (slug, nome exibido, regex)
REGRAS = [
    ('backup', 'Backup', r'backup|bacula'),
    ('banco-dados', 'Banco de Dados', r'postgresql|postgres|mysql|replication|\bdatabase\b'),
    ('disco', 'Disco', r'disk space|free inodes|disk i/o|volume\s*/'),
    ('swap-memoria', 'Swap / Memória', r'swap|\bmemory\b|\bram\b'),
    ('cpu', 'CPU', r'processor load|\bcpu\b|iowait'),
    ('rede', 'Rede / Banda', r'bandwidth|dnsdist|\bnetwork\b|interface|latency'),
    ('email', 'E-mail', r'\bpmta\b|\bsmtp\b|\bimap\b|\bmail\b'),
    ('web', 'Web / HTTP', r'apache|nginx|http|gunicorn|gurnicorn|busy workers|web test|\bport:\s*(80|443)\b'),
    ('disponibilidade', 'Disponibilidade', r'icmp|unavailable|\bping\b|is down|not running|isn.?t running'),
    ('provisionamento', 'Provisionamento', r'recipes::|instalation|\bactivate\b|domain_reserved'),
    ('app-monitoring', 'Application Monitoring', r'application monitoring'),
]

REGRAS_COMPILADAS = [(slug, nome, re.compile(padrao, re.IGNORECASE)) for slug, nome, padrao in REGRAS]

OUTROS = ('outros', 'Outros')


def classificar(descricao):
    """Retorna (slug, nome) da família de sinal para uma descrição."""
    texto = (descricao or '').strip()
    if not texto:
        return OUTROS
    for slug, nome, padrao in REGRAS_COMPILADAS:
        if padrao.search(texto):
            return slug, nome
    return OUTROS


def todas_familias():
    return list(REGRAS) + [(OUTROS[0], OUTROS[1], '')]
