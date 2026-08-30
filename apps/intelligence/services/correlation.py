"""Correlation Engine — relações reais entre eventos.

Duas fontes de correlação, ambas presentes no dataset (nada inferido artificialmente):

1. **Blast radius**: o campo `Incidente Pai` liga um incidente-raiz aos seus desdobramentos.
   São 15.127 filhos distribuídos em 3.065 pais — tempestades de incidentes registradas pela
   própria operação. É o sinal mais forte de causalidade que a base oferece.

2. **Co-ocorrência de sintomas**: famílias de sinal que disparam no mesmo item de configuração
   dentro de uma janela curta. Correlação temporal ≠ causalidade — a UI apresenta como cadeia
   observada, não como diagnóstico fechado.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count

from apps.core.models import FamiliaSinal, Incidente
from apps.core.tempo import referencia_temporal

JANELA_COOCORRENCIA_MIN = 60
CACHE_TTL = 300


@dataclass
class Tempestade:
    pai: object
    total_filhos: int
    inicio: object
    fim: object
    produtos: list = field(default_factory=list)
    equipes: list = field(default_factory=list)
    familias: list = field(default_factory=list)

    @property
    def duracao_horas(self):
        if not self.inicio or not self.fim:
            return None
        return round((self.fim - self.inicio).total_seconds() / 3600, 1)


def tempestades(limite=8, dias=None):
    """Maiores blast radius — incidentes-pai com mais desdobramentos."""
    filhos_qs = Incidente.objects.filter(incidente_pai__isnull=False)
    if dias:
        filhos_qs = filhos_qs.filter(
            incidente_pai__aberto_em__gte=referencia_temporal() - timedelta(days=dias)
        )

    # Contamos pelo lado do filho: assim o agrupamento usa o índice de `incidente_pai`
    # diretamente. Anotar `Count('filhos')` no pai obrigava a varrer a tabela inteira.
    contagens = (
        filhos_qs.values('incidente_pai')
        .annotate(n_filhos=Count('id'))
        .order_by('-n_filhos')[:limite]
    )
    ordem = [linha['incidente_pai'] for linha in contagens]
    if not ordem:
        return []

    pais = Incidente.objects.select_related('produto', 'equipe', 'familia_sinal').in_bulk(ordem)

    # Um único SELECT para os filhos de todas as tempestades. `values_list` também evita o
    # N+1 que instâncias parcialmente carregadas provocavam: o related manager lê
    # `incidente_pai_id` de cada filho e, se o campo estivesse diferido, ia ao banco por linha.
    agrupados = defaultdict(list)
    campos = ('incidente_pai_id', 'aberto_em', 'produto__codigo', 'equipe__nome', 'familia_sinal__nome')
    for pai_id, aberto_em, produto, equipe, familia in (
        Incidente.objects.filter(incidente_pai_id__in=ordem).values_list(*campos)
    ):
        agrupados[pai_id].append((aberto_em, produto, equipe, familia))

    resultado = []
    for pai_id in ordem:
        pai = pais.get(pai_id)
        filhos = agrupados.get(pai_id)
        if not pai or not filhos:
            continue
        datas = [f[0] for f in filhos] + [pai.aberto_em]
        resultado.append(Tempestade(
            pai=pai,
            total_filhos=len(filhos),
            inicio=min(datas),
            fim=max(datas),
            produtos=Counter(f[1] for f in filhos if f[1]).most_common(3),
            equipes=Counter(f[2] for f in filhos if f[2]).most_common(3),
            familias=Counter(f[3] for f in filhos if f[3]).most_common(4),
        ))
    return resultado


def _versao_dados():
    """Assinatura da base — muda sozinha quando a simulação injeta ou limpa incidentes.

    A simulação escreve direto via `bulk_create`/`delete`, sem passar por nenhum hook. Amarrar a
    chave de cache à contagem (4 ms) faz o resultado expirar junto com o cenário, sem espalhar
    invalidação manual por cada ponto de escrita.
    """
    return f'{referencia_temporal():%Y%m%d%H%M}:{Incidente.objects.count()}'


def cadeia_de_sintomas(limite=8, dias=90):
    """Pares de famílias de sinal que se seguem no mesmo item de configuração.

    Conta quantas vezes a família B aparece até `JANELA_COOCORRENCIA_MIN` minutos depois da
    família A no mesmo ativo — revelando sequências como disco cheio → indisponibilidade.
    """
    chave_cache = f'sentinelops:cadeia:{dias}:{limite}:{_versao_dados()}'
    em_cache = cache.get(chave_cache)
    if em_cache is not None:
        return em_cache

    pares = _contar_pares(dias)
    nomes = dict(FamiliaSinal.objects.values_list('id', 'nome'))
    total = sum(pares.values()) or 1
    resultado = [
        {
            'de': nomes.get(de, de),
            'para': nomes.get(para, para),
            'ocorrencias': n,
            'participacao': round((n / total) * 100, 1),
        }
        for (de, para), n in pares.most_common(limite)
    ]
    cache.set(chave_cache, resultado, CACHE_TTL)
    return resultado


def _contar_pares(dias):
    """Varre os eventos da janela contando pares (família A → família B) por ativo.

    São ~69k eventos em 90 dias, então o caminho importa: os incidentes chegam já ordenados por
    ativo e horário (índice composto `item_configuracao` + `aberto_em`), o que permite fechar a
    sequência de cada ativo numa passada só, sem materializar a base inteira em memória. As
    famílias trafegam como id — resolver o nome no SQL custaria um join e 69k strings.
    """
    desde = referencia_temporal() - timedelta(days=dias)
    eventos = (
        Incidente.objects
        .filter(aberto_em__gte=desde, item_configuracao__isnull=False, familia_sinal__isnull=False)
        .values_list('item_configuracao_id', 'familia_sinal_id', 'aberto_em')
        .order_by('item_configuracao_id', 'aberto_em')
        .iterator(chunk_size=5000)
    )

    pares = Counter()
    janela = timedelta(minutes=JANELA_COOCORRENCIA_MIN)

    def fechar(sequencia):
        for i, (momento_a, familia_a) in enumerate(sequencia):
            limite_janela = momento_a + janela
            for momento_b, familia_b in sequencia[i + 1:]:
                if momento_b > limite_janela:
                    break
                if familia_a != familia_b:
                    pares[(familia_a, familia_b)] += 1

    ativo_atual = None
    sequencia = []
    for ativo_id, familia_id, momento in eventos:
        if ativo_id != ativo_atual:
            fechar(sequencia)
            ativo_atual, sequencia = ativo_id, []
        sequencia.append((momento, familia_id))
    fechar(sequencia)
    return pares


def grafo_tempestade(tempestade):
    """Estrutura simples de nós/arestas para desenhar o blast radius em SVG."""
    nos = [{'id': 'raiz', 'rotulo': tempestade.pai.numero, 'tipo': 'raiz'}]
    arestas = []
    for i, (familia, quantidade) in enumerate(tempestade.familias):
        no_id = f'f{i}'
        nos.append({'id': no_id, 'rotulo': familia, 'tipo': 'familia', 'peso': quantidade})
        arestas.append({'de': 'raiz', 'para': no_id, 'peso': quantidade})
    return {'nos': nos, 'arestas': arestas}
