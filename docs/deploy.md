# Deploying Lesarin to a VPS (GitHub Actions → SSH)

When a pull request is merged into `main`, GitHub Actions builds the Angular app,
runs the backend tests, and — only if they pass — ships the code to your VPS over
SSH and restarts the service. This is a single-VPS, no-Docker setup: uvicorn runs
under systemd, nginx terminates TLS, and the SQLite database lives on disk and
persists across deploys.

```
 merge to main ──► GitHub Actions ──► build frontend (Node 24)
                                  ──► pytest (gate)
                                  ──► rsync code + dist over SSH
                                  ──► ssh: pip install + systemctl restart
                                              │
                       nginx :443 (TLS) ──────┴──► uvicorn 127.0.0.1:8000
                                                   data: /var/lib/lesarin/lesarin.db
```

Files involved:

| Path | Role |
| --- | --- |
| `.github/workflows/deploy.yml` | the CI/CD pipeline (build, test, ship) |
| `deploy/setup-server.sh` | one-time VPS bootstrap (deps, user, dirs, secret, unit, nginx) |
| `deploy/deploy.sh` | server-side step each deploy: venv + deps + restart |
| `deploy/lesarin.service` | systemd unit (uvicorn) |
| `deploy/nginx-lesarin.conf` | reverse-proxy config |

## 1. One-time server setup

On the VPS (Ubuntu/Debian), as a sudo-capable user:

```bash
# a) A dedicated 'deploy' login for GitHub Actions (no password; key only).
sudo adduser --disabled-password --gecos "" deploy

# b) Put the code in place once so the setup script has deploy/ + .git.
sudo apt-get update && sudo apt-get install -y git
sudo -u deploy git clone https://github.com/jhoj/lesarin.hoj.fo.git /opt/lesarin
# (private repo? use a clone URL/credential you control, or scp the repo up.)

# c) Bootstrap: system deps incl. OCR, service user, data dir, signing secret,
#    systemd unit, nginx site, and a one-line sudoers rule for the restart.
sudo DEPLOY_USER=deploy bash /opt/lesarin/deploy/setup-server.sh
```

Then get a certificate (point your domain at the VPS first, and set
`server_name` in `/etc/nginx/sites-available/lesarin`):

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your.domain
```

## 2. The deploy SSH key

GitHub Actions authenticates to the VPS with a dedicated key pair:

```bash
# On your machine (or the server): generate a key with no passphrase.
ssh-keygen -t ed25519 -f lesarin_deploy -N "" -C "github-actions"

# Authorise the PUBLIC key for the deploy user on the VPS:
ssh-copy-id -i lesarin_deploy.pub deploy@your.server   # or append to
                                                        # ~deploy/.ssh/authorized_keys
```

## 3. GitHub repository secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Value |
| --- | --- |
| `SSH_PRIVATE_KEY` | contents of the **private** `lesarin_deploy` file |
| `SSH_HOST` | the VPS IP or hostname |
| `SSH_USER` | `deploy` |
| `SSH_PORT` | *(optional)* SSH port if not `22` |
| `DEPLOY_PATH` | *(optional)* code dir if not `/opt/lesarin` |

## 4. Deploy

Merge a PR into `main` (or run the **Deploy** workflow manually from the Actions
tab). The run goes red if the tests fail or the service doesn't come back up, so
a broken build never reaches users.

## How data and config persist

- **Database** — `/var/lib/lesarin/lesarin.db`, *outside* the code dir, so the
  `rsync --delete` never touches it. Schema changes are applied automatically by
  `init_db()` at startup (it creates tables, seeds the canonical vocabulary, and
  back-fills new columns), so there's no separate migration step.
- **Token secret** — `LESARIN_SECRET` in `/etc/lesarin/lesarin.env`, generated
  once. Pinning it keeps everyone logged in across restarts and deploys.
- **Backups** — the whole state is one file. A nightly
  `sqlite3 /var/lib/lesarin/lesarin.db ".backup '/var/backups/lesarin-$(date +\%F).db'"`
  cron job is plenty to start.

## Updating the systemd unit or nginx config

These are copied into `/etc/...` during the one-time setup, so editing the repo
copies later doesn't move them automatically. After changing
`deploy/lesarin.service` or `deploy/nginx-lesarin.conf`, re-copy on the server:

```bash
sudo install -m644 /opt/lesarin/deploy/lesarin.service /etc/systemd/system/lesarin.service
sudo systemctl daemon-reload && sudo systemctl restart lesarin
```

## Troubleshooting

```bash
sudo systemctl status lesarin --no-pager      # is it up?
sudo journalctl -u lesarin -n 100 --no-pager  # app logs
curl -sS http://127.0.0.1:8000/health         # does the app answer locally?
sudo nginx -t && sudo systemctl reload nginx   # proxy config sane?
```

## Scaling past SQLite

The unit runs a single uvicorn worker, which keeps SQLite write-contention-free
and is fine for a small tenant base. When you outgrow it, move `LESARIN_DB` to a
Postgres URL and raise `--workers` — the app uses SQLAlchemy, so the data layer
ports with minimal change.
