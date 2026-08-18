#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
deploy_dir="$project_root/deploy"
secret_dir="$deploy_dir/secrets"
production_env="$deploy_dir/production.env"

if ! command -v openssl >/dev/null 2>&1; then
    echo "openssl is required to generate production secrets." >&2
    exit 1
fi

umask 077
mkdir -p "$secret_dir"
chmod 700 "$secret_dir"

if [ ! -f "$production_env" ]; then
    cp "$deploy_dir/production.env.example" "$production_env"
    echo "Created deploy/production.env; edit APP_DOMAIN and ACME_EMAIL before deployment."
fi

write_generated_secret() {
    target="$1"
    value="$2"
    if [ ! -s "$target" ]; then
        printf '%s' "$value" > "$target"
        chmod 600 "$target"
    fi
}

postgres_password=$(openssl rand -hex 24)
write_generated_secret "$secret_dir/postgres_password" "$postgres_password"
postgres_password=$(cat "$secret_dir/postgres_password")

# Keep the SQLAlchemy URL synchronized with the generated database password.
# The generated password is hexadecimal and therefore URL-safe.
if [ ! -s "$secret_dir/database_url" ]; then
    printf 'postgresql+asyncpg://linkedin:%s@postgres:5432/linkedin_manager' "$postgres_password" \
        > "$secret_dir/database_url"
    chmod 600 "$secret_dir/database_url"
fi

write_generated_secret "$secret_dir/dashboard_admin_password" "$(openssl rand -base64 36 | tr -d '\n')"
write_generated_secret "$secret_dir/credential_encryption_key" "$(openssl rand 32 | base64 | tr '+/' '-_' | tr -d '\n')"

# Optional provider files must exist for Compose but may remain empty until used.
for secret_name in \
    composio_api_key \
    openai_api_key \
    anthropic_api_key \
    reddit_client_secret \
    github_token \
    producthunt_token \
    brave_search_api_key
do
    secret_file="$secret_dir/$secret_name"
    if [ ! -e "$secret_file" ]; then
        : > "$secret_file"
        chmod 600 "$secret_file"
    fi
done

cat <<'EOF'
Production secret files are ready in deploy/secrets/.

Before deployment:
  1. Edit deploy/production.env with the real domain and ACME email.
  2. Put required provider keys into the matching secret files.
  3. Store an encrypted offline copy of deploy/secrets/.

The generated administrator password is stored at:
  deploy/secrets/dashboard_admin_password

Do not commit or paste any file from deploy/secrets/ into logs or chat.
EOF
