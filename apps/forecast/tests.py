"""Testes do Forecast Engine.

O motor de previsão agora é o modelo treinado em `ml/` — o que sobrou neste app é a camada de
leitura: buscar a previsão, escrever a narrativa e montar o calendário. É isso que se testa aqui.

O comportamento **sem modelo treinado** é o caso mais importante: `ml/models/` não é
pré-requisito para rodar os testes nem para subir a aplicação, e o que a plataforma não pode
fazer é preencher o buraco com uma média e chamar de previsão.

As partes de ML propriamente ditas (features, partições temporais, métricas, ausência de
vazamento) são testadas em `apps/ml/tests.py`, sobre séries determinísticas.
"""

from datetime import date, timedelta

from apps.core.tests import (
    DATA_REFERENCIA, REFERENCIA, TesteComCache, criar_incidente, criar_serie_valores,
)
from apps.forecast import services
from apps.intelligence.services.base import DIM


def _previsao_falsa(valores=(40, 45, 50), mae=3.2, mae_baseline=4.0):
    """Bloco no formato que `inferencia.resumo_global` devolve, para testar só a narrativa."""
    previsoes = [
        {
            'data': DATA_REFERENCIA + timedelta(days=i + 1),
            'horizonte': i + 1,
            'dia_semana': 'Seg',
            'valor': valor,
            'minimo': valor - 5,
            'maximo': valor + 5,
            'modelo': 'model_d1',
            'algoritmo': 'XGBRegressor',
            'media_recente': 40.0,
            'probabilidade_pico': 0.8 if i == 2 else 0.1,
            'pico': i == 2,
        }
        for i, valor in enumerate(valores)
    ]
    return {
        'd1': previsoes[0],
        'previsoes': previsoes,
        'd7_total': sum(valores),
        'd7_min': sum(p['minimo'] for p in previsoes),
        'd7_max': sum(p['maximo'] for p in previsoes),
        'media_recente': 40.0,
        'variacao_d1': 0.0,
        'tendencia': 2.0,
        'pico': max(previsoes, key=lambda p: p['valor']),
        'origem': DATA_REFERENCIA,
        'explicacao': {
            'modelo': 'model_d1',
            'algoritmo': 'XGBRegressor',
            'valor_base': 38.0,
            'contribuicoes': [{
                'feature': 'media_7', 'descricao': 'média dos últimos 7 dias',
                'contribuicao': 4.2, 'valor': 41.0, 'sentido': 'aumenta',
            }],
        },
        'modelo': {
            'nome': 'model_d1',
            'algoritmo': 'XGBRegressor',
            'metricas': {
                'teste': {'mae': mae, 'mae_baseline': mae_baseline},
                'walk_forward': {
                    'janelas': 12, 'mae': mae, 'mae_baseline': mae_baseline,
                    'vitorias_sobre_baseline': 10,
                },
            },
        },
        'avaliacao': {},
    }


class SemModeloTest(TesteComCache):
    """Sem artefato treinado, nada de previsão — e nada de número improvisado no lugar."""

    def test_resumo_devolve_nada(self):
        criar_serie_valores([100] * 120)
        criar_incidente(REFERENCIA)
        self.assertIsNone(services.resumo())

    def test_calendario_e_picos_ficam_vazios(self):
        criar_serie_valores([100] * 120)
        criar_incidente(REFERENCIA)
        self.assertEqual(services.calendario_risco(), [])
        self.assertEqual(services.picos(), [])

    def test_interpretacao_de_nada_e_string_vazia(self):
        self.assertEqual(services.interpretar(None), '')


class NarrativaTest(TesteComCache):
    """A leitura em texto fala com a operação: incerteza e comparação sem jargão de modelo."""

    # A operação não lê nome de algoritmo nem "walk-forward" — esses termos só valem em /modelos/.
    JARGAO = ('XGBRegressor', 'LGBMRegressor', 'walk-forward', 'baseline', 'MAE', 'RMSE',
              'classificador', 'feature', 'SHAP')

    def test_cita_faixa_e_a_comparacao_sem_jargao(self):
        texto = services.interpretar(_previsao_falsa())

        self.assertIn('faixa provável', texto)
        self.assertIn('semanas seguidas', texto)
        self.assertIn('mesmo dia da semana anterior', texto)

    def test_narrativa_nao_usa_vocabulario_tecnico(self):
        texto = services.interpretar(_previsao_falsa(valores=(40, 45, 70)))

        for termo in self.JARGAO:
            self.assertNotIn(termo, texto, f'{termo!r} não pode aparecer na leitura da operação')

    def test_cita_o_dia_de_maior_pressao(self):
        texto = services.interpretar(_previsao_falsa(valores=(40, 45, 70)))

        self.assertIn('maior pressão', texto)
        self.assertIn('80%', texto)

    def test_cita_o_fator_que_mais_pesou_na_previsao(self):
        texto = services.interpretar(_previsao_falsa())

        self.assertIn('média dos últimos 7 dias', texto)
        self.assertIn('aumenta', texto)

    def test_nao_promete_ganho_quando_o_modelo_perde_da_baseline(self):
        """Modelo pior que a baseline tem de aparecer como tal, não sumir do texto."""
        texto = services.interpretar(_previsao_falsa(mae=5.0, mae_baseline=4.0))

        self.assertIn('5.0', texto)
        self.assertIn('4.0', texto)


class TendenciaTest(TesteComCache):
    """`tendencia_por_dimensao` é descritivo e continua valendo sem modelo nenhum."""

    def test_compara_duas_metades_do_periodo(self):
        criar_serie_valores([10] * 14 + [20] * 14, dimensao=DIM.FAMILIA, chave='disco')
        criar_incidente(REFERENCIA)

        linhas = services.tendencia_por_dimensao(DIM.FAMILIA, dias=28)

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['chave'], 'disco')
        self.assertEqual(linhas[0]['recentes'], 20 * 14)
        self.assertEqual(linhas[0]['variacao'], 100.0)

    def test_sem_base_de_comparacao_a_variacao_e_nula(self):
        criar_serie_valores([0] * 14 + [5] * 14, dimensao=DIM.FAMILIA, chave='disco')
        criar_incidente(REFERENCIA)

        linhas = services.tendencia_por_dimensao(DIM.FAMILIA, dias=28)

        self.assertIsNone(linhas[0]['variacao'])
