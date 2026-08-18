#!/bin/sh
set -eu

backup_root=/backups
vector_root=/vector-data
requested_backup=${1:-latest}

if [ "${RESTORE_CONFIRM:-}" != "restore" ]; then
    echo "Restore refused. Set RESTORE_CONFIRM=restore after stopping the backend." >&2
    exit 2
fi

if [ ! -r "${POSTGRES_PASSWORD_FILE:-}" ]; then
    echo "POSTGRES_PASSWORD_FILE is not readable." >&2
    exit 1
fi

if [ "$requested_backup" = "latest" ]; then
    requested_backup=$(
        find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name '20??????T??????Z' \
            -printf '%f\n' | sort | tail -n 1
    )
fi

case "$requested_backup" in
    20??????T??????Z) ;;
    *) echo "Backup must be 'latest' or a UTC timestamp such as 20260818T120000Z." >&2; exit 1 ;;
esac

backup_dir="$backup_root/$requested_backup"
if [ ! -d "$backup_dir" ]; then
    echo "Backup does not exist: $requested_backup" >&2
    exit 1
fi

for required_file in postgres.dump vector-data.tar.gz metadata.txt SHA256SUMS; do
    if [ ! -f "$backup_dir/$required_file" ]; then
        echo "Backup is incomplete; missing $required_file" >&2
        exit 1
    fi
done

echo "Verifying backup integrity"
(
    cd "$backup_dir"
    sha256sum -c SHA256SUMS
)

export PGPASSWORD
PGPASSWORD=$(cat "$POSTGRES_PASSWORD_FILE")

echo "Restoring PostgreSQL from $requested_backup"
pg_restore \
    --exit-on-error \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --dbname="$PGDATABASE" \
    "$backup_dir/postgres.dump"

# Refuse replacement unless Compose mounted the exact expected vector path.
if [ "$vector_root" != "/vector-data" ] || [ ! -d "$vector_root" ]; then
    echo "Unexpected vector-data mount; refusing replacement." >&2
    exit 1
fi

echo "Restoring vector data from $requested_backup"
find "$vector_root" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
tar -C "$vector_root" -xzf "$backup_dir/vector-data.tar.gz"

echo "Restore completed from $requested_backup"
