from django.urls import path

from . import views

app_name = 'monitor'

urlpatterns = [
    path('', views.live_operations, name='live'),
    path('saude/', views.service_health, name='saude'),
    path('saude/<str:codigo>/', views.detalhe_servico, name='detalhe_servico'),
    path('incidentes/', views.incident_intelligence, name='incidentes'),
    path('incidentes/<str:numero>/', views.detalhe_incidente, name='detalhe_incidente'),
    path('simular/', views.simular, name='simular'),
]
