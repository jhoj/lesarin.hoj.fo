#!/usr/bin/env bash
# One-time VPS bootstrap for Lesarin (Ubuntu/Debian). Run once as root on a
# fresh server BEFORE the first GitHub Actions deploy:
#
#   sudo bash setup-server.sh
#
# It installs system deps (incl. OCR), creates the service user and data dirs,
# generates the token-signing secret, installs the systemd unit + nginx config,
# and grants the deploy user permission to restart the service. Safe to re-run.
set -euo pipefail

APP_DIR=/opt/lesarin
DATA_DIR=/var/lib/lesarin
ENV_DIR=/etc/lesarin
SERVICE_USER=lesarin
DEPLOY_USER="${DEPLOY_USER:-deploy}"   # the SSH user GitHub Actions logs in as

if [ "$(id -u)" -ne 0 ]; then echo "Run as root (sudo)." >&2; exit 1; fi

echo "==> Installing system packages"
apt-get update -y
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip rsync nginx \
  tesseract-ocr tesseract-ocr-dan poppler-utils

echo "==> Creating service user + directories"
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$APP_DIR" "$DATA_DIR" "$ENV_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
# The deploy user owns the code dir so rsync can write without sudo.
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"

echo "==> Writing $ENV_DIR/lesarin.env (secret generated once)"
if [ ! -f "$ENV_DIR/lesarin.env" ]; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  cat > "$ENV_DIR/lesarin.env" <<EOF
# Pinning LESARIN_SECRET keeps login tokens valid across restarts/deploys.
LESARIN_SECRET=$SECRET
# Keep the SQLite DB outside the code dir so deploys never overwrite it.
LESARIN_DB=$DATA_DIR/lesarin.db
EOF
  chmod 640 "$ENV_DIR/lesarin.env"
  chown root:"$SERVICE_USER" "$ENV_DIR/lesarin.env"
else
  echo "    exists — leaving it untouched."
fi

echo "==> Installing systemd unit"
install -m 644 "$APP_DIR/deploy/lesarin.service" /etc/systemd/system/lesarin.service 2>/dev/null \
  || echo "    (code not deployed yet — copy deploy/lesarin.service after first rsync)"
systemctl daemon-reload
systemctl enable lesarin || true

echo "==> Granting the deploy user a no-password 'systemctl restart lesarin'"
cat > /etc/sudoers.d/lesarin-deploy <<EOF
$DEPLOY_USER ALL=(root) NOPASSWD: /bin/systemctl restart lesarin, /bin/systemctl status lesarin, /bin/systemctl is-active lesarin
EOF
chmod 440 /etc/sudoers.d/lesarin-deploy

echo "==> Installing nginx site (edit server_name, then run certbot)"
if [ ! -f /etc/nginx/sites-available/lesarin ]; then
  install -m 644 "$APP_DIR/deploy/nginx-lesarin.conf" /etc/nginx/sites-available/lesarin 2>/dev/null \
    && ln -sf /etc/nginx/sites-available/lesarin /etc/nginx/sites-enabled/lesarin \
    && nginx -t && systemctl reload nginx \
    || echo "    (copy deploy/nginx-lesarin.conf after first rsync, then reload nginx)"
fi

cat <<'DONE'

==> Done. Next:
  1. Point your domain's A/AAAA record at this server.
  2. Edit server_name in /etc/nginx/sites-available/lesarin, then:
       sudo apt-get install -y certbot python3-certbot-nginx
       sudo certbot --nginx -d your.domain
  3. Add the GitHub repo secrets (see docs/deploy.md) and merge to main.
DONE
