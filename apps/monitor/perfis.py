"""Leitura operacional dos grupos de comportamento (modelo de clusterização).

O `model_cluster` agrupa produtos por **como se comportam**, não por quanto volume têm:
volume típico, participação de críticos, taxa de violação, MTTR, regularidade, ruído de
monitoramento e instabilidade. O modelo devolve "grupo 0" e "grupo 1" — números que não
dizem nada a quem opera.

Este módulo faz a ponte: rotula cada grupo a partir do próprio perfil e calcula o contraste
entre eles, para a tela poder mostrar a diferença que importa.

O rótulo é **derivado dos números**, nunca fixo no código. Se o próximo treino produzir três
grupos, ou inverter quem é o de alto volume, o rótulo acompanha — que é a diferença entre
ler o modelo e decorar a saída dele.
"""

from apps.ml.services import inferencia

# Quanto a característica precisa se destacar em relação à média dos grupos para virar rótulo.
DESTAQUE = 1.25


def _rotular(perfil, medias):
    """Nome do grupo a partir do que mais o distingue dos demais.

    Ordem de precedência deliberada: volume primeiro (é o que o operador percebe), depois
    lentidão de resolução, depois irregularidade. Um grupo que não se destaca em nada fica
    com o rótulo neutro em vez de ganhar um nome inventado."""
    volume = perfil.get('volume_medio') or 0
    mttr = perfil.get('mttr') or 0
    dias = perfil.get('dias_ativos') or 0
    instabilidade = perfil.get('instabilidade') or 0

    if medias['volume_medio'] and volume >= medias['volume_medio'] * DESTAQUE:
        return 'Núcleo de alto volume', (
            'Produtos que recebem chamado quase todo dia e concentram a maior parte da fila.'
        )
    if medias['mttr'] and mttr >= medias['mttr'] * DESTAQUE:
        return 'Cauda de resolução lenta', (
            'Produtos de baixo volume e ocorrência esporádica, mas que demoram mais para '
            'serem resolvidos quando aparecem.'
        )
    if medias['dias_ativos'] and dias <= medias['dias_ativos'] / DESTAQUE:
        return 'Ocorrência esporádica', (
            'Produtos que passam boa parte dos dias sem nenhum chamado.'
        )
    if medias['instabilidade'] and instabilidade >= medias['instabilidade'] * DESTAQUE:
        return 'Comportamento irregular', (
            'Produtos cujo volume varia muito de um dia para o outro.'
        )
    return 'Comportamento regular', (
        'Produtos sem característica que os destaque dos demais grupos.'
    )


def _medidas(perfil):
    """Os números do perfil já traduzidos para a unidade que a operação lê."""
    mttr_seg = perfil.get('mttr') or 0
    return [
        {'rotulo': 'Chamados por dia', 'valor': f"{perfil.get('volume_medio', 0):.1f}"},
        {'rotulo': 'Tempo médio de resolução', 'valor': f'{mttr_seg / 60:.0f} min'},
        {'rotulo': 'Dias com chamado', 'valor': f"{(perfil.get('dias_ativos') or 0) * 100:.0f}%"},
        {'rotulo': 'Fatia de críticos', 'valor': f"{(perfil.get('share_criticos') or 0) * 100:.0f}%"},
        {'rotulo': 'Estouro de prazo', 'valor': f"{(perfil.get('taxa_violacao') or 0) * 100:.1f}%"},
    ]


def grupos_de_comportamento():
    """Grupos prontos para a tela, ordenados do maior para o menor volume típico.

    Devolve None quando o modelo não foi treinado — a tela some, como todo bloco que depende
    de artefato."""
    bruto = inferencia.clusters()
    if not bruto or not bruto.get('grupos'):
        return None

    perfis = [g['perfil'] for g in bruto['grupos'].values()]
    campos = ('volume_medio', 'mttr', 'dias_ativos', 'instabilidade')
    medias = {
        campo: sum((p.get(campo) or 0) for p in perfis) / len(perfis)
        for campo in campos
    }

    grupos = []
    for numero, dados in bruto['grupos'].items():
        perfil = dados['perfil']
        titulo, descricao = _rotular(perfil, medias)
        grupos.append({
            'numero': numero,
            'titulo': titulo,
            'descricao': descricao,
            'produtos': dados['produtos'],
            'quantidade': len(dados['produtos']),
            'medidas': _medidas(perfil),
            'volume_medio': perfil.get('volume_medio') or 0,
            'mttr_min': round((perfil.get('mttr') or 0) / 60),
        })

    grupos.sort(key=lambda g: g['volume_medio'], reverse=True)
    return {'grupos': grupos, 'contraste': _contraste(grupos)}


def _contraste(grupos):
    """A diferença que muda a decisão: quem resolve mais devagar, e quantas vezes mais.

    Sem isso a tela vira duas listas de produtos lado a lado. O contraste é o motivo de a
    clusterização existir — ela separa produtos que exigem tratamento diferente."""
    if len(grupos) < 2:
        return None
    mais_lento = max(grupos, key=lambda g: g['mttr_min'])
    mais_rapido = min(grupos, key=lambda g: g['mttr_min'])
    if not mais_rapido['mttr_min'] or mais_lento is mais_rapido:
        return None
    return {
        'lento': mais_lento['titulo'],
        'rapido': mais_rapido['titulo'],
        'vezes': round(mais_lento['mttr_min'] / mais_rapido['mttr_min'], 1),
        'mttr_lento': mais_lento['mttr_min'],
        'mttr_rapido': mais_rapido['mttr_min'],
    }
