#!/bin/sh
set -eu

# Docker secrets are mounted as files. Load only the explicitly supported
# variables, without printing their values into container logs.
load_secret() {
    variable_name="$1"
    eval "secret_file=\${${variable_name}_FILE:-}"
    if [ -n "$secret_file" ]; then
        if [ ! -r "$secret_file" ]; then
            echo "Secret file for $variable_name is not readable: $secret_file" >&2
            exit 1
        fi
        secret_value=$(cat "$secret_file")
        export "$variable_name=$secret_value"
    fi
}

for variable_name in \
    DATABASE_URL \
    DASHBOARD_ADMIN_PASSWORD \
    CREDENTIAL_ENCRYPTION_KEY \
    COMPOSIO_API_KEY \
    OPENAI_API_KEY \
    ANTHROPIC_API_KEY \
    REDDIT_CLIENT_SECRET \
    GITHUB_TOKEN \
    PRODUCTHUNT_TOKEN \
    BRAVE_SEARCH_API_KEY
do
    load_secret "$variable_name"
done

gosu app alembic upgrade head
exec gosu app uvicorn app.main:app --host 0.0.0.0 --port 8000
