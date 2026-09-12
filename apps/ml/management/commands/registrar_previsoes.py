"""Alimenta o histórico de previsões: publica as de hoje e confere as antigas contra o real.

Duas fontes, marcadas como tais e nunca misturadas:

- `--historico` carrega as previsões do **conjunto de teste** gravadas no treino. São previsões
  fora da amostra, feitas por um modelo que não viu aqueles dias, e com o real já conhecido —
  por isso já nascem com erro calculado.
- sem argumento, grava a previsão **publicada hoje** para os próximos 7 dias e preenche o real
  das previsões cuja data já chegou.

A separação importa: um backtest não prova que o modelo está funcionando em produção, e
apresentar os dois como a mesma coisa seria maquiar o monitoramento.
"""

import csv

from django.core.management.base import BaseCommand

from apps.core.models import MetricaDiaria
from apps.core.tempo import data_referencia
from apps.ml.models import PrevisaoRegistrada
from apps.ml.services import inferencia
from ml.src import config

DIMENSOES_PUBLICADAS = (('global', ''),)


class Command(BaseCommand):
    help = 'Registra previsões no histórico e preenche o valor real das que já venceram.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--historico', action='store_true',
            help='Importa as previsões do conjunto de teste gravadas pelo treino.',
        )
        parser.add_argument(
            '--limpar', action='store_true',
            help='Apaga o histórico antes de registrar.',
        )

    def handle(self, *args, **options):
        if options['limpar']:
            apagados, _ = PrevisaoRegistrada.objects.all().delete()
            self.stdout.write(f'{apagados} registros apagados.')

        if options['historico']:
            total = self._importar_teste()
            self.stdout.write(self.style.SUCCESS(
                f'{total} previsões de teste registradas.'
            ))
            return

        publicadas = self._publicar()
        conferidas = self._conferir()
        self.stdout.write(self.style.SUCCESS(
            f'{publicadas} previsões publicadas · {conferidas} conferidas contra o real.'
        ))

    def _importar_teste(self):
        """Lê os CSVs gravados pelo treino. Só o global entra — é a série que a tela acompanha.

        As previsões de teste do modelo anterior são apagadas antes: elas pertencem ao artefato
        que as produziu, e um retreino as torna obsoletas. Mantê-las lado a lado com as novas
        faria o histórico misturar dois modelos diferentes sob o mesmo nome.
        """
        total = 0
        for arquivo in sorted(config.DADOS_DIR.glob('previsoes_teste_*.csv')):
            nome_modelo = arquivo.stem.replace('previsoes_teste_', '')
            PrevisaoRegistrada.objects.filter(
                modelo=nome_modelo, origem=PrevisaoRegistrada.Origem.BACKTEST,
            ).delete()
            registros = []
            with arquivo.open(encoding='utf-8') as fonte:
                for linha in csv.DictReader(fonte):
                    if linha['dimensao'] != 'global':
                        continue
                    previsto = float(linha['previsto'])
                    margem = float(linha['margem'])
                    registros.append(PrevisaoRegistrada(
                        modelo=linha['modelo'],
                        algoritmo=linha['algoritmo'],
                        dimensao=linha['dimensao'],
                        chave=linha['chave'],
                        data_execucao=linha['data_origem'][:10],
                        data_alvo=linha['data_alvo'][:10],
                        horizonte=int(linha['horizonte']),
                        valor_previsto=previsto,
                        minimo=max(0.0, previsto - margem),
                        maximo=previsto + margem,
                        valor_real=float(linha['real']),
                        origem=PrevisaoRegistrada.Origem.BACKTEST,
                    ))
            if registros:
                PrevisaoRegistrada.objects.bulk_create(registros, ignore_conflicts=True)
                total += len(registros)
        return total

    def _publicar(self):
        """Grava a previsão que a plataforma está mostrando agora, para conferir depois."""
        registros = []
        for dimensao, chave in DIMENSOES_PUBLICADAS:
            previsoes = inferencia.prever(dimensao, chave, dias=7)
            contexto = inferencia.contexto()
            if not previsoes or contexto is None:
                continue
            origem = contexto['origem'].date()
            for previsao in previsoes:
                registros.append(PrevisaoRegistrada(
                    modelo=previsao['modelo'],
                    algoritmo=previsao['algoritmo'],
                    dimensao=dimensao,
                    chave=chave,
                    data_execucao=origem,
                    data_alvo=previsao['data'],
                    horizonte=previsao['horizonte'],
                    valor_previsto=previsao['valor_exato'],
                    minimo=previsao['minimo'],
                    maximo=previsao['maximo'],
                    origem=PrevisaoRegistrada.Origem.PRODUCAO,
                ))
        PrevisaoRegistrada.objects.bulk_create(registros, ignore_conflicts=True)
        return len(registros)

    def _conferir(self):
        """Preenche `valor_real` das previsões cuja data-alvo já passou."""
        pendentes = PrevisaoRegistrada.objects.filter(
            valor_real__isnull=True, data_alvo__lte=data_referencia(),
        )
        if not pendentes.exists():
            return 0

        reais = {
            (m.dimensao, m.chave, m.data): m.total_kpi
            for m in MetricaDiaria.objects.filter(
                data__in=pendentes.values_list('data_alvo', flat=True),
            )
        }
        atualizar = []
        for previsao in pendentes:
            chave = (previsao.dimensao, previsao.chave, previsao.data_alvo)
            # Dia sem linha de métrica é dia sem incidente elegível, não dia sem dado.
            previsao.valor_real = float(reais.get(chave, 0))
            atualizar.append(previsao)

        PrevisaoRegistrada.objects.bulk_update(atualizar, ['valor_real'], batch_size=200)
        return len(atualizar)
