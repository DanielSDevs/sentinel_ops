"""Âncora temporal da plataforma.

O dataset da Locaweb termina em 31/12/2025. Se a plataforma usasse `timezone.now()`, todas as
janelas ("hoje", "últimas 24h", "D+1") cairiam num vazio depois do fim dos dados.

A solução é ancorar o "agora" da plataforma no último incidente registrado. Toda a UI exibe um
selo com a data de referência, deixando claro que é uma leitura sobre um extrato fechado — e não
uma alegação de tempo real.
"""

from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

CACHE_KEY = 'sentinelops:referencia_temporal'
CACHE_TTL = 300


def referencia_temporal():
    """Momento tratado como 'agora' — o último incidente aberto no dataset."""
    from apps.core.models import Incidente

    valor = cache.get(CACHE_KEY)
    if valor is None:
        valor = Incidente.objects.order_by('-aberto_em').values_list('aberto_em', flat=True).first()
        valor = valor or timezone.now()
        cache.set(CACHE_KEY, valor, CACHE_TTL)
    return valor


def data_referencia():
    return timezone.localtime(referencia_temporal()).date()


def janela(dias):
    """(inicio, fim) das últimas `dias` contadas a partir da referência."""
    fim = data_referencia()
    return fim - timedelta(days=dias - 1), fim


def janela_anterior(dias):
    """Janela imediatamente anterior a `janela(dias)`, para comparações período a período."""
    inicio_atual, _ = janela(dias)
    fim = inicio_atual - timedelta(days=1)
    return fim - timedelta(days=dias - 1), fim


def invalidar_cache():
    cache.delete(CACHE_KEY)
