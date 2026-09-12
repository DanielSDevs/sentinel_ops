from unittest.mock import patch

from django.test import TestCase

from apps.monitor import perfis


class GruposDeComportamentoTest(TestCase):
    """Os rótulos dos grupos saem dos números, não de uma lista fixa no código.

    É o que separa ler o modelo de decorar a saída dele: se o próximo treino inverter quem
    é o grupo de alto volume, ou produzir três grupos, o rótulo tem que acompanhar."""

    def _clusters(self, grupos):
        return {'k': len(grupos), 'silhueta': 0.3, 'grupos': grupos, 'features': []}

    def test_rotula_pelo_perfil_e_ordena_por_volume(self):
        bruto = self._clusters({
            '0': {'produtos': ['a', 'b'], 'perfil': {
                'volume_medio': 1.0, 'mttr': 7000.0, 'dias_ativos': 0.5,
                'share_criticos': 0.16, 'taxa_violacao': 0.017, 'instabilidade': 1.5}},
            '1': {'produtos': ['c'], 'perfil': {
                'volume_medio': 9.0, 'mttr': 1900.0, 'dias_ativos': 0.93,
                'share_criticos': 0.30, 'taxa_violacao': 0.008, 'instabilidade': 0.7}},
        })
        with patch('apps.ml.services.inferencia.clusters', return_value=bruto):
            resultado = perfis.grupos_de_comportamento()

        nomes = [g['titulo'] for g in resultado['grupos']]
        self.assertEqual(nomes[0], 'Núcleo de alto volume', 'o maior volume vem primeiro')
        self.assertEqual(nomes[1], 'Cauda de resolução lenta')

    def test_rotulo_acompanha_a_inversao_dos_grupos(self):
        # Mesmos perfis, números de cluster trocados: o rótulo tem que seguir o dado.
        bruto = self._clusters({
            '0': {'produtos': ['c'], 'perfil': {
                'volume_medio': 9.0, 'mttr': 1900.0, 'dias_ativos': 0.93,
                'share_criticos': 0.30, 'taxa_violacao': 0.008, 'instabilidade': 0.7}},
            '1': {'produtos': ['a', 'b'], 'perfil': {
                'volume_medio': 1.0, 'mttr': 7000.0, 'dias_ativos': 0.5,
                'share_criticos': 0.16, 'taxa_violacao': 0.017, 'instabilidade': 1.5}},
        })
        with patch('apps.ml.services.inferencia.clusters', return_value=bruto):
            resultado = perfis.grupos_de_comportamento()

        por_nome = {g['titulo']: g['produtos'] for g in resultado['grupos']}
        self.assertEqual(por_nome['Núcleo de alto volume'], ['c'])
        self.assertEqual(por_nome['Cauda de resolução lenta'], ['a', 'b'])

    def test_contraste_mede_quantas_vezes_um_grupo_resolve_mais_devagar(self):
        bruto = self._clusters({
            '0': {'produtos': ['a'], 'perfil': {
                'volume_medio': 1.0, 'mttr': 6000.0, 'dias_ativos': 0.5,
                'share_criticos': 0.1, 'taxa_violacao': 0.02, 'instabilidade': 1.5}},
            '1': {'produtos': ['b'], 'perfil': {
                'volume_medio': 9.0, 'mttr': 3000.0, 'dias_ativos': 0.9,
                'share_criticos': 0.3, 'taxa_violacao': 0.01, 'instabilidade': 0.7}},
        })
        with patch('apps.ml.services.inferencia.clusters', return_value=bruto):
            contraste = perfis.grupos_de_comportamento()['contraste']

        self.assertEqual(contraste['vezes'], 2.0)
        self.assertEqual(contraste['mttr_lento'], 100)

    def test_sem_modelo_treinado_o_bloco_some(self):
        with patch('apps.ml.services.inferencia.clusters', return_value=None):
            self.assertIsNone(perfis.grupos_de_comportamento())
