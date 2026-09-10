#!/bin/sh
# Comando de inicialização do Azure App Service (Configuração > Configurações gerais > Comando de
# inicialização: `sh startup.sh`). O build do App Service (Oryx) já instalou as dependências; aqui
# só falta pôr a base no lugar, migrar e subir o gunicorn.
set -e

SQLITE_PATH="${SQLITE_PATH:-/home/data/db.sqlite3}"
export SQLITE_PATH
mkdir -p "$(dirname "$SQLITE_PATH")"

# Troca de base: quem quer publicar dados novos envia `db.sqlite3.novo` para /home/data e reinicia
# o app. A troca acontece aqui, antes de qualquer processo abrir o arquivo — sobrescrever a base com
# o gunicorn rodando corromperia as leituras em andamento. A base anterior fica guardada ao lado.
if [ -f "$SQLITE_PATH.novo" ]; then
    echo "startup: nova base encontrada; a atual vai para $SQLITE_PATH.anterior"
    if [ -f "$SQLITE_PATH" ]; then
        mv -f "$SQLITE_PATH" "$SQLITE_PATH.anterior"
    fi
    # Um journal órfão da base antiga seria reaplicado sobre a nova e a corromperia.
    if [ -f "$SQLITE_PATH-journal" ]; then
        mv -f "$SQLITE_PATH-journal" "$SQLITE_PATH.anterior-journal"
    fi
    mv -f "$SQLITE_PATH.novo" "$SQLITE_PATH"
fi

# Idempotente: numa base já migrada não faz nada; numa base nova cria as tabelas vazias.
python manage.py migrate --noinput

# O pacote do GitHub Actions já traz os estáticos coletados; isto só age se o manifesto sumir.
if [ ! -f staticfiles/staticfiles.json ]; then
    python manage.py collectstatic --noinput
fi

# Um processo só: o plano B1 tem 1 vCPU, e com um processo o cache em memória (âncora temporal,
# Correlation Engine) é um só, em vez de cada worker recalcular o seu. As threads atendem as
# requisições concorrentes enquanto uma delas espera o banco.
exec gunicorn config.wsgi \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${GUNICORN_WORKERS:-1}" \
    --threads "${GUNICORN_THREADS:-4}" \
    --timeout 180 \
    --access-logfile - \
    --error-logfile -
