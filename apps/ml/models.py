"""Persistência do monitoramento dos modelos.

Métricas de treino ficam nos cartões JSON em `ml/models/` — são propriedade do artefato, não do
banco. O que mora aqui é o que só existe com o tempo: cada previsão que a plataforma publicou,
e o valor que o mundo devolveu depois. É o registro que permite responder "o modelo continua
acertando?" sem ter de acreditar na métrica do dia do treino.
"""

from django.db import models


class PrevisaoRegistrada(models.Model):
    """Uma previsão publicada para uma data, com o real preenchido quando o dia chega."""

    class Origem(models.TextChoices):
        BACKTEST = 'backtest', 'Teste fora da amostra'
        PRODUCAO = 'producao', 'Previsão publicada'

    modelo = models.CharField(max_length=40)
    algoritmo = models.CharField(max_length=40, blank=True)
    dimensao = models.CharField(max_length=20, default='global')
    chave = models.CharField(max_length=80, blank=True)

    data_execucao = models.DateField(help_text='Último dia observado quando a previsão foi feita.')
    data_alvo = models.DateField()
    horizonte = models.IntegerField()

    valor_previsto = models.FloatField()
    minimo = models.FloatField(null=True, blank=True)
    maximo = models.FloatField(null=True, blank=True)
    valor_real = models.FloatField(null=True, blank=True)

    origem = models.CharField(max_length=20, choices=Origem.choices, default=Origem.PRODUCAO)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Previsão registrada'
        verbose_name_plural = 'Previsões registradas'
        ordering = ['-data_alvo', 'modelo', 'dimensao', 'chave']
        constraints = [
            models.UniqueConstraint(
                fields=['modelo', 'dimensao', 'chave', 'data_execucao', 'data_alvo'],
                name='previsao_unica_por_execucao',
            ),
        ]
        indexes = [
            models.Index(fields=['modelo', '-data_alvo']),
            models.Index(fields=['dimensao', 'chave', '-data_alvo']),
        ]

    def __str__(self):
        return f'{self.modelo} · {self.data_alvo} · {self.valor_previsto:.0f}'

    @property
    def erro(self):
        if self.valor_real is None:
            return None
        return self.valor_real - self.valor_previsto

    @property
    def erro_absoluto(self):
        erro = self.erro
        return None if erro is None else abs(erro)

    @property
    def erro_percentual(self):
        if not self.valor_real:
            return None
        return abs(self.erro) / self.valor_real * 100

    @property
    def dentro_da_faixa(self):
        """A faixa cumpriu o que prometia? Só faz sentido depois que o real chega."""
        if self.valor_real is None or self.minimo is None or self.maximo is None:
            return None
        return self.minimo <= self.valor_real <= self.maximo

    @property
    def aguardando(self):
        return self.valor_real is None
