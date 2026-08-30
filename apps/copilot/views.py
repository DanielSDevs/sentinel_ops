from django.shortcuts import render

from . import services


def index(request):
    pergunta = request.GET.get('q', '').strip()
    resposta = services.responder(pergunta) if pergunta else None
    return render(request, 'copilot/index.html', {
        'pergunta': pergunta,
        'resposta': resposta,
        'sugestoes': services.SUGESTOES,
    })
