from django.urls import path

from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.diario, name='diario'),
    path('executivo/', views.executivo, name='executivo'),
]
