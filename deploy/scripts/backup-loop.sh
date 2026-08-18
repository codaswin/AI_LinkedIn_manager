#!/bin/sh
set -eu

interval=${BACKUP_INTERVAL_SECONDS:-86400}
case "$interval" in
    ''|*[!0-9]*|0) echo "BACKUP_INTERVAL_SECONDS must be a positive integer." >&2; exit 1 ;;
esac

while true; do
    if /scripts/backup-now.sh; then
        touch /tmp/backup-service-ready
    else
        rm -f /tmp/backup-service-ready
        echo "Backup failed; the container will restart and retry." >&2
        exit 1
    fi
    sleep "$interval"
done
