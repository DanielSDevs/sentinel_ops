from django.urls import path

from . import views

app_name = 'core'

urlpatterns = [
    path('', views.command_center, name='home'),
    path('dados/', views.data_sources, name='dados'),
    path('sobre/', views.sobre, name='sobre'),
]
