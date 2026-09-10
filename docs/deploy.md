# Deploying Lesarin to the VPS (GitHub Actions → self-hosted runner)

When a pull request is merged into `main`, GitHub-hosted machines build the
Angular app and run the backend tests. Only if everything is green does the
**runner installed on the VPS** pick up the deploy job and put the release in
place locally — copy the files, refresh the Python environment, restart the
service.

There are **no SSH keys and no repository secrets**: the runner is a small
official GitHub agent that lives on the server and only makes *outbound* HTTPS
calls to GitHub asking for work. The server never has to accept an incoming
connection, and nothing secret is stored outside it. To sever the link, remove
the runner in the repo settings.

```
 merge to main ──► GitHub-hosted: build frontend (Node 24) + pytest gate
                                   │  (artifact: built UI)
                VPS runner (asks GitHub for work, outbound HTTPS only)
                                   ▼
                   copy release → /opt/lesarin → pip install → restart
                                   │
            nginx :443 (TLS) ──────┴──► uvicorn 127.0.0.1:8000
                                        data: /var/lib/lesarin/lesarin.db
```

Files involved:

| Path | Role |
| --- | --- |
| `.github/workflows/deploy.yml` | build + test on GitHub, deploy on the VPS runner |
| `deploy/setup-server.sh` | one-time VPS bootstrap (deps, users, dirs, secret, unit, nginx) |
| `deploy/deploy.sh` | server-side step each deploy: venv + deps + restart |
| `deploy/lesarin.service` | systemd unit (uvicorn) |
| `deploy/nginx-lesarin.conf` | reverse proxy (`server_name lesarin.hoj.fo`) |

## One-time server setup (~10 minutes)

On the VPS (Ubuntu/Debian), as a sudo-capable user:

```bash
# a) A dedicated 'deploy' login that will own the app dir and run the runner.
sudo adduser --disabled-password --gecos "" deploy

# b) Put the code in place once.
sudo apt-get update && sudo apt-get install -y git
sudo -u deploy git clone https://github.com/jhoj/lesarin.hoj.fo.git /opt/lesarin

# c) Bootstrap: system deps incl. OCR, service user, data dir, signing secret,
#    systemd unit, nginx site, and the scoped sudo rule for restarts.
sudo DEPLOY_USER=deploy bash /opt/lesarin/deploy/setup-server.sh
```

### Install the GitHub runner

In the repo on GitHub: **Settings → Actions → Runners → New self-hosted
runner → Linux**. GitHub shows a short block of commands with a fresh token —
run them on the VPS **as the `deploy` user** (`sudo -iu deploy`), inside e.g.
`/home/deploy/actions-runner`. Then install it as a service so it survives
reboots:

```bash
sudo ./svc.sh install deploy
sudo ./svc.sh start
```

The runner should now show as **Idle** on the Runners settings page.

### HTTPS

Point `lesarin.hoj.fo`'s A/AAAA record at the VPS, then:

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d lesarin.hoj.fo
```

## Deploy

Merge a PR into `main` (or run the **Deploy** workflow manually from the
Actions tab). Build + tests run on GitHub; the deploy job appears on your
runner seconds later. The run goes red if tests fail or the service doesn't
come back up, so a broken build never reaches users.

> Until the runner is installed, the deploy job simply waits in the queue —
> the first successful run happens as soon as the runner shows Idle.

## How data and config persist

- **Database** — `/var/lib/lesarin/lesarin.db`, *outside* the code dir, so the
  release sync never touches it. Schema changes are applied automatically by
  `init_db()` at startup (tables, canonical vocabulary, back-filled columns).
- **Token secret** — `LESARIN_SECRET` in `/etc/lesarin/lesarin.env`, generated
  once by the bootstrap. Pinning it keeps everyone logged in across deploys.
- **Backups** — the whole state is one file. A nightly
  `sqlite3 /var/lib/lesarin/lesarin.db ".backup '/var/backups/lesarin-$(date +\%F).db'"`
  cron job is plenty to start.

## Email (password reset)

Password reset needs somewhere to send mail. Add to `/etc/lesarin/lesarin.env`:

```
LESARIN_BASE_URL=https://lesarin.hoj.fo     # used to build the reset link
LESARIN_SMTP_HOST=smtp.example.fo
LESARIN_SMTP_PORT=587
LESARIN_SMTP_USER=no-reply@lesarin.hoj.fo
LESARIN_SMTP_PASSWORD=...
LESARIN_MAIL_FROM=no-reply@lesarin.hoj.fo
```

Without `LESARIN_SMTP_HOST` nothing is sent — the message is written to the log
at WARNING instead, link and all. That keeps reset working on a laptop, but on
the server it means customers can't recover their passwords, so check for it:

```bash
sudo journalctl -u lesarin | grep 'SMTP is not configured'
```

## Staff accounts

The studio edits shared vendor knowledge, so it's staff-only — and there is
deliberately no way to grant that over HTTP. Register the account normally,
then promote it on the server:

```bash
cd /opt/lesarin
sudo -u lesarin .venv/bin/python scripts/make_staff.py me@firm.fo
sudo -u lesarin .venv/bin/python scripts/make_staff.py --list
```

Everyone is a customer until promoted, including existing accounts after this
change ships.

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
sudo systemctl status lesarin --no-pager        # is the app up?
sudo journalctl -u lesarin -n 100 --no-pager    # app logs
curl -sS http://127.0.0.1:8000/health           # does the app answer locally?
sudo nginx -t && sudo systemctl reload nginx    # proxy config sane?
sudo systemctl status 'actions.runner.*'        # is the runner service up?
```

If a deploy job sits queued forever, the runner is offline — check the last
line above, or the Runners page in repo settings.

## Schema changes

Alembic owns the schema. `init_db()` applies any outstanding migrations at
startup, so a deploy needs no separate step and a fresh install needs no setup.

A database created before Alembic existed — like the SQLite file already on the
VPS — has the tables but no version row. Startup detects that and stamps it at
the baseline revision instead of trying to recreate them, so **the existing
database is adopted in place and no learned template is lost**.

To add a migration after changing a model:

```bash
python -m alembic revision --autogenerate -m "what changed"
# read the generated file before committing it — autogenerate is a draft
python -m alembic upgrade head        # or just start the app
```

`tests/test_migrations.py` fails if the models and the migrations disagree, so
a model change without a migration is caught before it reaches the server.

## Moving to Postgres

Set `LESARIN_DATABASE_URL` in `/etc/lesarin/lesarin.env` and the service uses
Postgres instead of the SQLite file:

```
LESARIN_DATABASE_URL=postgresql+psycopg://lesarin:PASSWORD@localhost/lesarin
```

Then raise `--workers` in the unit — the single worker exists only to keep
SQLite write-contention-free, and that constraint disappears with Postgres.
Migrations run at startup exactly as before. CI runs the whole suite against
both SQLite and a real Postgres, so the dialect difference is covered.

To carry the existing data over, dump the SQLite database and load it into
Postgres (`pgloader` handles this in one step), or — since vendor templates are
the only irreplaceable part — export the knowledge bundle first and import it
after:

```bash
python -m app.sync export --out brain.json     # against SQLite
# ... point LESARIN_DATABASE_URL at Postgres, start once to create the schema
python -m app.sync import brain.json           # into Postgres
```

SQLite remains fully supported and is still the default: the CLI, the TUI and
self-hosted sites all use it.
