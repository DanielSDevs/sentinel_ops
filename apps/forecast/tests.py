"""Testes do Forecast Engine.

Os cenários usam números redondos de propósito: com nível 100 e perfil plano, a previsão de
qualquer dia tem que dar 100, e o valor esperado pode ser conferido na mão.
"""

from datetime import timedelta

from apps.core.tests import DATA_REFERENCIA, TesteComCache, criar_incidente, criar_serie_valores
from apps.core.tests import REFERENCIA
from apps.forecast import services


def _serie_constante(dias, valor):
    criar_serie_valores([valor] * dias)
    criar_incidente(REFERENCIA)


def _serie_por_dia_semana(dias, util, fim_de_semana, ruido=0):
    """Série terminando na data de referência, com valor definido pelo dia da semana de cada data.

    `ruido` alterna ±ruido nos dias úteis, para os testes que precisam de erro diferente de zero.
    """
    datas = [DATA_REFERENCIA - timedelta(days=dias - 1 - i) for i in range(dias)]
    valores = [
        (fim_de_semana if data.weekday() >= 5 else util + (ruido if i % 2 else -ruido))
        for i, data in enumerate(datas)
    ]
    criar_serie_valores(valores)
    criar_incidente(REFERENCIA)


class ModeloTest(TesteComCache):
    def test_serie_constante_preve_o_mesmo_valor(self):
        _serie_constante(120, 100)

        previsoes = services.prever(7)

        self.assertEqual([p['valor'] for p in previsoes], [100] * 7)

    def test_perfil_separa_dia_forte_de_dia_fraco(self):
        # 100 nos dias úteis, 20 no fim de semana — montado a partir do dia da semana de cada
        # data, não de um padrão repetido, que sairia do lugar se o tamanho da série mudasse.
        _serie_por_dia_semana(140, util=100, fim_de_semana=20)

        por_dia = {p['data'].weekday(): p['valor'] for p in services.prever(7)}

        self.assertEqual(por_dia[0], 100)
        self.assertEqual(por_dia[5], 20)

    def test_nivel_recente_domina_o_historico_antigo(self):
        """Mudança de patamar: o modelo antigo diluía a queda na média de 90 dias."""
        criar_serie_valores([200] * 100 + [50] * 20)
        criar_incidente(REFERENCIA)

        valores = [p['valor'] for p in services.prever(7)]

        # Nível vem dos últimos 14 dias (50) e o termo de repetição também aponta 50.
        self.assertTrue(all(45 <= v <= 60 for v in valores), valores)

    def test_previsao_nunca_e_negativa(self):
        criar_serie_valores([0] * 100 + [5] * 20)
        criar_incidente(REFERENCIA)

        self.assertTrue(all(p['valor'] >= 0 and p['minimo'] >= 0 for p in services.prever(7)))

    def test_faixa_envolve_o_valor_previsto(self):
        criar_serie_valores([100, 80, 120, 90, 110, 30, 25] * 25)
        criar_incidente(REFERENCIA)

        for p in services.prever(7):
            self.assertLessEqual(p['minimo'], p['valor'])
            self.assertGreaterEqual(p['maximo'], p['valor'])


class BacktestTest(TesteComCache):
    def test_serie_previsivel_tem_erro_proximo_de_zero(self):
        _serie_constante(200, 100)

        avaliacao = services.backtest()

        self.assertEqual(avaliacao['mae'], 0)
        self.assertEqual(avaliacao['vies'], 0)

    def test_agrega_varias_janelas_em_vez_de_um_holdout_unico(self):
        _serie_constante(200, 100)

        avaliacao = services.backtest(janelas=6, horizonte=7)

        self.assertEqual(avaliacao['janelas'], 6)
        self.assertEqual(avaliacao['dias_teste'], 42)
        self.assertEqual(len(avaliacao['resumo_janelas']), 6)

    def test_cada_janela_treina_apenas_com_o_proprio_passado(self):
        """Sem vazamento: um degrau só no fim da série não pode ser previsto antes de acontecer."""
        criar_serie_valores([100] * 180 + [900] * 7)
        criar_incidente(REFERENCIA)

        avaliacao = services.backtest(janelas=3, horizonte=7)

        # A última janela cai sobre o degrau e erra feio; as anteriores, não.
        maes = [j['mae'] for j in avaliacao['resumo_janelas']]
        self.assertEqual(maes[0], 0)
        self.assertGreater(maes[-1], 500)

    def test_janela_sem_historico_e_descartada(self):
        """Base curta não deve inventar janelas treinadas em zeros preenchidos."""
        criar_serie_valores([100] * 40)
        criar_incidente(REFERENCIA)

        avaliacao = services.backtest(janelas=12, horizonte=7)

        self.assertIsNotNone(avaliacao)
        self.assertLess(avaliacao['janelas'], 12)

    def test_sem_dados_nao_quebra(self):
        criar_incidente(REFERENCIA)

        self.assertIsNone(services.backtest())
        self.assertEqual(services.prever(7), [])
        self.assertIsNone(services.resumo())

    def test_margem_por_dia_semana_respeita_o_tamanho_do_dia(self):
        """Sábado de 20 incidentes não pode receber a mesma faixa que uma quarta de 100±30."""
        _serie_por_dia_semana(200, util=100, fim_de_semana=20, ruido=30)

        margens = services.backtest()['margens']

        self.assertIn('geral', margens)
        self.assertLess(margens[5], margens[2])


class InterpretacaoTest(TesteComCache):
    def test_cita_o_numero_de_janelas_e_a_comparacao_com_a_baseline(self):
        # Precisa de erro diferente de zero: numa série perfeita a baseline acerta em cheio e
        # não há razão contra a qual comparar.
        _serie_por_dia_semana(200, util=100, fim_de_semana=25, ruido=20)

        texto = services.interpretar(services.resumo())

        self.assertIn('walk-forward', texto)
        self.assertIn('janelas', texto)

    def test_sem_previsao_devolve_texto_vazio(self):
        self.assertEqual(services.interpretar(None), '')
