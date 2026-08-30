from django.urls import path

from . import views

app_name = 'alerts'

urlpatterns = [
    path('', views.alert_center, name='index'),
    path('decisao/', views.decision_center, name='decisao'),
]
