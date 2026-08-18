# Production VPS deployment

This stack is intended for a private, single-workspace testing deployment on a VPS. It publishes only Caddy on HTTP/HTTPS. The frontend, FastAPI backend, PostgreSQL, Redis, and backup service remain on Docker networks and have no host port bindings.

```text
Internet -> Caddy :80/:443 -> frontend:80
                         \-> /api/* -> backend:8000
backend -> private PostgreSQL + Redis
backup  -> private PostgreSQL + read-only vector data -> backup volume
```

## Prerequisites

- A VPS with Docker Engine and the Docker Compose plugin.
- A domain or subdomain whose `A` record points to the VPS public IPv4 address. Add an `AAAA` record only when IPv6 is configured and reachable.
- TCP ports 80 and 443, plus UDP 443, open to the internet.
- SSH restricted to your own address where practical.

Do not start Caddy until DNS points to the VPS. Caddy obtains and renews the TLS certificate automatically.

## Prepare the VPS

Clone the repository, enter its directory, and initialize the ignored production files:

```bash
./deploy/scripts/init-secrets.sh
```

This creates `deploy/production.env` and `deploy/secrets/` with mode `0600`. It does not overwrite existing non-empty secret files.

Edit the non-secret deployment settings:

```bash
nano deploy/production.env
```

At minimum, replace:

```env
APP_DOMAIN=linkedin.example.com
ACME_EMAIL=admin@example.com
```

Place any provider credentials you need into their matching files under `deploy/secrets/`. Do not add quotes or trailing spaces. Empty optional files are allowed.

```text
composio_api_key
openai_api_key
anthropic_api_key
reddit_client_secret
github_token
producthunt_token
brave_search_api_key
```

Choose the matching `LLM_PROVIDER` in `deploy/production.env`. The generated dashboard password is in `deploy/secrets/dashboard_admin_password`. Read it directly on the VPS; never paste it into source control or deployment logs.

Keep an encrypted offline copy of `deploy/secrets/credential_encryption_key`. Losing it makes credentials saved through the Connections screen unreadable.

## Firewall

First confirm that a second SSH session works. Then allow SSH before enabling the firewall. On Ubuntu with UFW:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable
sudo ufw status
```

Restrict SSH to a trusted source address if that address is stable. Do not open 5173, 8010, 5432, 5433, or 6379. Also apply the equivalent rules in the VPS provider firewall.

## Validate and deploy

Use the production environment file for every Compose command:

```bash
docker compose --env-file deploy/production.env -f compose.production.yml config --quiet
docker compose --env-file deploy/production.env -f compose.production.yml build
docker compose --env-file deploy/production.env -f compose.production.yml up -d
docker compose --env-file deploy/production.env -f compose.production.yml ps
```

Open `https://YOUR_DOMAIN` and sign in with `DASHBOARD_ADMIN_USERNAME` and the generated administrator password.

Useful checks:

```bash
curl -fsS https://YOUR_DOMAIN/api/health
curl -I https://YOUR_DOMAIN
curl -o /dev/null -sS -w '%{http_code}\n' https://YOUR_DOMAIN/api/docs
docker compose --env-file deploy/production.env -f compose.production.yml logs --tail=100 caddy backend
```

The health endpoint should return `{"status":"ok"}`. `/api/docs`, `/api/redoc`, and `/api/openapi.json` should return `404` in production.

## Updating

Back up first, pull the approved revision, rebuild, and recreate the services:

```bash
docker compose --env-file deploy/production.env -f compose.production.yml exec backup /scripts/backup-now.sh
git pull --ff-only
docker compose --env-file deploy/production.env -f compose.production.yml up -d --build
docker compose --env-file deploy/production.env -f compose.production.yml ps
```

Database migrations run automatically before the backend starts.

## Backups and retention

The backup service starts with the stack, creates a backup immediately, repeats every `BACKUP_INTERVAL_SECONDS`, and removes timestamped backup directories older than `BACKUP_RETENTION_DAYS`. Defaults are one backup per day and fourteen days of retention.

Each backup contains:

- A PostgreSQL custom-format dump.
- A compressed vector-data archive.
- Metadata and SHA-256 checksums.

Run an extra backup:

```bash
docker compose --env-file deploy/production.env -f compose.production.yml exec backup /scripts/backup-now.sh
```

List backups:

```bash
docker compose --env-file deploy/production.env -f compose.production.yml exec backup find /backups -mindepth 1 -maxdepth 1 -type d -name '20*Z' -printf '%f\n'
```

The Docker backup volume protects against an application failure, not total VPS loss. Regularly copy encrypted backups to a different provider or storage account and test restoring them.

## Restore

A restore replaces the current database and vector index. Take a VPS snapshot or another backup first. Stop writers, keep PostgreSQL running, and invoke the opt-in restore profile:

```bash
docker compose --env-file deploy/production.env -f compose.production.yml stop backend backup
docker compose --env-file deploy/production.env -f compose.production.yml up -d postgres
docker compose --env-file deploy/production.env -f compose.production.yml --profile restore run --rm -e RESTORE_CONFIRM=restore restore latest
docker compose --env-file deploy/production.env -f compose.production.yml up -d backend backup
```

Replace `latest` with an exact UTC directory name to restore a specific backup, for example `20260818T120000Z`. The restore refuses to run without the confirmation variable and verifies all checksums before changing data.

## Secret rotation

- Administrator password: replace `deploy/secrets/dashboard_admin_password`, then recreate the backend. Startup rotates the stored admin hash and revokes existing sessions.
- Provider API key: replace the matching secret file and recreate the backend.
- PostgreSQL password: requires coordinated rotation of both `postgres_password` and `database_url`; schedule downtime and back up first.
- Credential encryption key: do not replace it unless all encrypted stored credentials are migrated or intentionally discarded.

Recreate the backend after an ordinary secret update:

```bash
docker compose --env-file deploy/production.env -f compose.production.yml up -d --force-recreate backend
```

## Security properties

- Caddy is the only service with published host ports.
- Browser traffic uses one HTTPS origin and `/api`, avoiding public backend ports.
- Session cookies are Secure, HttpOnly, and SameSite=Strict.
- CORS accepts only `https://APP_DOMAIN`.
- Interactive API documentation and the OpenAPI schema are disabled in production.
- Runtime secrets are mounted from ignored files and loaded without being printed.
- Containers use restart policies, health checks, and `no-new-privileges`.
- PostgreSQL and Redis are isolated on an internal Docker network.

Review container logs, operating-system security updates, disk usage, certificate renewal, and off-site backup status regularly.
