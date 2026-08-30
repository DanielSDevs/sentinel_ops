from django.contrib import admin

from .models import Equipe, FamiliaSinal, Incidente, ItemConfiguracao, MetricaDiaria, Produto


@admin.register(Equipe)
class EquipeAdmin(admin.ModelAdmin):
    list_display = ['nome']
    search_fields = ['nome']


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ['codigo']
    search_fields = ['codigo']


@admin.register(ItemConfiguracao)
class ItemConfiguracaoAdmin(admin.ModelAdmin):
    list_display = ['codigo']
    search_fields = ['codigo']


@admin.register(FamiliaSinal)
class FamiliaSinalAdmin(admin.ModelAdmin):
    list_display = ['nome', 'slug']
    search_fields = ['nome', 'slug']


@admin.register(Incidente)
class IncidenteAdmin(admin.ModelAdmin):
    list_display = [
        'numero', 'prioridade', 'produto', 'equipe', 'familia_sinal',
        'status', 'aberto_por', 'entrou_kpi', 'kpi_violado', 'aberto_em',
    ]
    list_filter = [
        'prioridade', 'status', 'aberto_por', 'entrou_kpi', 'kpi_violado',
        'origem', 'familia_sinal', 'equipe',
    ]
    search_fields = ['numero', 'descricao', 'categoria', 'subcategoria']
    date_hierarchy = 'aberto_em'
    raw_id_fields = ['produto', 'item_configuracao', 'incidente_pai']
    list_select_related = ['produto', 'equipe', 'familia_sinal']


@admin.register(MetricaDiaria)
class MetricaDiariaAdmin(admin.ModelAdmin):
    list_display = ['data', 'dimensao', 'chave', 'total', 'total_kpi', 'violacoes', 'criticos']
    list_filter = ['dimensao', 'data']
    search_fields = ['chave']
