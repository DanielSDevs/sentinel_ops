"""Importa o dataset real da Locaweb (LW-DATASET.xlsx) para o banco da plataforma."""

from collections import defaultdict
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.core import taxonomia, tempo
from apps.core.models import (
    Equipe, FamiliaSinal, Incidente, ItemConfiguracao, MetricaDiaria, Produto,
)

COLUNAS = {
    'Número': 'numero',
    'Prioridade': 'prioridade',
    'Produto': 'produto',
    'Categoria': 'categoria',
    'Subcategoria': 'subcategoria',
    'Grupo designado': 'equipe',
    'Item de configuração': 'item_cfg',
    'Aberto': 'aberto_em',
    'Resolvido': 'resolvido_em',
    'Encerrado': 'encerrado_em',
    'Duração': 'duracao_seg',
    'Código de fechamento': 'cod_fechamento',
    'Descrição resumida': 'descricao',
    'Solução': 'solucao',
    'Aberto por': 'aberto_por',
    'Incidente Pai': 'incidente_pai',
    'Status': 'status',
    'Entrou para KPI?': 'entrou_kpi',
    'KPI Violado?': 'kpi_violado',
}

MAPA_STATUS = {
    'encerrado': Incidente.Status.ENCERRADO,
    'encerrado automaticamente': Incidente.Status.ENCERRADO_AUTO,
    'sem intervenção': Incidente.Status.SEM_INTERVENCAO,
    'aguardando problema': Incidente.Status.AGUARDANDO,
}

MAPA_ABERTO_POR = {
    'manual': Incidente.AbertoPor.MANUAL,
    'monitoramento': Incidente.AbertoPor.MONITORAMENTO,
}

MAPA_SOLUCAO = {
    'contorno': Incidente.Solucao.CONTORNO,
    'definitiva': Incidente.Solucao.DEFINITIVA,
}

LOTE = 2000
# bulk_update monta um CASE WHEN por linha, gastando várias variáveis SQL em cada uma.
# O SQLite tem teto para isso, então esse lote precisa ser bem menor que o de inserção.
LOTE_UPDATE = 200


def _texto(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ''
    return str(valor).strip()


def _booleano(valor):
    texto = _texto(valor).upper()
    if texto == 'SIM':
        return True
    if texto == 'NAO':
        return False
    return None


def _datahora(valor):
    if valor is None or pd.isna(valor):
        return None
    return timezone.make_aware(valor.to_pydatetime()) if timezone.is_naive(valor) else valor


class Command(BaseCommand):
    help = 'Importa o dataset da Locaweb (LW-DATASET.xlsx) e materializa as métricas diárias.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--arquivo',
            default=str(Path(settings.BASE_DIR) / 'documentos' / 'LW-DATASET.xlsx'),
            help='Caminho do .xlsx do dataset.',
        )
        parser.add_argument('--aba', default='Dataset Geral')
        parser.add_argument(
            '--manter-simulacoes', action='store_true',
            help='Preserva incidentes criados pela simulação manual.',
        )

    def handle(self, *args, **options):
        caminho = Path(options['arquivo'])
        if not caminho.exists():
            raise CommandError(f'Arquivo não encontrado: {caminho}')

        self.stdout.write(f'Lendo {caminho.name}…')
        df = pd.read_excel(caminho, sheet_name=options['aba'])
        faltando = set(COLUNAS) - set(df.columns)
        if faltando:
            raise CommandError(f'Colunas ausentes no dataset: {sorted(faltando)}')
        df = df.rename(columns=COLUNAS)[list(COLUNAS.values())]
        self.stdout.write(f'{len(df):,} linhas lidas.'.replace(',', '.'))

        with transaction.atomic():
            self._limpar(options['manter_simulacoes'])
            dimensoes = self._criar_dimensoes(df)
            total = self._criar_incidentes(df, dimensoes)
            vinculos = self._vincular_incidentes_pai(df)
            linhas_metrica = self._materializar_metricas()

        tempo.invalidar_cache()

        self.stdout.write(self.style.SUCCESS(
            f'\nImportação concluída:\n'
            f'  {total:,} incidentes\n'
            f'  {len(dimensoes["produtos"]):,} produtos · {len(dimensoes["equipes"]):,} equipes · '
            f'{len(dimensoes["itens"]):,} itens de configuração · {len(dimensoes["familias"]):,} famílias de sinal\n'
            f'  {vinculos:,} vínculos incidente-pai resolvidos\n'
            f'  {linhas_metrica:,} linhas de métrica diária\n'
            f'  Referência temporal: {tempo.data_referencia()}'.replace(',', '.')
        ))

    def _limpar(self, manter_simulacoes):
        """Limpa a base anterior sem acionar o coletor de cascata do Django.

        `Incidente` tem uma FK para si mesmo (`incidente_pai`, on_delete=SET_NULL). Num delete
        em massa o ORM carrega todos os filhos para anular o vínculo linha a linha, o que estoura
        o teto de variáveis do SQLite com 122k registros. Zerar o vínculo num único UPDATE e
        apagar via SQL direto resolve — nenhuma outra tabela referencia Incidente, então não há
        cascata legítima a preservar.
        """
        Incidente.objects.filter(incidente_pai__isnull=False).update(incidente_pai=None)

        tabela = Incidente._meta.db_table
        with connection.cursor() as cursor:
            if manter_simulacoes:
                cursor.execute(f'DELETE FROM {tabela} WHERE origem = %s', [Incidente.Origem.DATASET])
            else:
                cursor.execute(f'DELETE FROM {tabela}')
            cursor.execute(f'DELETE FROM {MetricaDiaria._meta.db_table}')

    def _criar_dimensoes(self, df):
        self.stdout.write('Criando dimensões…')

        equipes_nomes = sorted({_texto(v) for v in df.equipe.dropna()} - {''})
        Equipe.objects.bulk_create(
            [Equipe(nome=n) for n in equipes_nomes], ignore_conflicts=True,
        )

        produtos_codigos = sorted({_texto(v) for v in df.produto.dropna()} - {''})
        Produto.objects.bulk_create(
            [Produto(codigo=c) for c in produtos_codigos], ignore_conflicts=True,
        )

        itens_codigos = sorted({_texto(v) for v in df.item_cfg.dropna()} - {''})
        ItemConfiguracao.objects.bulk_create(
            [ItemConfiguracao(codigo=c) for c in itens_codigos], ignore_conflicts=True,
        )

        FamiliaSinal.objects.bulk_create(
            [FamiliaSinal(slug=s, nome=n) for s, n, _ in taxonomia.todas_familias()],
            ignore_conflicts=True,
        )

        return {
            'equipes': {e.nome: e.pk for e in Equipe.objects.all()},
            'produtos': {p.codigo: p.pk for p in Produto.objects.all()},
            'itens': {i.codigo: i.pk for i in ItemConfiguracao.objects.all()},
            'familias': {f.slug: f.pk for f in FamiliaSinal.objects.all()},
        }

    def _criar_incidentes(self, df, dimensoes):
        self.stdout.write('Importando incidentes…')
        buffer, total = [], 0

        for linha in df.itertuples(index=False):
            numero = _texto(linha.numero)
            if not numero:
                continue

            prioridade_texto = _texto(linha.prioridade)
            try:
                prioridade = int(prioridade_texto[0])
            except (ValueError, IndexError):
                continue

            slug, _nome = taxonomia.classificar(_texto(linha.descricao))
            status = MAPA_STATUS.get(_texto(linha.status).lower(), Incidente.Status.ENCERRADO)
            aberto_por = MAPA_ABERTO_POR.get(
                _texto(linha.aberto_por).lower(), Incidente.AbertoPor.MONITORAMENTO,
            )

            duracao = linha.duracao_seg
            duracao = 0 if pd.isna(duracao) else int(duracao)

            buffer.append(Incidente(
                numero=numero,
                prioridade=prioridade,
                produto_id=dimensoes['produtos'].get(_texto(linha.produto)),
                categoria=_texto(linha.categoria),
                subcategoria=_texto(linha.subcategoria),
                equipe_id=dimensoes['equipes'][_texto(linha.equipe)],
                item_configuracao_id=dimensoes['itens'].get(_texto(linha.item_cfg)),
                familia_sinal_id=dimensoes['familias'][slug],
                descricao=_texto(linha.descricao),
                aberto_em=_datahora(linha.aberto_em),
                resolvido_em=_datahora(linha.resolvido_em),
                encerrado_em=_datahora(linha.encerrado_em),
                duracao_seg=duracao,
                cod_fechamento=_texto(linha.cod_fechamento),
                solucao=MAPA_SOLUCAO.get(_texto(linha.solucao).lower(), ''),
                aberto_por=aberto_por,
                status=status,
                entrou_kpi=bool(_booleano(linha.entrou_kpi)),
                kpi_violado=_booleano(linha.kpi_violado),
                origem=Incidente.Origem.DATASET,
            ))

            if len(buffer) >= LOTE:
                Incidente.objects.bulk_create(buffer, batch_size=LOTE)
                total += len(buffer)
                buffer.clear()
                self.stdout.write(f'  {total:,} incidentes…'.replace(',', '.'), ending='\r')

        if buffer:
            Incidente.objects.bulk_create(buffer, batch_size=LOTE)
            total += len(buffer)

        self.stdout.write(f'  {total:,} incidentes importados.        '.replace(',', '.'))
        return total

    def _vincular_incidentes_pai(self, df):
        """Resolve `Incidente Pai` numa segunda passada (o pai pode aparecer depois do filho)."""
        self.stdout.write('Resolvendo vínculos incidente-pai…')
        pares = [
            (_texto(l.numero), _texto(l.incidente_pai))
            for l in df.itertuples(index=False)
            if _texto(l.incidente_pai)
        ]
        if not pares:
            return 0

        ids_por_numero = dict(Incidente.objects.values_list('numero', 'pk'))
        atualizar = []
        for numero, numero_pai in pares:
            filho_id = ids_por_numero.get(numero)
            pai_id = ids_por_numero.get(numero_pai)
            # Parte dos pais referenciados não existe no extrato — esses vínculos ficam nulos.
            if filho_id and pai_id and filho_id != pai_id:
                atualizar.append(Incidente(pk=filho_id, incidente_pai_id=pai_id))

        Incidente.objects.bulk_update(atualizar, ['incidente_pai'], batch_size=LOTE_UPDATE)
        return len(atualizar)

    def _materializar_metricas(self):
        self.stdout.write('Materializando métricas diárias…')
        campos = (
            'aberto_em', 'entrou_kpi', 'kpi_violado', 'prioridade', 'resolvido_em',
            'produto__codigo', 'equipe__nome', 'familia_sinal__slug', 'categoria',
        )
        acumulador = defaultdict(lambda: {
            'total': 0, 'total_kpi': 0, 'violacoes': 0,
            'criticos': 0, 'resolvidos': 0, 'soma_mttr_seg': 0,
        })

        for inc in Incidente.objects.values(*campos).iterator(chunk_size=5000):
            data = timezone.localtime(inc['aberto_em']).date()
            elegivel = bool(inc['entrou_kpi'])

            # MTTR e severidade só fazem sentido sobre o que exige intervenção humana. Chamados
            # automáticos fecham em segundos e distorceriam as duas métricas.
            mttr = 0
            resolvido = 0
            if elegivel and inc['resolvido_em']:
                resolvido = 1
                mttr = int((inc['resolvido_em'] - inc['aberto_em']).total_seconds())
            critico = 1 if (elegivel and inc['prioridade'] in (1, 2)) else 0

            chaves = [(MetricaDiaria.Dimensao.GLOBAL, '')]
            if inc['produto__codigo']:
                chaves.append((MetricaDiaria.Dimensao.PRODUTO, inc['produto__codigo']))
            if inc['equipe__nome']:
                chaves.append((MetricaDiaria.Dimensao.EQUIPE, inc['equipe__nome']))
            if inc['familia_sinal__slug']:
                chaves.append((MetricaDiaria.Dimensao.FAMILIA, inc['familia_sinal__slug']))
            if inc['categoria']:
                chaves.append((MetricaDiaria.Dimensao.CATEGORIA, inc['categoria']))

            for dimensao, chave in chaves:
                alvo = acumulador[(data, dimensao, chave)]
                alvo['total'] += 1
                alvo['total_kpi'] += 1 if elegivel else 0
                alvo['violacoes'] += 1 if inc['kpi_violado'] else 0
                alvo['criticos'] += critico
                alvo['resolvidos'] += resolvido
                alvo['soma_mttr_seg'] += mttr

        registros = [
            MetricaDiaria(data=data, dimensao=dimensao, chave=chave, **valores)
            for (data, dimensao, chave), valores in acumulador.items()
        ]
        MetricaDiaria.objects.bulk_create(registros, batch_size=LOTE)
        return len(registros)
