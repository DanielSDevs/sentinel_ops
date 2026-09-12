from django.contrib import admin

from .models import PrevisaoRegistrada


@admin.register(PrevisaoRegistrada)
class PrevisaoRegistradaAdmin(admin.ModelAdmin):
    list_display = ('data_alvo', 'modelo', 'horizonte', 'valor_previsto', 'valor_real', 'origem')
    list_filter = ('modelo', 'origem', 'horizonte')
    date_hierarchy = 'data_alvo'
