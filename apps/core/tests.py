"""Fábricas de dados compartilhadas e testes do núcleo.

Os testes da plataforma partem sempre de uma base montada à mão, nunca do dataset real: as
asserções precisam saber exatamente quantos incidentes existem em cada dia para verificar médias,
z-scores e janelas. As fábricas abaixo são importadas pelos testes dos demais apps.

A âncora temporal é fixa (`REFERENCIA`) porque toda a plataforma calcula janelas a partir do
último incidente aberto — sem uma âncora determinística, os testes mudariam de resultado a cada
dia que passasse.
"""

from datetime import datetime, timedelta
from itertools import count

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core import tempo
from apps.core.models import (
    Equipe,
    FamiliaSinal,
    Incidente,
    ItemConfiguracao,
    MetricaDiaria,
    Produto,
)

REFERENCIA = timezone.make_aware(datetime(2025, 12, 31, 12, 0))
DATA_REFERENCIA = REFERENCIA.date()

DIM = MetricaDiaria.Dimensao

_sequencia = count(1)


def criar_equipe(nome='Team01'):
    return Equipe.objects.get_or_create(nome=nome)[0]


def criar_produto(codigo='prod1'):
    return Produto.objects.get_or_create(codigo=codigo)[0]


def criar_ativo(codigo='ci1'):
    return ItemConfiguracao.objects.get_or_create(codigo=codigo)[0]


def criar_familia(slug='disco', nome='Disco'):
    return FamiliaSinal.objects.get_or_create(slug=slug, defaults={'nome': nome})[0]


def criar_incidente(aberto_em=None, **campos):
    """Incidente com defaults plausíveis — passe só o que o teste precisa afirmar."""
    campos.setdefault('numero', f'INC{next(_sequencia):07d}')
    campos.setdefault('prioridade', 3)
    campos.setdefault('equipe', criar_equipe())
    campos.setdefault('descricao', 'Problem: Free disk space is less than 10% on volume /')
    campos.setdefault('aberto_por', Incidente.AbertoPor.MONITORAMENTO)
    campos.setdefault('status', Incidente.Status.ENCERRADO)
    campos.setdefault('entrou_kpi', True)
    return Incidente.objects.create(aberto_em=aberto_em or REFERENCIA, **campos)


def criar_metrica(data, dimensao=DIM.GLOBAL, chave='', **valores):
    return MetricaDiaria.objects.create(data=data, dimensao=dimensao, chave=chave, **valores)


def criar_serie(dias, dimensao=DIM.GLOBAL, chave='', ate=None, **valores):
    """`dias` métricas iguais terminando em `ate` (padrão: data de referência)."""
    fim = ate or DATA_REFERENCIA
    return [
        criar_metrica(fim - timedelta(days=i), dimensao, chave, **valores)
        for i in range(dias)
    ]


def criar_serie_valores(valores, dimensao=DIM.GLOBAL, chave='', campo='total_kpi', ate=None):
    """Uma métrica por valor, do mais antigo ao mais recente, terminando em `ate`.

    Serve aos testes de série temporal (z-score, tendência), onde importa o formato da curva.
    """
    fim = ate or DATA_REFERENCIA
    ultimo = len(valores) - 1
    return [
        criar_metrica(fim - timedelta(days=ultimo - i), dimensao, chave,
                      **{campo: valor, 'total': valor})
        for i, valor in enumerate(valores)
    ]


def montar_operacao(dias=120):
    """Base mínima em que todas as telas conseguem renderizar.

    Cobre as quatro dimensões de `MetricaDiaria` mais alguns incidentes reais para as telas de
    drill-down (detalhe de incidente, blast radius, semelhantes).
    """
    equipe = criar_equipe()
    produto = criar_produto()
    ativo = criar_ativo()
    familia = criar_familia()

    diario = {'total': 20, 'total_kpi': 8, 'violacoes': 1, 'criticos': 2,
              'resolvidos': 6, 'soma_mttr_seg': 6 * 1800}
    criar_serie(dias, DIM.GLOBAL, '', **diario)
    criar_serie(dias, DIM.PRODUTO, produto.codigo, **diario)
    criar_serie(dias, DIM.EQUIPE, equipe.nome, **diario)
    criar_serie(dias, DIM.FAMILIA, familia.slug, **diario)
    criar_serie(dias, DIM.CATEGORIA, 'infraestrutura', **diario)

    pai = criar_incidente(
        REFERENCIA - timedelta(hours=3), prioridade=1, produto=produto, equipe=equipe,
        item_configuracao=ativo, familia_sinal=familia, categoria='infraestrutura',
        subcategoria='disco', cod_fechamento='Limpeza de log', solucao=Incidente.Solucao.DEFINITIVA,
        resolvido_em=REFERENCIA - timedelta(hours=1), kpi_violado=False,
    )
    for i in range(3):
        criar_incidente(
            REFERENCIA - timedelta(hours=2, minutes=i * 10), prioridade=3, produto=produto,
            equipe=equipe, item_configuracao=ativo, familia_sinal=familia,
            categoria='infraestrutura', subcategoria='disco', incidente_pai=pai,
            cod_fechamento='Limpeza de log', solucao=Incidente.Solucao.CONTORNO,
            resolvido_em=REFERENCIA - timedelta(minutes=30), kpi_violado=False,
        )
    criar_incidente(
        REFERENCIA - timedelta(days=1), prioridade=4, produto=produto, equipe=equipe,
        item_configuracao=ativo, familia_sinal=familia, entrou_kpi=False,
        status=Incidente.Status.SEM_INTERVENCAO,
    )
    return {'equipe': equipe, 'produto': produto, 'ativo': ativo, 'familia': familia, 'pai': pai}


class TesteComCache(TestCase):
    """Base para todo teste que toque na camada de inferência.

    `referencia_temporal()` e o Correlation Engine guardam resultado em cache de processo; sem
    limpar entre os testes, um caso herdaria a âncora temporal calculada por outro.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)


class TesteDeTela(TesteComCache):
    """Base para testes que abrem telas: toda rota exige login (LoginRequiredMiddleware)."""

    def setUp(self):
        super().setUp()
        operador = User.objects.create_user('operador', password='senha-de-teste')
        self.client.force_login(operador)


class TempoTest(TesteComCache):
    def test_referencia_e_o_ultimo_incidente_aberto(self):
        criar_incidente(REFERENCIA - timedelta(days=10))
        criar_incidente(REFERENCIA)
        self.assertEqual(tempo.referencia_temporal(), REFERENCIA)
        self.assertEqual(tempo.data_referencia(), DATA_REFERENCIA)

    def test_referencia_cai_no_agora_quando_nao_ha_incidentes(self):
        antes = timezone.now()
        self.assertGreaterEqual(tempo.referencia_temporal(), antes)

    def test_janela_inclui_o_dia_de_referencia(self):
        criar_incidente(REFERENCIA)
        inicio, fim = tempo.janela(7)
        self.assertEqual(fim, DATA_REFERENCIA)
        self.assertEqual((fim - inicio).days + 1, 7)

    def test_janela_anterior_nao_encosta_na_atual(self):
        criar_incidente(REFERENCIA)
        inicio_atual, _ = tempo.janela(7)
        inicio_anterior, fim_anterior = tempo.janela_anterior(7)
        self.assertEqual(fim_anterior, inicio_atual - timedelta(days=1))
        self.assertEqual((fim_anterior - inicio_anterior).days + 1, 7)

    def test_invalidar_cache_refaz_a_leitura(self):
        criar_incidente(REFERENCIA - timedelta(days=5))
        self.assertEqual(tempo.referencia_temporal(), REFERENCIA - timedelta(days=5))

        criar_incidente(REFERENCIA)
        self.assertEqual(tempo.referencia_temporal(), REFERENCIA - timedelta(days=5))  # ainda em cache
        tempo.invalidar_cache()
        self.assertEqual(tempo.referencia_temporal(), REFERENCIA)


class IncidenteTest(TesteComCache):
    def test_mttr_so_existe_quando_houve_resolucao(self):
        sem_resolucao = criar_incidente(REFERENCIA, status=Incidente.Status.SEM_INTERVENCAO)
        self.assertIsNone(sem_resolucao.mttr_minutos)

        resolvido = criar_incidente(REFERENCIA, resolvido_em=REFERENCIA + timedelta(minutes=45))
        self.assertEqual(resolvido.mttr_minutos, 45)

    def test_sla_por_prioridade_segue_o_dicionario_de_dados(self):
        self.assertEqual(criar_incidente(prioridade=1).sla_minutos, 240)
        self.assertEqual(criar_incidente(prioridade=3).sla_minutos, 720)

    def test_esta_ativo_cobre_aberto_e_aguardando(self):
        self.assertTrue(criar_incidente(status=Incidente.Status.ABERTO).esta_ativo)
        self.assertTrue(criar_incidente(status=Incidente.Status.AGUARDANDO).esta_ativo)
        self.assertFalse(criar_incidente(status=Incidente.Status.ENCERRADO).esta_ativo)


class RotasTest(TesteDeTela):
    """Toda tela da sidebar precisa renderizar — com dados e também sem nenhum.

    O estado vazio é o que a plataforma mostra logo depois de um `importar_dataset --limpar`, e
    é onde divisões por zero e `[0]` em lista vazia aparecem.
    """

    ROTAS = [
        'core:home', 'core:dados', 'core:sobre',
        'monitor:live', 'monitor:saude', 'monitor:incidentes',
        'forecast:index', 'forecast:modelo',
        'intelligence:risk', 'intelligence:anomaly', 'intelligence:correlation',
        'intelligence:insights',
        'alerts:index', 'alerts:decisao',
        'copilot:index',
        'reports:diario', 'reports:executivo',
    ]

    def test_todas_as_telas_respondem_com_dados(self):
        dados = montar_operacao()
        for nome in self.ROTAS:
            with self.subTest(rota=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

        detalhes = [
            reverse('monitor:detalhe_servico', args=[dados['produto'].codigo]),
            reverse('monitor:detalhe_incidente', args=[dados['pai'].numero]),
        ]
        for url in detalhes:
            with self.subTest(rota=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_todas_as_telas_respondem_sem_dados(self):
        for nome in self.ROTAS:
            with self.subTest(rota=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)


class AcessoTest(TesteComCache):
    """Os dados da Locaweb não podem ficar abertos a quem só tem a URL do deploy."""

    def test_visitante_anonimo_e_levado_ao_login(self):
        login = reverse('accounts:login')
        for nome in RotasTest.ROTAS:
            with self.subTest(rota=nome):
                resposta = self.client.get(reverse(nome))
                self.assertRedirects(resposta, f'{login}?next={reverse(nome)}')

    def test_login_e_cadastro_ficam_abertos(self):
        for nome in ('accounts:login', 'accounts:register'):
            with self.subTest(rota=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

    def test_cadastro_ja_entra_na_plataforma(self):
        self.client.post(reverse('accounts:register'), {
            'username': 'nova.operadora',
            'password1': 'Sentinel#2026!', 'password2': 'Sentinel#2026!',
        })
        self.assertEqual(self.client.get(reverse('core:home')).status_code, 200)
