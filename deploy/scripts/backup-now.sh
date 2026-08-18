#!/bin/sh
set -eu

backup_root=/backups
vector_root=/vector-data
retention_days=${BACKUP_RETENTION_DAYS:-14}

case "$retention_days" in
    ''|*[!0-9]*) echo "BACKUP_RETENTION_DAYS must be a non-negative integer." >&2; exit 1 ;;
esac

if [ ! -r "${POSTGRES_PASSWORD_FILE:-}" ]; then
    echo "POSTGRES_PASSWORD_FILE is not readable." >&2
    exit 1
fi

export PGPASSWORD
PGPASSWORD=$(cat "$POSTGRES_PASSWORD_FILE")

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
temporary_dir=$(mktemp -d "$backup_root/.incomplete-XXXXXX")
final_dir="$backup_root/$timestamp"

cleanup() {
    if [ -n "${temporary_dir:-}" ] && [ -d "$temporary_dir" ]; then
        rm -rf -- "$temporary_dir"
    fi
}
trap cleanup EXIT INT TERM

echo "Creating PostgreSQL backup $timestamp"
pg_dump \
    --format=custom \
    --no-owner \
    --no-privileges \
    --file="$temporary_dir/postgres.dump"

echo "Creating vector-data backup $timestamp"
tar -C "$vector_root" -czf "$temporary_dir/vector-data.tar.gz" .

cat > "$temporary_dir/metadata.txt" <<EOF
created_at_utc=$timestamp
database=$PGDATABASE
database_host=$PGHOST
retention_days=$retention_days
EOF

(
    cd "$temporary_dir"
    sha256sum postgres.dump vector-data.tar.gz metadata.txt > SHA256SUMS
)

mv "$temporary_dir" "$final_dir"
temporary_dir=

# Only timestamp-shaped backup directories inside /backups are eligible.
find "$backup_root" -mindepth 1 -maxdepth 1 -type d \
    -name '20??????T??????Z' -mtime "+$retention_days" -exec rm -rf -- {} +

