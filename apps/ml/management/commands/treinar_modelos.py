"""Treina todos os modelos de ML da plataforma a partir do banco já importado."""

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.ml.models import PrevisaoRegistrada
from ml.src import config, registry, train

# Acima disso o artefato deixa de ser prático de versionar e de publicar.
LIMITE_ARTEFATO_MB = 50


class Command(BaseCommand):
    help = (
        'Treina os modelos de ML (D+1, D+7, pico, OLA, anomalia, cluster), grava os artefatos '
        'em ml/models/ e registra as previsões de teste no histórico.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--sem-historico', action='store_true',
            help='Não registra as previsões do conjunto de teste no histórico.',
        )

    def handle(self, *args, **options):
        if not config.BANCO.exists():
            raise CommandError(
                f'Banco não encontrado em {config.BANCO}. '
                f'Rode `python manage.py importar_dataset` primeiro.'
            )

        self.stdout.write('Treinando modelos — isso leva alguns minutos.')
        resumo, cartoes = train.executar_tudo(verbose=True)

        for cartao in cartoes:
            principal = cartao['metrica_principal']
            bloco = cartao['metricas'].get('teste') or cartao['metricas']
            valor = bloco.get(principal)
            tamanho = registry.caminho_artefato(cartao['nome']).stat().st_size / 1e6
            self.stdout.write(
                f'  {cartao["nome"]:<16} {cartao["algoritmo"]:<34} '
                f'{principal}={valor}  ({tamanho:.1f} MB)'
            )
            # Artefato gordo é regressão silenciosa: continua funcionando, mas não cabe no
            # versionamento nem no pacote de deploy, e atrasa cada processo que o carrega.
            if tamanho > LIMITE_ARTEFATO_MB:
                self.stdout.write(self.style.WARNING(
                    f'    atenção: {cartao["nome"]} passou de {LIMITE_ARTEFATO_MB} MB. '
                    f'Verifique o teto de profundidade das árvores em ml/src/models_zoo.py.'
                ))

        if not options['sem_historico']:
            self.stdout.write('\nRegistrando previsões de teste no histórico...')
            call_command('registrar_previsoes', historico=True, verbosity=0)
            # As previsões publicadas antes do retreino vieram de outro artefato. Mantê-las
            # sugeriria que o modelo atual as fez; apagá-las deixa o histórico contar só a
            # trajetória destes modelos. Elas voltam a acumular a partir daqui.
            apagadas, _ = PrevisaoRegistrada.objects.filter(
                origem=PrevisaoRegistrada.Origem.PRODUCAO,
            ).delete()
            if apagadas:
                self.stdout.write(
                    f'{apagadas} previsões publicadas pelo modelo anterior foram descartadas.'
                )
            call_command('registrar_previsoes', verbosity=0)

        # As telas cacheiam artefato e contexto por 5 min; sem isso o modelo novo só apareceria
        # depois que o cache expirasse.
        cache.clear()

        self.stdout.write(self.style.SUCCESS(
            f'\n{len(cartoes)} modelos treinados em {resumo["segundos"]}s. '
            f'Dados de {resumo["inicio_regime"]} a {resumo["fim_dados"]}.'
        ))
