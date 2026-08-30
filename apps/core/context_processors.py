from apps.core.tempo import data_referencia


def referencia(request):
    """Expõe a data de referência do dataset em todas as telas.

    A plataforma analisa um extrato fechado; deixar isso visível em todo lugar evita que alguém
    leia "hoje" como o dia corrente e tire conclusões erradas.
    """
    try:
        return {'data_referencia': data_referencia()}
    except Exception:
        return {'data_referencia': None}
