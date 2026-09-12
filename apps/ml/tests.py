"""Testes da camada de ML.

Dois grupos, com propósitos diferentes:

- **Contrato do pipeline** (features, partições, métricas): testes determinísticos sobre séries
  montadas à mão, onde o valor esperado pode ser conferido no papel. É aqui que mora a proteção
  contra vazamento temporal — o erro mais caro deste tipo de projeto, porque não quebra nada:
  só produz uma métrica boa demais que ninguém questiona.
- **Comportamento sem modelo treinado**: a plataforma precisa dizer "não treinado" em vez de
  inventar número. Como `ml/models/` não é pré-requisito para rodar os testes, este é o caminho
  que o CI exercita por padrão.
"""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from django.test import TestCase
from django.urls import reverse

from apps.core.tests import TesteDeTela
from apps.ml.models import PrevisaoRegistrada
from apps.ml.services import catalogo, inferencia
from ml.src import features, metrics, models_zoo, splits


def _incidentes_sinteticos(dias=200, inicio='2025-01-01'):
    """Fato mínimo no formato que `dataset.carregar_incidentes` devolve."""
    linhas = []
    for indice, dia in enumerate(pd.date_range(inicio, periods=dias, freq='D')):
        # Volume determinístico e aprendível: perfil de dia da semana mais um degrau no início
        # do mês. O degrau é o ponto — ele quebra a baseline sazonal (que repete o valor de 7
        # dias antes e atravessa a virada de mês errando) sem deixar de ser um padrão que um
        # modelo com `dia_mes` entre as features consegue capturar.
        volume = 10 + 2 * dia.dayofweek + (8 if dia.day <= 5 else 0)
        for n in range(volume):
            linhas.append({
                'numero': f'INC{indice:04d}{n:03d}',
                'prioridade': 2 if n % 5 == 0 else 3,
                'categoria': 'cat1',
                'equipe': 'Team01' if n % 2 else 'Team02',
                'produto': 'prod1',
                'item_configuracao': f'ci{n % 3}',
                'familia_sinal': 'disco',
                'aberto_por': 'manual' if n % 3 else 'monitoramento',
                'aberto_em': dia + pd.Timedelta(hours=9 + (n % 8)),
                'resolvido_em': dia + pd.Timedelta(hours=11 + (n % 8)),
                'data': dia,
                'entrou_kpi': True,
                'kpi_violado': n == 0,
                'mttr_min': 120.0,
                'incidente_pai_id': None,
            })
    quadro = pd.DataFrame(linhas)
    quadro['kpi_violado'] = quadro['kpi_violado'].astype('boolean')
    return quadro


class FeaturesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.incidentes = _incidentes_sinteticos()
        cls.painel = features.painel_diario(cls.incidentes, min_dias=60)
        cls.matriz = features.construir_features(cls.painel)

    def test_painel_reproduz_o_volume_diario(self):
        global_ = self.painel[self.painel['dimensao'] == 'global'].set_index('data')
        esperado = self.incidentes.groupby('data').size()
        for dia, valor in esperado.items():
            self.assertEqual(global_.loc[dia, 'total_kpi'], valor)

    def test_alvo_sempre_no_futuro_da_origem(self):
        self.assertTrue((self.matriz['data_alvo'] > self.matriz['data_origem']).all())
        for horizonte, bloco in self.matriz.groupby('horizonte'):
            distancia = (bloco['data_alvo'] - bloco['data_origem']).dt.days.unique()
            self.assertEqual(list(distancia), [horizonte])

    def test_features_nao_contem_o_alvo(self):
        """`lag_0` é o volume do dia de origem, nunca o do dia previsto.

        É o teste que pega o vazamento clássico: um `shift` com sinal trocado faria `lag_0`
        valer o próprio alvo, a métrica ficaria excelente e nada quebraria.
        """
        X, y, ident = features.separar(self.matriz)
        h1 = self.matriz['horizonte'] == 1
        origem = self.matriz.loc[h1].set_index(['dimensao', 'chave', 'data_origem'])
        painel = self.painel.set_index(['dimensao', 'chave', 'data'])['total_kpi']

        for chave, linha in origem.head(40).iterrows():
            self.assertEqual(linha['lag_0'], painel.loc[chave])
            self.assertNotEqual(linha['lag_0'], linha['alvo'])

    def test_media_7_usa_apenas_dias_ate_a_origem(self):
        h1 = self.matriz[(self.matriz['horizonte'] == 1)
                         & (self.matriz['dimensao'] == 'global')].tail(5)
        serie = (
            self.painel[self.painel['dimensao'] == 'global'].set_index('data')['total_kpi']
        )
        for _, linha in h1.iterrows():
            janela = serie.loc[:linha['data_origem']].tail(7)
            self.assertAlmostEqual(linha['media_7'], janela.mean(), places=6)

    def test_feriados_incluem_fixos_e_moveis(self):
        feriados = features.feriados_brasil([2025])
        self.assertIn(date(2025, 1, 1), feriados)      # Confraternização
        self.assertIn(date(2025, 9, 7), feriados)      # Independência
        self.assertIn(date(2025, 3, 4), feriados)      # Carnaval (terça) — móvel
        self.assertIn(date(2025, 4, 18), feriados)     # Sexta-feira Santa — móvel
        self.assertNotIn(date(2025, 1, 2), feriados)

    def test_taxa_historica_por_incidente_ignora_a_propria_linha(self):
        """A taxa de violação da equipe não pode conhecer o desfecho do incidente que ela explica."""
        X, y, ident = features.features_incidentes(self.incidentes, self.painel)
        primeira = X.iloc[0]
        # Na primeira linha não existe passado nenhum: a taxa tem de ser zero, mesmo que esse
        # incidente tenha violado.
        self.assertEqual(primeira['taxa_violacao_equipe'], 0.0)
        self.assertEqual(len(X), len(y))

    def test_features_de_incidente_nao_trazem_campos_de_desfecho(self):
        X, _, _ = features.features_incidentes(self.incidentes, self.painel)
        proibidos = ('duracao', 'resolvido', 'encerrado', 'status', 'fechamento', 'solucao',
                     'mttr_min', 'kpi_violado')
        for coluna in X.columns:
            for proibido in proibidos:
                self.assertNotIn(proibido, coluna)


class SplitsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.matriz = features.construir_features(
            features.painel_diario(_incidentes_sinteticos(), min_dias=60),
        )

    def test_particoes_nao_se_sobrepoem_no_tempo(self):
        treino, validacao, teste = splits.dividir(self.matriz)
        alvo = self.matriz['data_alvo']
        self.assertLess(alvo[treino].max(), alvo[validacao].min())
        self.assertLess(alvo[validacao].max(), alvo[teste].min())

    def test_corte_e_pela_data_alvo_e_nao_pela_origem(self):
        """Uma linha de horizonte 7 no treino não pode enxergar dentro da validação.

        Cortar pela data de origem deixaria passar exatamente isso: origem no dia 20 com
        horizonte 7 tem alvo no dia 27, já dentro da partição seguinte.
        """
        treino, _, _ = splits.dividir(self.matriz)
        inicio_validacao, _ = splits.limites(self.matriz['data_alvo'])

        h7 = self.matriz[treino & (self.matriz['horizonte'] == 7)]
        self.assertLess(h7['data_alvo'].max(), inicio_validacao)
        # Consequência direta: a origem mais recente do treino em D+7 fica 7 dias atrás do corte.
        self.assertLessEqual(
            h7['data_origem'].max(), inicio_validacao - pd.Timedelta(days=7),
        )

    def test_walk_forward_treina_somente_com_o_passado_da_origem(self):
        for corte, treino, teste in splits.particoes_walk_forward(self.matriz, janelas=3):
            self.assertLess(self.matriz.loc[treino, 'data_alvo'].max(), corte)
            self.assertGreaterEqual(self.matriz.loc[teste, 'data_alvo'].min(), corte)

    def test_inicio_de_regime_descarta_o_periodo_de_backfill(self):
        serie = pd.Series(
            [0] * 100 + [50] * 100,
            index=pd.date_range('2024-01-01', periods=200, freq='D'),
        )
        inicio = splits.detectar_inicio_regime(serie)
        self.assertGreaterEqual(inicio, pd.Timestamp('2024-04-01'))


class MetricasTest(TestCase):
    def test_mae_e_rmse_conferem_na_mao(self):
        real = [10, 20, 30]
        previsto = [12, 18, 33]           # erros: -2, +2, -3
        resultado = metrics.regressao(real, previsto)
        self.assertAlmostEqual(resultado['mae'], 7 / 3)
        self.assertAlmostEqual(resultado['rmse'], (17 / 3) ** 0.5)
        self.assertAlmostEqual(resultado['vies'], -1.0)

    def test_mase_compara_com_a_baseline(self):
        resultado = metrics.regressao([10, 20], [11, 21], [12, 22])
        self.assertAlmostEqual(resultado['mae'], 1.0)
        self.assertAlmostEqual(resultado['mae_baseline'], 2.0)
        self.assertAlmostEqual(resultado['mase'], 0.5)
        self.assertAlmostEqual(resultado['ganho_vs_baseline'], 50.0)

    def test_mape_ignora_dias_de_volume_zero(self):
        """Com real igual a zero o MAPE explodiria para infinito e contaminaria a média."""
        resultado = metrics.regressao([0, 10], [5, 11])
        self.assertAlmostEqual(resultado['mape'], 10.0)

    def test_classificacao_expoe_taxa_base_junto_da_auc(self):
        real = [0] * 95 + [1] * 5
        prob = [0.1] * 95 + [0.9] * 5
        resultado = metrics.classificacao(real, prob)
        self.assertEqual(resultado['taxa_base'], 0.05)
        self.assertEqual(resultado['recall'], 1.0)
        self.assertEqual(resultado['roc_auc'], 1.0)
        self.assertEqual(resultado['matriz_confusao']['vp'], 5)

    def test_auc_e_nula_quando_so_existe_uma_classe(self):
        resultado = metrics.classificacao([0, 0, 0], [0.1, 0.2, 0.3])
        self.assertIsNone(resultado['roc_auc'])
        self.assertIsNone(resultado['pr_auc'])

    def test_melhor_limiar_sai_da_validacao_e_nao_do_padrao(self):
        real = [0] * 90 + [1] * 10
        prob = [0.05] * 90 + [0.3] * 10
        limiar = metrics.melhor_limiar(real, prob)
        self.assertLess(limiar, 0.5)
        self.assertGreater(limiar, 0.05)


class BaselinesTest(TestCase):
    def test_baseline_sazonal_devolve_a_coluna_pedida(self):
        X = pd.DataFrame({'mesmo_dow_1': [5.0, 8.0], 'media_7': [1.0, 2.0]})
        modelo = models_zoo.PreverColuna('mesmo_dow_1').fit(X)
        self.assertEqual(list(modelo.predict(X)), [5.0, 8.0])

    def test_peso_para_desbalanceamento_e_a_razao_negativos_positivos(self):
        self.assertEqual(models_zoo.peso_para_desbalanceamento([0] * 99 + [1]), 99.0)
        self.assertEqual(models_zoo.peso_para_desbalanceamento([0, 0]), 1.0)

    def test_nome_do_algoritmo_atravessa_pipeline_e_calibrador(self):
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        pipeline = Pipeline([('e', StandardScaler()), ('m', LogisticRegression())])
        self.assertEqual(models_zoo.nome_algoritmo(pipeline), 'LogisticRegression')

        X = pd.DataFrame(np.random.rand(40, 2), columns=['a', 'b'])
        y = np.array([0, 1] * 20)
        calibrado = CalibratedClassifierCV(pipeline.fit(X, y), cv=2).fit(X, y)
        self.assertEqual(
            models_zoo.nome_algoritmo(calibrado), 'LogisticRegression (calibrado)',
        )


class TreinoTest(TestCase):
    """Um treino de verdade, pequeno, sobre série determinística.

    Não substitui o pipeline completo (que roda em `manage.py treinar_modelos` e nos notebooks),
    mas trava a fiação: competir escolhe pela validação, o vencedor é reajustado, e num padrão
    perfeitamente aprendível o modelo tem de bater a baseline sazonal. Se algum dia a seleção
    passar a devolver o candidato errado, ou o `set_params` parar de chegar ao Pipeline, este
    teste quebra em segundos em vez de a plataforma publicar previsão ruim em silêncio.
    """

    @classmethod
    def setUpTestData(cls):
        painel = features.painel_diario(_incidentes_sinteticos(dias=240), min_dias=60)
        cls.matriz = features.construir_features(painel, horizontes=[1])

    def test_modelo_vence_a_baseline_sazonal_em_serie_aprendivel(self):
        from sklearn.ensemble import RandomForestRegressor

        from ml.src import selecao

        treino, validacao, _ = splits.dividir(self.matriz)
        colunas = [c for c in features.colunas_de_features(self.matriz) if c != 'horizonte']
        X = self.matriz[colunas].astype(float).fillna(0.0)
        y = self.matriz['alvo'].astype(float)

        candidatos = {
            'Baseline sazonal': {
                'tipo': 'baseline',
                'estimador': lambda: models_zoo.PreverColuna('mesmo_dow_1'),
                'grade': [{}],
            },
            'Random Forest': {
                'tipo': 'arvore',
                'estimador': lambda: RandomForestRegressor(n_estimators=60, random_state=42),
                'grade': [{'min_samples_leaf': 2}],
            },
        }
        melhor, tabela = selecao.competir(
            candidatos, X[treino], y[treino], X[validacao], y[validacao],
            avaliar=selecao.avaliador_regressao(), chave_metrica='mae',
        )

        self.assertEqual(melhor['nome'], 'Random Forest')
        self.assertEqual(len(tabela), 2)
        self.assertTrue(melhor['linha']['vencedor'])

        previsto = melhor['modelo'].predict(X[validacao])
        self.assertLess(metrics.regressao(y[validacao], previsto)['mae'], 1.0)

    def test_set_params_chega_dentro_do_pipeline(self):
        """`modelo__alpha` precisa configurar o Ridge, e não explodir no construtor do Pipeline."""
        from ml.src import selecao

        definicao = models_zoo.candidatos_regressao()['Ridge']
        estimador = selecao._instanciar(definicao, {'modelo__alpha': 42.0})
        self.assertEqual(estimador.named_steps['modelo'].alpha, 42.0)


class PrevisaoRegistradaTest(TestCase):
    def _registro(self, **campos):
        padrao = dict(
            modelo='model_d1', algoritmo='XGBRegressor', dimensao='global', chave='',
            data_execucao=date(2025, 12, 30), data_alvo=date(2025, 12, 31), horizonte=1,
            valor_previsto=100.0, minimo=90.0, maximo=110.0,
        )
        padrao.update(campos)
        return PrevisaoRegistrada.objects.create(**padrao)

    def test_erro_so_existe_depois_que_o_real_chega(self):
        registro = self._registro()
        self.assertIsNone(registro.erro)
        self.assertIsNone(registro.dentro_da_faixa)
        self.assertTrue(registro.aguardando)

        registro.valor_real = 104.0
        self.assertAlmostEqual(registro.erro, 4.0)
        self.assertAlmostEqual(registro.erro_absoluto, 4.0)
        self.assertTrue(registro.dentro_da_faixa)

    def test_valor_fora_da_faixa_e_marcado(self):
        registro = self._registro(valor_real=130.0)
        self.assertFalse(registro.dentro_da_faixa)
        self.assertAlmostEqual(registro.erro_percentual, 30 / 130 * 100)

    def test_previsao_e_unica_por_execucao(self):
        from django.db.utils import IntegrityError

        self._registro()
        with self.assertRaises(IntegrityError):
            self._registro()


class SemModeloTreinadoTest(TesteDeTela):
    """Sem artefato, a plataforma diz que não há modelo — não improvisa um número.

    O caminho oposto (previsão fabricada por média quando o modelo falta) é o que o requisito de
    não simular resultados proíbe, e é fácil de reintroduzir sem querer num `except` largo.
    """

    def test_catalogo_vazio_nao_quebra_a_tela(self):
        resposta = self.client.get(reverse('ml:modelos'))
        self.assertEqual(resposta.status_code, 200)

    def test_historico_vazio_nao_quebra_a_tela(self):
        resposta = self.client.get(reverse('ml:previsoes'))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Sem histórico')

    def test_inferencia_devolve_nada_quando_o_artefato_nao_existe(self):
        if inferencia.disponivel('model_d1'):
            self.skipTest('Modelos treinados presentes — este teste cobre a ausência deles.')
        self.assertIsNone(inferencia.prever())
        self.assertIsNone(inferencia.risco_ola_global())
        self.assertIsNone(inferencia.anomalias())

    def test_detalhe_de_modelo_inexistente_devolve_404(self):
        self.assertEqual(self.client.get(reverse('ml:detalhe', args=['nao-existe'])).status_code, 404)


class CatalogoTest(TestCase):
    def test_metrica_principal_prefere_o_teste_sobre_a_validacao(self):
        cartao = {
            'nome': 'x', 'metrica_principal': 'mae', 'tipo': 'regressao',
            'metricas': {'validacao': {'mae': 9.0}, 'teste': {'mae': 5.0}},
        }
        enriquecido = catalogo.enriquecer(cartao)
        self.assertEqual(enriquecido['metrica_valor'], 5.0)
        self.assertEqual(enriquecido['metrica_particao'], 'teste')

    def test_metrica_ausente_vira_none_em_vez_de_zero(self):
        cartao = {'nome': 'x', 'metrica_principal': 'mae', 'tipo': 'regressao', 'metricas': {}}
        self.assertIsNone(catalogo.enriquecer(cartao)['metrica_valor'])
