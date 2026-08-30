from django.db import models

# Metas de OLA por prioridade, em minutos (dicionário de dados da Locaweb):
# 1-Crítica e 2-Alta até 4h, 3-Média até 12h, 4-Baixa até 24h, 5-Muito Baixa até 96h.
SLA_MINUTOS_POR_PRIORIDADE = {1: 240, 2: 240, 3: 720, 4: 1440, 5: 5760}

# Só prioridades 1, 2 e 3 são medidas por KPI.
PRIORIDADES_CRITICAS = (1, 2)


class Equipe(models.Model):
    """Grupo designado responsável por tratar o incidente (campo `Grupo designado`)."""

    nome = models.CharField(max_length=80, unique=True)

    class Meta:
        verbose_name = 'Equipe'
        verbose_name_plural = 'Equipes'
        ordering = ['nome']

    def __str__(self):
        return self.nome


class Produto(models.Model):
    """Produto/serviço afetado (campo `Produto`). Códigos anonimizados pela Locaweb."""

    codigo = models.CharField(max_length=40, unique=True)

    class Meta:
        verbose_name = 'Produto'
        verbose_name_plural = 'Produtos'
        ordering = ['codigo']

    def __str__(self):
        return self.codigo


class ItemConfiguracao(models.Model):
    """Ativo de TI específico com problema (campo `Item de configuração`)."""

    codigo = models.CharField(max_length=40, unique=True)

    class Meta:
        verbose_name = 'Item de configuração'
        verbose_name_plural = 'Itens de configuração'
        ordering = ['codigo']

    def __str__(self):
        return self.codigo


class FamiliaSinal(models.Model):
    """Família de sintoma derivada da `Descrição resumida`.

    A descrição é o único campo textual não anonimizado do dataset — traz o sinal técnico real
    ("Free disk space is less than 10%", "Processor load is too high"). Agrupá-la em famílias
    permite detectar padrões e correlações que os códigos anonimizados não permitiriam.
    """

    slug = models.SlugField(max_length=40, unique=True)
    nome = models.CharField(max_length=80)

    class Meta:
        verbose_name = 'Família de sinal'
        verbose_name_plural = 'Famílias de sinal'
        ordering = ['nome']

    def __str__(self):
        return self.nome


class Incidente(models.Model):
    class Prioridade(models.IntegerChoices):
        P1 = 1, '1 — Crítica'
        P2 = 2, '2 — Alta'
        P3 = 3, '3 — Média'
        P4 = 4, '4 — Baixa'
        P5 = 5, '5 — Muito Baixa'

    class Status(models.TextChoices):
        ENCERRADO = 'encerrado', 'Encerrado'
        ENCERRADO_AUTO = 'encerrado_auto', 'Encerrado Automaticamente'
        SEM_INTERVENCAO = 'sem_intervencao', 'Sem Intervenção'
        AGUARDANDO = 'aguardando', 'Aguardando Problema'
        ABERTO = 'aberto', 'Aberto'

    class AbertoPor(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        MONITORAMENTO = 'monitoramento', 'Monitoramento'

    class Solucao(models.TextChoices):
        CONTORNO = 'contorno', 'Contorno'
        DEFINITIVA = 'definitiva', 'Definitiva'

    class Origem(models.TextChoices):
        DATASET = 'dataset', 'Dataset Locaweb'
        SIMULACAO = 'simulacao', 'Simulação manual'

    numero = models.CharField(max_length=30, unique=True)
    prioridade = models.IntegerField(choices=Prioridade.choices)
    produto = models.ForeignKey(
        Produto, on_delete=models.PROTECT, related_name='incidentes', null=True, blank=True,
    )
    categoria = models.CharField(max_length=40, blank=True)
    subcategoria = models.CharField(max_length=40, blank=True)
    equipe = models.ForeignKey(Equipe, on_delete=models.PROTECT, related_name='incidentes')
    item_configuracao = models.ForeignKey(
        ItemConfiguracao, on_delete=models.PROTECT, related_name='incidentes', null=True, blank=True,
    )
    familia_sinal = models.ForeignKey(
        FamiliaSinal, on_delete=models.PROTECT, related_name='incidentes', null=True, blank=True,
    )
    descricao = models.TextField()

    aberto_em = models.DateTimeField()
    resolvido_em = models.DateTimeField(null=True, blank=True)
    encerrado_em = models.DateTimeField(null=True, blank=True)
    duracao_seg = models.BigIntegerField(
        default=0, help_text='Campo `Duração` do dataset — tempo até resolução/encerramento.',
    )

    cod_fechamento = models.CharField(max_length=80, blank=True)
    solucao = models.CharField(max_length=20, choices=Solucao.choices, blank=True)
    aberto_por = models.CharField(max_length=20, choices=AbertoPor.choices)
    incidente_pai = models.ForeignKey(
        'self', on_delete=models.SET_NULL, related_name='filhos', null=True, blank=True,
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    entrou_kpi = models.BooleanField(default=False)
    kpi_violado = models.BooleanField(null=True, blank=True)
    origem = models.CharField(max_length=20, choices=Origem.choices, default=Origem.DATASET)

    class Meta:
        verbose_name = 'Incidente'
        verbose_name_plural = 'Incidentes'
        ordering = ['-aberto_em']
        indexes = [
            models.Index(fields=['-aberto_em']),
            models.Index(fields=['entrou_kpi', '-aberto_em']),
            models.Index(fields=['produto', '-aberto_em']),
            models.Index(fields=['equipe', '-aberto_em']),
            models.Index(fields=['familia_sinal', '-aberto_em']),
            # Correlation Engine varre a janela ordenada por ativo e horário.
            models.Index(fields=['item_configuracao', 'aberto_em']),
            models.Index(fields=['kpi_violado']),
        ]

    def __str__(self):
        return f'{self.numero} ({self.get_prioridade_display()})'

    @property
    def sla_minutos(self):
        return SLA_MINUTOS_POR_PRIORIDADE[self.prioridade]

    @property
    def mttr_minutos(self):
        """Tempo real de resolução. Só existe quando alguém de fato trabalhou o incidente.

        Incidentes "Sem Intervenção" são fechados em lote e não têm `Resolvido` — usar a `Duração`
        bruta neles mediria o fechamento administrativo, não o esforço de operação.
        """
        if not self.resolvido_em:
            return None
        return (self.resolvido_em - self.aberto_em).total_seconds() / 60

    @property
    def esta_ativo(self):
        return self.status in {Incidente.Status.ABERTO, Incidente.Status.AGUARDANDO}


class MetricaDiaria(models.Model):
    """Agregados diários pré-computados na importação.

    Com 122k incidentes em SQLite, agregar a tabela inteira a cada request deixaria as páginas
    lentas. As telas leem daqui; a tabela de incidentes fica para drill-down pontual.
    """

    class Dimensao(models.TextChoices):
        GLOBAL = 'global', 'Global'
        PRODUTO = 'produto', 'Produto'
        EQUIPE = 'equipe', 'Equipe'
        FAMILIA = 'familia', 'Família de sinal'
        CATEGORIA = 'categoria', 'Categoria'

    data = models.DateField()
    dimensao = models.CharField(max_length=20, choices=Dimensao.choices)
    chave = models.CharField(max_length=80, blank=True)

    total = models.IntegerField(default=0)
    total_kpi = models.IntegerField(default=0)
    violacoes = models.IntegerField(default=0)
    # Críticos e MTTR são contados APENAS sobre incidentes elegíveis a KPI. Misturar o ruído de
    # monitoramento aqui produziria números sem sentido: participação de críticos acima de 100%
    # (numerador com incidentes fora do KPI) e um MTTR artificialmente baixo, já que os chamados
    # automáticos fecham em segundos sem intervenção humana.
    criticos = models.IntegerField(default=0)
    resolvidos = models.IntegerField(default=0)
    soma_mttr_seg = models.BigIntegerField(default=0)

    class Meta:
        verbose_name = 'Métrica diária'
        verbose_name_plural = 'Métricas diárias'
        ordering = ['-data']
        unique_together = [('data', 'dimensao', 'chave')]
        indexes = [
            models.Index(fields=['dimensao', 'chave', '-data']),
            models.Index(fields=['-data']),
        ]

    def __str__(self):
        return f'{self.data} · {self.dimensao}:{self.chave or "—"} · {self.total}'

    @property
    def mttr_medio_minutos(self):
        if not self.resolvidos:
            return None
        return (self.soma_mttr_seg / self.resolvidos) / 60
