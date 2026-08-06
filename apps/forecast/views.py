from django.shortcuts import render


def index(request):
    return render(request, 'forecast/index.html')
