from django.urls import path

from . import views

app_name = 'forecast'

urlpatterns = [
    path('', views.index, name='index'),
    path('modelo/', views.model_performance, name='modelo'),
]
