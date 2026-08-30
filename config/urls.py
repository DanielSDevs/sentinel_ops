"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.core.urls')),
    path('contas/', include('apps.accounts.urls')),
    path('inteligencia/', include('apps.intelligence.urls')),
    path('forecast/', include('apps.forecast.urls')),
    path('monitor/', include('apps.monitor.urls')),
    path('alertas/', include('apps.alerts.urls')),
    path('copilot/', include('apps.copilot.urls')),
    path('relatorios/', include('apps.reports.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
