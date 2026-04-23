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

# UID/GID de l'user interne du conteneur. Le bootstrap passe les valeurs
# reelles de l'user host `ipastore` via build-args dans docker-compose.yml,
# pour que les fichiers ecrits dans les volumes montes (/etc/ipastore,
# /srv/store) matchent les permissions host. Defaut 1000 pour les builds
# locaux/manuels.
ARG IPASTORE_UID=1000
ARG IPASTORE_GID=1000

RUN groupadd -r -g ${IPASTORE_GID} ipastore \
 && useradd -r -g ipastore -u ${IPASTORE_UID} ipastore \
 && mkdir -p /srv/store /etc/ipastore \
 && chown -R ipastore:ipastore /opt/ipastore /srv/store /etc/ipastore

USER ipastore

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz >/dev/null || exit 1

# --proxy-headers + --forwarded-allow-ips=* : fait confiance a X-Forwarded-*
# pour que request.base_url reflete l'URL publique (scheme + host) meme
# derriere un reverse proxy / Cloudflare Tunnel. Sans ca, source.json sort
# des liens vers http://127.0.0.1:8000 et SideStore ne peut rien telecharger.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
