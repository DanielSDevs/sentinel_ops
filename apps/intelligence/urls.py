from django.urls import path

from . import views

app_name = 'intelligence'

urlpatterns = [
    path('risco/', views.risk_radar, name='risk'),
    path('anomalias/', views.anomaly_detection, name='anomaly'),
    path('correlacao/', views.correlation_engine, name='correlation'),
    path('insights/', views.operational_insights, name='insights'),
]
