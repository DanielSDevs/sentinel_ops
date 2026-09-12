from django.urls import path

from . import views

app_name = 'ml'

urlpatterns = [
    path('', views.modelos, name='modelos'),
    path('previsoes/', views.previsoes, name='previsoes'),
    path('<slug:nome>/', views.detalhe, name='detalhe'),
]
