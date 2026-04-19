FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/ipastore

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY static ./static
# patch/ contient les scripts Python de patch IPA (fix_ipa.py, etc.).
# Chaque .py a la racine est automatiquement decouvert par app.patches et
# devient selectionnable depuis l'onglet Patch de l'UI.
COPY patch ./patch

RUN groupadd -r ipastore && useradd -r -g ipastore -u 1000 ipastore \
 && mkdir -p /srv/store /etc/ipastore \
 && chown -R ipastore:ipastore /opt/ipastore /srv/store /etc/ipastore

USER ipastore

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/source.json >/dev/null || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
