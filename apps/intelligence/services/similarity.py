"""Incident Intelligence — incidentes semelhantes e hipótese de causa.

A similaridade aqui é **match categórico real**, não embeddings nem ML: dois incidentes se
parecem na medida em que compartilham produto, categoria, subcategoria, família de sinal e
prioridade. Cada atributo tem um peso e o percentual exibido é literalmente a soma dos pesos
que casaram — auditável linha a linha.

Sobre esses vizinhos históricos, o dataset entrega o que a operação precisa saber: quanto tempo
levou para resolver (MTTR real), como foi fechado (`Código de fechamento`) e se a solução foi
definitiva ou contorno.
"""

from collections import Counter
from dataclasses import dataclass

from django.db.models import Q

from apps.core.models import Incidente

PESOS = {
    'produto': 30,
    'categoria': 25,
    'subcategoria': 20,
    'familia_sinal': 15,
    'prioridade': 10,
}


@dataclass
class Semelhante:
    incidente: object
    similaridade: int
    atributos_iguais: list


def _pontuar(referencia, candidato):
    pontos, iguais = 0, []
    comparacoes = [
        ('produto', referencia.produto_id, candidato.produto_id, 'produto'),
        ('categoria', referencia.categoria, candidato.categoria, 'categoria'),
        ('subcategoria', referencia.subcategoria, candidato.subcategoria, 'subcategoria'),
        ('familia_sinal', referencia.familia_sinal_id, candidato.familia_sinal_id, 'família de sinal'),
        ('prioridade', referencia.prioridade, candidato.prioridade, 'prioridade'),
    ]
    for chave, valor_ref, valor_cand, rotulo in comparacoes:
        if valor_ref and valor_ref == valor_cand:
            pontos += PESOS[chave]
            iguais.append(rotulo)
    return pontos, iguais


def semelhantes(incidente, limite=5):
    """Vizinhos históricos, buscando primeiro num universo plausível e depois pontuando."""
    filtro = Q()
    if incidente.produto_id:
        filtro |= Q(produto_id=incidente.produto_id)
    if incidente.familia_sinal_id:
        filtro |= Q(familia_sinal_id=incidente.familia_sinal_id)
    if incidente.categoria:
        filtro |= Q(categoria=incidente.categoria)
    if not filtro:
        return []

    candidatos = (
        Incidente.objects
        .filter(filtro)
        .exclude(pk=incidente.pk)
        .filter(aberto_em__lt=incidente.aberto_em)
        .select_related('produto', 'equipe', 'familia_sinal')
        .order_by('-aberto_em')[:400]
    )

    pontuados = []
    for candidato in candidatos:
        pontos, iguais = _pontuar(incidente, candidato)
        if pontos >= 40:
            pontuados.append(Semelhante(candidato, pontos, iguais))

    pontuados.sort(key=lambda s: (s.similaridade, s.incidente.aberto_em), reverse=True)
    return pontuados[:limite]


def perfil_resolucao(incidente, vizinhos):
    """O que o histórico diz sobre como esse tipo de incidente costuma ser resolvido."""
    if not vizinhos:
        return None

    incidentes = [v.incidente for v in vizinhos]
    tempos = [i.mttr_minutos for i in incidentes if i.mttr_minutos is not None]
    fechamentos = Counter(i.cod_fechamento for i in incidentes if i.cod_fechamento)
    solucoes = Counter(i.get_solucao_display() for i in incidentes if i.solucao)
    violacoes = sum(1 for i in incidentes if i.kpi_violado)

    return {
        'amostra': len(incidentes),
        'mttr_mediano': round(sorted(tempos)[len(tempos) // 2]) if tempos else None,
        'fechamento_frequente': fechamentos.most_common(1)[0] if fechamentos else None,
        'solucao_frequente': solucoes.most_common(1)[0] if solucoes else None,
        'violacoes': violacoes,
        'taxa_violacao': round((violacoes / len(incidentes)) * 100, 1),
    }


def hipotese_causa(incidente, perfil):
    """Hipótese textual construída só a partir de evidência do dataset."""
    if not perfil or not perfil['fechamento_frequente']:
        return None
    codigo, ocorrencias = perfil['fechamento_frequente']
    pct = round((ocorrencias / perfil['amostra']) * 100)
    return (
        f'Entre os {perfil["amostra"]} incidentes semelhantes anteriores, {pct}% foram encerrados '
        f'como "{codigo}" — é a hipótese de causa mais provável para este caso.'
    )
