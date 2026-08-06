from django.shortcuts import render


def index(request):
    return render(request, 'alerts/index.html')
