"""Injeção de cenários operacionais sintéticos sobre a base real.

Serve para demonstrar como a plataforma reage a uma mudança de estado: os incidentes criados aqui
usam o mesmo schema do dataset e são marcados com `origem=SIMULACAO`, então dá para removê-los
sem tocar nos dados reais. Os efeitos se propagam para Health Score, Risk Radar e alertas.
"""

import random
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.models import Equipe, FamiliaSinal, Incidente, Produto
from apps.core.tempo import referencia_temporal

# Sintomas reais retirados do dataset — mantém o cenário simulado coerente com o vocabulário
# técnico que a operação de fato registra.
SINTOMAS = {
    'cpu': ['Problem: Processor load is too high P3', 'Problem: IOwait grown up CPU queue'],
    'disco': ['Problem: Free disk space is less than 10% on volume /'],
    'swap-memoria': ['Problem: Lack of free swap space 40m <5%'],
    'web': ['Problem: Apache Busy Workers', 'Problem: Check: Nginx Type: tcp on Port: 443 Not Running'],
    'disponibilidade': ['Problem: Unavailable by ICMP ping'],
    'banco-dados': ['Problem: Check PostgreSQL Replication Slave'],
}


def _proximo_numero():
    ultimo = (
        Incidente.objects.filter(origem=Incidente.Origem.SIMULACAO)
        .order_by('-numero').values_list('numero', flat=True).first()
    )
    sequencia = int(ultimo.replace('SIM', '')) + 1 if ultimo else 1
    return f'SIM{sequencia:07d}'


def _criar(numero, produto, equipe, familia, descricao, prioridade, momento, entrou_kpi):
    return Incidente(
        numero=numero,
        prioridade=prioridade,
        produto=produto,
        categoria='simulado',
        equipe=equipe,
        familia_sinal=familia,
        descricao=descricao,
        aberto_em=momento,
        duracao_seg=0,
        aberto_por=Incidente.AbertoPor.MONITORAMENTO,
        status=Incidente.Status.ABERTO,
        entrou_kpi=entrou_kpi,
        kpi_violado=None,
        origem=Incidente.Origem.SIMULACAO,
    )


@transaction.atomic
def simular_pico_incidentes(quantidade=12):
    """Rajada de incidentes elegíveis a KPI concentrada em um produto — simula um pico real."""
    produtos = list(Produto.objects.all()[:40])
    equipes = list(Equipe.objects.all())
    familias = {f.slug: f for f in FamiliaSinal.objects.all()}
    if not produtos or not equipes:
        return None

    produto = random.choice(produtos)
    agora = referencia_temporal()
    numero_base = int(_proximo_numero().replace('SIM', ''))

    incidentes = []
    for i in range(quantidade):
        slug = random.choice(list(SINTOMAS))
        incidentes.append(_criar(
            numero=f'SIM{numero_base + i:07d}',
            produto=produto,
            equipe=random.choice(equipes),
            familia=familias.get(slug),
            descricao=random.choice(SINTOMAS[slug]),
            prioridade=random.choices([2, 3], weights=[0.4, 0.6])[0],
            momento=agora - timedelta(minutes=random.randint(0, 180)),
            entrou_kpi=True,
        ))

    Incidente.objects.bulk_create(incidentes)
    return {'produto': produto.codigo, 'quantidade': quantidade}


@transaction.atomic
def simular_degradacao_servico():
    """Incidente crítico (P1) + rajada correlacionada de filhos, simulando blast radius."""
    produtos = list(Produto.objects.all()[:40])
    equipes = list(Equipe.objects.all())
    familias = {f.slug: f for f in FamiliaSinal.objects.all()}
    if not produtos or not equipes:
        return None

    produto = random.choice(produtos)
    equipe = random.choice(equipes)
    agora = referencia_temporal()
    numero_base = int(_proximo_numero().replace('SIM', ''))

    pai = _criar(
        numero=f'SIM{numero_base:07d}',
        produto=produto,
        equipe=equipe,
        familia=familias.get('disponibilidade'),
        descricao='Problem: Unavailable by ICMP ping',
        prioridade=1,
        momento=agora - timedelta(minutes=25),
        entrou_kpi=True,
    )
    pai.save()

    filhos = []
    for i in range(1, random.randint(5, 9)):
        slug = random.choice(['cpu', 'web', 'disponibilidade'])
        filho = _criar(
            numero=f'SIM{numero_base + i:07d}',
            produto=produto,
            equipe=equipe,
            familia=familias.get(slug),
            descricao=random.choice(SINTOMAS[slug]),
            prioridade=random.choice([2, 3]),
            momento=agora - timedelta(minutes=random.randint(0, 20)),
            entrou_kpi=True,
        )
        filho.incidente_pai = pai
        filhos.append(filho)

    Incidente.objects.bulk_create(filhos)
    return {'produto': produto.codigo, 'incidente_pai': pai.numero, 'filhos': len(filhos)}


@transaction.atomic
def limpar_simulacoes():
    """Remove tudo que a simulação criou, devolvendo a plataforma aos dados reais."""
    total, _ = Incidente.objects.filter(origem=Incidente.Origem.SIMULACAO).delete()
    return total
