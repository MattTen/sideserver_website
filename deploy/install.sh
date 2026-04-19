#!/bin/bash
# IPA Store — install / update on VM. Run as root via su.
set -euo pipefail

SRC=/home/altuser/ipastore-src
DST=/opt/ipastore

echo "=== 1. Creation des dossiers ==="
mkdir -p "$DST" /srv/store/ipas /srv/store/icons /srv/store/screenshots /var/log/ipastore
chown -R altuser:altuser /srv/store /var/log/ipastore

echo "=== 2. Copie du code source ==="
rsync -a --delete --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' "$SRC"/ "$DST"/
chown -R altuser:altuser "$DST"

echo "=== 3. Python venv + deps ==="
if [ ! -d "$DST/venv" ]; then
    su - altuser -c "python3 -m venv $DST/venv"
fi
su - altuser -c "$DST/venv/bin/pip install --upgrade pip wheel --quiet"
su - altuser -c "$DST/venv/bin/pip install -r $DST/requirements.txt --quiet"

echo "=== 4. Systemd service ==="
cp "$DST/deploy/ipastore.service" /etc/systemd/system/ipastore.service
systemctl daemon-reload
systemctl enable ipastore.service

echo "=== 5. Nginx site ==="
cp "$DST/deploy/nginx-store.conf" /etc/nginx/sites-available/store
ln -sf /etc/nginx/sites-available/store /etc/nginx/sites-enabled/store
rm -f /etc/nginx/sites-enabled/default
nginx -t

echo "=== 6. Demarrage des services ==="
systemctl restart ipastore.service
systemctl reload nginx

echo "=== 7. Etat ==="
sleep 1
systemctl is-active ipastore.service && echo "ipastore: active"
systemctl is-active nginx && echo "nginx: active"
systemctl is-active mariadb && echo "mariadb: active"

echo
echo "=== DONE ==="
echo "Ouvre : http://<IP_SERVEUR>/"
echo "Premier lancement = setup du compte admin."
