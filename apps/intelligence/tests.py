"""Testes da camada de inferência.

Cada cenário é montado com números escolhidos para que o resultado esperado possa ser conferido
na mão — é isso que permite afirmar que o Health Score perdeu exatamente 30 pontos por OLA, ou
que o Risk Radar colocou um produto de volume baixo à frente de um de volume alto.

As fábricas vêm de `apps.core.tests`, para que todos os apps montem a base do mesmo jeito.
"""

from datetime import timedelta

from django.urls import reverse

from apps.core.models import Incidente
from apps.core.tests import (
    DATA_REFERENCIA,
    DIM,
    REFERENCIA,
    TesteComCache,
    criar_ativo,
    criar_equipe,
    criar_familia,
    criar_incidente,
    criar_metrica,
    criar_produto,
    criar_serie,
    criar_serie_valores,
    montar_operacao,
)
from apps.intelligence.services import (
    anomaly,
    base,
    briefing,
    correlation,
    deltas,
    health,
    insights,
    risk,
    similarity,
)


class BaseTest(TesteComCache):
    """As funções de `base` são a fundação numérica de todo o resto."""

    def setUp(self):
        super().setUp()
        criar_incidente(REFERENCIA)  # ancora a data de referência

    def test_serie_diaria_preenche_dias_sem_registro_com_zero(self):
        criar_metrica(DATA_REFERENCIA, total_kpi=5)
        criar_metrica(DATA_REFERENCIA - timedelta(days=3), total_kpi=2)

        serie = base.serie_diaria(5, campo='total_kpi')

        self.assertEqual(len(serie), 5)
        self.assertEqual(serie[-1], (DATA_REFERENCIA, 5))
        self.assertEqual([valor for _, valor in serie], [0, 2, 0, 0, 5])

    def test_agregado_ignora_o_que_esta_fora_da_janela(self):
        criar_serie(7, total=10, total_kpi=4)
        criar_metrica(DATA_REFERENCIA - timedelta(days=30), total=999, total_kpi=999)

        resultado = base.agregado(7)

        self.assertEqual(resultado['total'], 70)
        self.assertEqual(resultado['total_kpi'], 28)

    def test_agregado_por_chave_respeita_volume_minimo(self):
        criar_serie(7, DIM.PRODUTO, 'grande', total=10)
        criar_serie(7, DIM.PRODUTO, 'pequeno', total=1)

        chaves = {l['chave'] for l in base.agregado_por_chave(7, DIM.PRODUTO, minimo_total=20)}

        self.assertEqual(chaves, {'grande'})

    def test_periodo_e_anterior_nao_se_sobrepoem(self):
        criar_serie(7, total_kpi=10)
        criar_serie(7, ate=DATA_REFERENCIA - timedelta(days=7), total_kpi=5)

        atual, anterior = base.periodo_e_anterior(7)

        self.assertEqual(atual['total_kpi'], 70)
        self.assertEqual(anterior['total_kpi'], 35)

    def test_variacao_percentual_sem_base_de_comparacao_e_none(self):
        self.assertEqual(base.variacao_percentual(120, 100), 20.0)
        self.assertEqual(base.variacao_percentual(50, 100), -50.0)
        self.assertIsNone(base.variacao_percentual(10, 0))

    def test_estatisticas_basicas(self):
        self.assertEqual(base.media([]), 0.0)
        self.assertEqual(base.media([1, 2, 3]), 2.0)
        self.assertEqual(base.desvio_padrao([7]), 0.0)
        self.assertAlmostEqual(base.desvio_padrao([1, 2, 3, 4, 5]), 1.5811, places=3)
        self.assertEqual(base.taxa(1, 0), 0.0)
        self.assertEqual(base.taxa(1, 4), 0.25)


class HealthTest(TesteComCache):
    def setUp(self):
        super().setUp()
        criar_incidente(REFERENCIA)

    def _operacao_estavel(self, **ajustes):
        """28 dias idênticos: nenhum fator tem do que reclamar."""
        valores = {'total': 20, 'total_kpi': 20, 'violacoes': 0, 'criticos': 0,
                   'resolvidos': 20, 'soma_mttr_seg': 20 * 600}
        valores.update(ajustes)
        criar_serie(health.JANELA_BASELINE, **valores)

    def test_operacao_estavel_nao_perde_pontos(self):
        self._operacao_estavel()

        resultado = health.calcular()

        self.assertEqual(resultado.score, 100)
        self.assertEqual(resultado.faixa, 'healthy')
        self.assertIsNone(resultado.principal_ofensor)

    def test_violacao_no_limiar_custa_o_peso_inteiro_do_fator(self):
        # 1 violação em 20 elegíveis por dia = 5%, exatamente o limiar de penalidade máxima.
        self._operacao_estavel(violacoes=1)

        resultado = health.calcular()
        ofensor = resultado.principal_ofensor

        self.assertEqual(ofensor.slug, 'ola')
        self.assertEqual(ofensor.pontos_perdidos, 30)
        self.assertEqual(resultado.score, 70)
        self.assertEqual(resultado.faixa, 'attention')

    def test_cada_fator_explica_o_proprio_numero(self):
        self._operacao_estavel(violacoes=1, criticos=10)

        resultado = health.calcular()

        self.assertEqual({f.slug for f in resultado.fatores},
                         {'ola', 'volume', 'severidade', 'tendencia', 'mttr'})
        for fator in resultado.fatores:
            with self.subTest(fator=fator.slug):
                self.assertTrue(fator.explicacao)
                self.assertTrue(fator.detalhe_calculo)
                self.assertTrue(fator.valor_exibido)

    def test_fatores_vem_ordenados_pelo_que_mais_pesa(self):
        self._operacao_estavel(violacoes=1, criticos=20)

        ordenados = health.calcular().fatores_ordenados

        pontos = [f.pontos_perdidos for f in ordenados]
        self.assertEqual(pontos, sorted(pontos, reverse=True))
        self.assertEqual(ordenados[0].slug, 'ola')

    def test_score_fica_entre_0_e_100_no_pior_cenario(self):
        # Tudo ruim ao mesmo tempo: violação total, só críticos, volume e MTTR explodindo.
        criar_serie(health.JANELA_BASELINE, ate=DATA_REFERENCIA - timedelta(days=7),
                    total=10, total_kpi=10, violacoes=0, criticos=0,
                    resolvidos=10, soma_mttr_seg=10 * 60)
        criar_serie(7, total=100, total_kpi=100, violacoes=100, criticos=100,
                    resolvidos=100, soma_mttr_seg=100 * 6000)

        resultado = health.calcular()

        self.assertGreaterEqual(resultado.score, 0)
        self.assertLess(resultado.score, 50)
        self.assertEqual(resultado.faixa, 'critical')

    def test_sem_dado_nenhum_o_score_nao_quebra(self):
        resultado = health.calcular()

        self.assertEqual(resultado.score, 100)
        self.assertIsNone(resultado.variacao)


class DeltasTest(TesteComCache):
    def setUp(self):
        super().setUp()
        criar_incidente(REFERENCIA)

    def _dois_periodos(self, atual, anterior):
        criar_serie(7, **atual)
        criar_serie(7, ate=DATA_REFERENCIA - timedelta(days=7), **anterior)
        return {d.slug: d for d in deltas.calcular(7)}

    def test_volume_elegivel_subindo_e_lido_como_piora(self):
        calculados = self._dois_periodos(
            atual={'total': 20, 'total_kpi': 10},
            anterior={'total': 20, 'total_kpi': 5},
        )
        volume = calculados['volume_kpi']

        self.assertEqual(volume.variacao, 100.0)
        self.assertEqual(volume.direcao, 'subiu')
        self.assertEqual(volume.sentimento, 'ruim')
        self.assertEqual(volume.seta, '↑')

    def test_mttr_caindo_e_lido_como_melhora(self):
        calculados = self._dois_periodos(
            atual={'total_kpi': 10, 'resolvidos': 10, 'soma_mttr_seg': 10 * 600},
            anterior={'total_kpi': 10, 'resolvidos': 10, 'soma_mttr_seg': 10 * 1200},
        )
        mttr = calculados['mttr']

        self.assertEqual(mttr.valor_atual, '10 min')
        self.assertEqual(mttr.variacao, -50.0)
        self.assertEqual(mttr.sentimento, 'bom')

    def test_variacao_pequena_fica_estavel_e_neutra(self):
        calculados = self._dois_periodos(
            atual={'total': 100, 'total_kpi': 100},
            anterior={'total': 100, 'total_kpi': 99},
        )
        volume = calculados['volume_kpi']

        self.assertEqual(volume.direcao, 'estavel')
        self.assertEqual(volume.sentimento, 'neutro')
        self.assertEqual(volume.seta, '→')

    def test_violacao_em_numero_absoluto_baixo_vem_com_ressalva(self):
        # 2 violações contra 1: +100% na taxa, mas a base é pequena demais para alarmar.
        criar_serie(6, total_kpi=100, violacoes=0)
        criar_metrica(DATA_REFERENCIA - timedelta(days=6), total_kpi=100, violacoes=2)
        criar_serie(6, ate=DATA_REFERENCIA - timedelta(days=7), total_kpi=100, violacoes=0)
        criar_metrica(DATA_REFERENCIA - timedelta(days=13), total_kpi=100, violacoes=1)

        ola = {d.slug: d for d in deltas.calcular(7)}['ola']

        self.assertEqual(ola.variacao, 100.0)
        self.assertIn('oscila muito', ola.explicacao)

    def test_ruido_e_contexto_e_nunca_alarme(self):
        calculados = self._dois_periodos(
            atual={'total': 20, 'total_kpi': 5},
            anterior={'total': 20, 'total_kpi': 10},
        )
        ruido = calculados['ruido']

        self.assertEqual(ruido.sentimento, 'neutro')
        self.assertIn('75%', ruido.valor_atual)


class RiskTest(TesteComCache):
    """O ponto central do Risk Radar: risco ≠ volume."""

    def setUp(self):
        super().setUp()
        criar_incidente(REFERENCIA)
        # 'violador': volume baixo, mas 50% dos elegíveis violaram OLA no histórico.
        criar_serie(risk.JANELA_HISTORICO, DIM.PRODUTO, 'violador',
                    total=4, total_kpi=2, violacoes=1, resolvidos=2, soma_mttr_seg=2 * 600)
        # 'volumoso': 5x mais incidentes, nenhuma violação.
        criar_serie(risk.JANELA_HISTORICO, DIM.PRODUTO, 'volumoso',
                    total=20, total_kpi=10, violacoes=0, resolvidos=10, soma_mttr_seg=10 * 600)

    def test_ordena_por_risco_e_nao_por_volume(self):
        riscos = {r.chave: r for r in risk.calcular(DIM.PRODUTO)}
        ordem = [r.chave for r in risk.calcular(DIM.PRODUTO)]

        self.assertEqual(ordem[0], 'violador')
        self.assertGreater(riscos['violador'].score, riscos['volumoso'].score)
        self.assertLess(riscos['violador'].volume_kpi, riscos['volumoso'].volume_kpi)

    def test_probabilidade_e_a_taxa_historica_de_violacao(self):
        violador = next(r for r in risk.calcular(DIM.PRODUTO) if r.chave == 'violador')

        self.assertAlmostEqual(violador.probabilidade, 0.5)
        self.assertEqual(violador.faixa, 'critical')
        self.assertEqual(violador.rotulo_faixa, 'Crítico')

    def test_amostra_pequena_fica_de_fora(self):
        # 5 dias × 2 elegíveis = 10 < VOLUME_MINIMO: não dá para estimar probabilidade.
        criar_serie(5, DIM.PRODUTO, 'novato', total=4, total_kpi=2, violacoes=2)

        chaves = {r.chave for r in risk.calcular(DIM.PRODUTO)}

        self.assertNotIn('novato', chaves)

    def test_drivers_e_acao_citam_a_evidencia(self):
        violador = next(r for r in risk.calcular(DIM.PRODUTO) if r.chave == 'violador')

        self.assertTrue(any('violações de OLA' in d for d in violador.drivers))
        self.assertIn('violador', violador.acao)

    def test_panorama_cobre_as_tres_dimensoes(self):
        self.assertEqual(set(risk.panorama()), {'produtos', 'equipes', 'familias'})


class AnomalyTest(TesteComCache):
    def setUp(self):
        super().setUp()
        criar_incidente(REFERENCIA)

    def test_pico_isolado_vira_anomalia_com_faixa_esperada(self):
        # 50 dias oscilando entre 9 e 11 (média 10) e um pico de 30 no dia de referência.
        criar_serie_valores([9, 11] * 25 + [30])

        _timeline, anomalias = anomaly.detectar()
        pico = anomalias[-1]

        self.assertEqual(pico.data, DATA_REFERENCIA)
        self.assertEqual(pico.observado, 30)
        self.assertGreaterEqual(pico.z, anomaly.Z_ANOMALIA)
        self.assertEqual(pico.confianca, 99)
        self.assertEqual(pico.faixa, 'critical')
        self.assertEqual(pico.rotulo, 'Anomalia')
        self.assertLessEqual(pico.esperado_min, 10)
        self.assertGreaterEqual(pico.esperado_max, 10)
        self.assertGreater(pico.desvio_pct, 100)

    def test_serie_estavel_nao_gera_anomalia(self):
        criar_serie_valores([10, 11, 9, 10, 11, 9] * 8)

        timeline, anomalias = anomaly.detectar()

        self.assertEqual(anomalias, [])
        self.assertTrue(all(ponto.estado == 'normal' for ponto in timeline))

    def test_sem_baseline_suficiente_nada_e_julgado(self):
        # Menos de 7 dias anteriores: a série não tem do que ser desvio, nem o pico é julgado.
        serie = [(DATA_REFERENCIA - timedelta(days=4 - i), v)
                 for i, v in enumerate([1, 2, 3, 4, 500])]

        timeline, anomalias = anomaly._analisar_serie(serie, DIM.GLOBAL, '')

        self.assertEqual(anomalias, [])
        self.assertTrue(all(ponto.z == 0.0 for ponto in timeline))

    def test_timeline_devolve_a_janela_de_analise(self):
        criar_serie_valores([10] * (anomaly.JANELA_ANALISE + anomaly.JANELA_BASELINE))

        timeline, _ = anomaly.detectar()

        self.assertEqual(len(timeline), anomaly.JANELA_ANALISE)
        self.assertEqual(timeline[-1].data, DATA_REFERENCIA)

    def test_varredura_por_dimensao_ignora_chave_de_baixo_volume(self):
        criar_serie_valores([9, 11] * 25 + [60], DIM.FAMILIA, 'disco')
        criar_serie_valores([0, 1] * 25 + [3], DIM.FAMILIA, 'ruidinho')

        chaves = {a.chave for a in anomaly.detectar_por_dimensao(DIM.FAMILIA)}

        self.assertIn('disco', chaves)
        self.assertNotIn('ruidinho', chaves)


class CorrelationTest(TesteComCache):
    """Blast radius e cadeia de sintomas — as duas evidências reais do dataset."""

    def setUp(self):
        super().setUp()
        self.equipe = criar_equipe('Team01')
        self.outra_equipe = criar_equipe('Team02')
        self.produto = criar_produto('prod1')
        self.outro_produto = criar_produto('prod2')
        self.ativo = criar_ativo('ci1')
        self.outro_ativo = criar_ativo('ci2')
        self.disco = criar_familia('disco', 'Disco')
        self.web = criar_familia('web', 'Web / HTTP')
        self.rede = criar_familia('rede', 'Rede')

    def _tempestade(self, quantidade, momento=None, **campos):
        pai = criar_incidente(momento or REFERENCIA - timedelta(hours=2),
                              produto=self.produto, equipe=self.equipe, **campos)
        for i in range(quantidade):
            criar_incidente(
                pai.aberto_em + timedelta(minutes=i + 1),
                produto=self.outro_produto if i == 0 else self.produto,
                equipe=self.outra_equipe if i == 0 else self.equipe,
                familia_sinal=self.web if i == 0 else self.disco,
                incidente_pai=pai,
            )
        return pai

    def test_tempestades_ordenam_pelo_tamanho_do_desdobramento(self):
        maior = self._tempestade(4)
        menor = self._tempestade(2, momento=REFERENCIA - timedelta(hours=5))

        resultado = correlation.tempestades(limite=5)

        self.assertEqual([t.pai.numero for t in resultado], [maior.numero, menor.numero])
        self.assertEqual([t.total_filhos for t in resultado], [4, 2])

    def test_tempestade_resume_janela_e_participantes(self):
        pai = self._tempestade(3)

        tempestade = correlation.tempestades(limite=1)[0]

        self.assertEqual(tempestade.inicio, pai.aberto_em)
        self.assertEqual(tempestade.fim, pai.aberto_em + timedelta(minutes=3))
        self.assertEqual(dict(tempestade.produtos), {'prod2': 1, 'prod1': 2})
        self.assertEqual(dict(tempestade.equipes), {'Team02': 1, 'Team01': 2})
        self.assertEqual(dict(tempestade.familias), {'Web / HTTP': 1, 'Disco': 2})
        self.assertEqual(tempestade.duracao_horas, 0.1)

    def test_limite_de_tempestades_e_respeitado(self):
        for i in range(4):
            self._tempestade(2, momento=REFERENCIA - timedelta(hours=3 + i))

        self.assertEqual(len(correlation.tempestades(limite=2)), 2)

    def test_janela_em_dias_filtra_pela_data_do_incidente_raiz(self):
        recente = self._tempestade(2)
        self._tempestade(3, momento=REFERENCIA - timedelta(days=40))

        resultado = correlation.tempestades(limite=5, dias=7)

        self.assertEqual([t.pai.numero for t in resultado], [recente.numero])

    def test_tempestades_nao_disparam_uma_consulta_por_filho(self):
        """Regressão: `pai.filhos` sobre instâncias parciais fazia um SELECT por desdobramento."""
        self._tempestade(30)

        with self.assertNumQueries(3):
            correlation.tempestades(limite=5)

    def test_cadeia_conta_pares_na_janela_do_mesmo_ativo(self):
        inicio = REFERENCIA - timedelta(hours=1)
        # Mesmo ativo: disco → disco (mesma família, não conta) → web, dentro de 60 min.
        criar_incidente(inicio, item_configuracao=self.ativo, familia_sinal=self.disco)
        criar_incidente(inicio + timedelta(minutes=5), item_configuracao=self.ativo,
                        familia_sinal=self.disco)
        criar_incidente(inicio + timedelta(minutes=10), item_configuracao=self.ativo,
                        familia_sinal=self.web)
        # Outro ativo: par fora da janela de 60 min.
        criar_incidente(inicio, item_configuracao=self.outro_ativo, familia_sinal=self.disco)
        criar_incidente(inicio + timedelta(minutes=90), item_configuracao=self.outro_ativo,
                        familia_sinal=self.rede)

        cadeias = correlation.cadeia_de_sintomas()

        self.assertEqual(cadeias, [
            {'de': 'Disco', 'para': 'Web / HTTP', 'ocorrencias': 2, 'participacao': 100.0},
        ])

    def test_cadeia_nao_cruza_itens_de_configuracao_diferentes(self):
        inicio = REFERENCIA - timedelta(hours=1)
        criar_incidente(inicio, item_configuracao=self.ativo, familia_sinal=self.disco)
        criar_incidente(inicio + timedelta(minutes=5), item_configuracao=self.outro_ativo,
                        familia_sinal=self.web)

        self.assertEqual(correlation.cadeia_de_sintomas(), [])

    def test_cadeia_ignora_incidentes_fora_do_periodo(self):
        antigo = REFERENCIA - timedelta(days=120)
        criar_incidente(antigo, item_configuracao=self.ativo, familia_sinal=self.disco)
        criar_incidente(antigo + timedelta(minutes=5), item_configuracao=self.ativo,
                        familia_sinal=self.web)
        criar_incidente(REFERENCIA)  # mantém a referência temporal no presente

        self.assertEqual(correlation.cadeia_de_sintomas(dias=90), [])

    def test_cadeia_e_servida_do_cache_na_segunda_chamada(self):
        inicio = REFERENCIA - timedelta(hours=1)
        criar_incidente(inicio, item_configuracao=self.ativo, familia_sinal=self.disco)
        criar_incidente(inicio + timedelta(minutes=5), item_configuracao=self.ativo,
                        familia_sinal=self.web)

        primeira = correlation.cadeia_de_sintomas()
        with self.assertNumQueries(1):  # só a contagem que versiona a chave
            segunda = correlation.cadeia_de_sintomas()

        self.assertEqual(primeira, segunda)

    def test_incidente_novo_invalida_o_cache_da_cadeia(self):
        """A simulação escreve sem passar por hook nenhum — o cache não pode segurar o passado."""
        inicio = REFERENCIA - timedelta(hours=1)
        criar_incidente(inicio, item_configuracao=self.ativo, familia_sinal=self.disco)
        criar_incidente(inicio + timedelta(minutes=5), item_configuracao=self.ativo,
                        familia_sinal=self.web)
        antes = correlation.cadeia_de_sintomas()

        criar_incidente(inicio + timedelta(minutes=10), item_configuracao=self.ativo,
                        familia_sinal=self.rede)
        depois = correlation.cadeia_de_sintomas()

        self.assertEqual(antes[0]['ocorrencias'], 1)
        self.assertGreater(len(depois), len(antes))

    def test_grafo_liga_a_raiz_a_cada_familia(self):
        self._tempestade(3)

        grafo = correlation.grafo_tempestade(correlation.tempestades(limite=1)[0])

        self.assertEqual(grafo['nos'][0]['tipo'], 'raiz')
        self.assertEqual(len(grafo['nos']), 1 + len(grafo['arestas']))
        self.assertTrue(all(a['de'] == 'raiz' for a in grafo['arestas']))


class SimilarityTest(TesteComCache):
    def setUp(self):
        super().setUp()
        self.produto = criar_produto('prod1')
        self.familia = criar_familia('disco', 'Disco')
        self.comuns = {
            'produto': self.produto, 'familia_sinal': self.familia,
            'categoria': 'infra', 'subcategoria': 'disco', 'prioridade': 3,
        }
        self.referencia = criar_incidente(REFERENCIA, **self.comuns)

    def test_similaridade_e_a_soma_dos_pesos_que_casaram(self):
        gemeo = criar_incidente(REFERENCIA - timedelta(days=1), **self.comuns)

        pontos, iguais = similarity._pontuar(self.referencia, gemeo)

        self.assertEqual(pontos, 100)
        self.assertEqual(len(iguais), len(similarity.PESOS))

    def test_so_conta_como_vizinho_quem_veio_antes(self):
        anterior = criar_incidente(REFERENCIA - timedelta(days=1), **self.comuns)
        criar_incidente(REFERENCIA + timedelta(days=1), **self.comuns)

        vizinhos = similarity.semelhantes(self.referencia)

        self.assertEqual([v.incidente.numero for v in vizinhos], [anterior.numero])
        self.assertEqual(vizinhos[0].similaridade, 100)

    def test_parecido_demais_de_leve_fica_de_fora(self):
        # Só a família de sinal em comum: 15 pontos, abaixo do corte de 40.
        criar_incidente(REFERENCIA - timedelta(days=1), familia_sinal=self.familia,
                        categoria='rede', subcategoria='switch', prioridade=1)

        self.assertEqual(similarity.semelhantes(self.referencia), [])

    def test_perfil_resume_o_que_o_historico_diz(self):
        for minutos, fechamento in ((30, 'Limpeza de log'), (60, 'Limpeza de log'), (90, 'Reinício')):
            criar_incidente(
                REFERENCIA - timedelta(days=1), **self.comuns,
                resolvido_em=REFERENCIA - timedelta(days=1, minutes=-minutos),
                cod_fechamento=fechamento, solucao=Incidente.Solucao.DEFINITIVA,
                kpi_violado=(minutos == 90),
            )
        vizinhos = similarity.semelhantes(self.referencia)

        perfil = similarity.perfil_resolucao(self.referencia, vizinhos)

        self.assertEqual(perfil['amostra'], 3)
        self.assertEqual(perfil['mttr_mediano'], 60)
        self.assertEqual(perfil['fechamento_frequente'], ('Limpeza de log', 2))
        self.assertEqual(perfil['violacoes'], 1)
        self.assertAlmostEqual(perfil['taxa_violacao'], 33.3)

    def test_hipotese_de_causa_cita_o_fechamento_mais_comum(self):
        criar_incidente(REFERENCIA - timedelta(days=1), **self.comuns,
                        cod_fechamento='Limpeza de log')
        vizinhos = similarity.semelhantes(self.referencia)
        perfil = similarity.perfil_resolucao(self.referencia, vizinhos)

        self.assertIn('Limpeza de log', similarity.hipotese_causa(self.referencia, perfil))

    def test_sem_vizinhos_nao_ha_perfil_nem_hipotese(self):
        self.assertIsNone(similarity.perfil_resolucao(self.referencia, []))
        self.assertIsNone(similarity.hipotese_causa(self.referencia, None))


class InsightsTest(TesteComCache):
    def setUp(self):
        super().setUp()
        criar_incidente(REFERENCIA)

    def test_crescimento_relevante_de_volume_vira_insight(self):
        criar_serie(7, total=100, total_kpi=100)
        criar_serie(7, ate=DATA_REFERENCIA - timedelta(days=7), total=100, total_kpi=50)

        slugs = {i.slug for i in insights.gerar()}

        self.assertIn('volume-acima-baseline', slugs)

    def test_crescimento_pequeno_nao_polui_a_tela(self):
        criar_serie(7, total=100, total_kpi=100)
        criar_serie(7, ate=DATA_REFERENCIA - timedelta(days=7), total=100, total_kpi=95)

        slugs = {i.slug for i in insights.gerar()}

        self.assertNotIn('volume-acima-baseline', slugs)

    def test_ruido_dominante_vira_insight_de_eficiencia(self):
        criar_serie(14, total=100, total_kpi=20)

        gerados = {i.slug: i for i in insights.gerar()}

        self.assertIn('ruido-monitoramento', gerados)
        self.assertEqual(gerados['ruido-monitoramento'].tipo, 'eficiencia')
        self.assertIn('80%', gerados['ruido-monitoramento'].titulo)

    def test_prioridade_e_o_produto_dos_quatro_fatores(self):
        criar_serie(14, total=100, total_kpi=20)

        insight = next(i for i in insights.gerar() if i.slug == 'ruido-monitoramento')
        esperado = round(insight.impacto * insight.urgencia * insight.confianca * insight.alcance * 100)

        self.assertEqual(insight.prioridade, esperado)
        self.assertEqual(insight.confianca_pct, round(insight.confianca * 100))

    def test_saida_vem_ordenada_e_limitada(self):
        criar_serie(7, total=100, total_kpi=100, violacoes=10, criticos=50,
                    resolvidos=50, soma_mttr_seg=50 * 600)
        criar_serie(7, ate=DATA_REFERENCIA - timedelta(days=7), total=100, total_kpi=40)

        gerados = insights.gerar(limite=2)
        prioridades = [i.prioridade for i in gerados]

        self.assertLessEqual(len(gerados), 2)
        self.assertEqual(prioridades, sorted(prioridades, reverse=True))

    def test_operacao_sem_dados_nao_inventa_insight(self):
        self.assertEqual(insights.gerar(), [])


class BriefingTest(TesteComCache):
    def setUp(self):
        super().setUp()
        criar_incidente(REFERENCIA)

    def test_operacao_estavel_e_relatada_como_estavel(self):
        criar_serie(28, total=20, total_kpi=20, resolvidos=20, soma_mttr_seg=20 * 600)

        resultado = briefing.montar()

        self.assertIn('Nenhum indicador-chave piorou', resultado.resumo)
        self.assertEqual(len(resultado.o_que_mudou), 5)
        self.assertTrue(resultado.por_que_importa)

    def test_piora_aparece_no_resumo_e_na_narrativa(self):
        criar_serie(7, total=100, total_kpi=100, violacoes=20, criticos=60,
                    resolvidos=50, soma_mttr_seg=50 * 6000)
        criar_serie(21, ate=DATA_REFERENCIA - timedelta(days=7), total=100, total_kpi=20,
                    resolvidos=20, soma_mttr_seg=20 * 600)

        resultado = briefing.montar()

        self.assertIn('pioraram', resultado.resumo)
        self.assertIn('Operational Health', resultado.por_que_importa)

    def test_acoes_nao_se_repetem(self):
        montar_operacao(dias=120)

        acoes = [a['texto'] for a in briefing.montar().acoes]

        self.assertEqual(len(acoes), len(set(acoes)))


class ViewsTest(TesteComCache):
    ROTAS = ['intelligence:risk', 'intelligence:anomaly',
             'intelligence:correlation', 'intelligence:insights']

    def test_telas_renderizam_com_dados(self):
        montar_operacao()

        for nome in self.ROTAS:
            with self.subTest(rota=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

    def test_anomalias_aceitam_troca_de_dimensao(self):
        montar_operacao()

        resposta = self.client.get(reverse('intelligence:anomaly'), {'dim': DIM.PRODUTO})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['dimensao'], DIM.PRODUTO)

    def test_risk_radar_entrega_as_tres_dimensoes(self):
        montar_operacao()

        panorama = self.client.get(reverse('intelligence:risk')).context['panorama']

        self.assertEqual(set(panorama), {'produtos', 'equipes', 'familias'})
