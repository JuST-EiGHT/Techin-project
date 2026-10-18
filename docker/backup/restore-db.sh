#!/bin/sh
# Восстановление рабочей БД из копии — только через scripts/restore.sh (он останавливает сервисы, спрашивает
# подтверждение и делает копию текущей БД). Аргумент — имя файла: на сервере (/backups/…) или в Object Storage
# (6h/…, daily/…, monthly/…).
set -eu
. /usr/local/bin/lib.sh

SRC="${1:?укажите копию: имя файла из /backups или 6h/…, daily/…, monthly/… из Object Storage}"
TMP=/tmp/restore.dump
trap 'rm -f "$TMP"' EXIT

case "$SRC" in
  6h/*|daily/*|monthly/*)
    s3_enabled || { log "Object Storage не настроен (BACKUP_S3_* в .env.prod)"; exit 1; }
    s3_setup
    log "скачиваю $SRC из Object Storage"
    rclone copyto "$(remote "${SRC%%/*}")/${SRC#*/}" "$TMP"
    ;;
  *)
    FILE="$LOCAL_DIR/$(basename "$SRC")"
    [ -f "$FILE" ] || { log "нет файла $FILE"; exit 1; }
    cp "$FILE" "$TMP"
    ;;
esac
pg_restore --list "$TMP" >/dev/null

log "пересоздаю БД $PGDATABASE и восстанавливаю из $SRC"
dropdb --force "$PGDATABASE"
createdb "$PGDATABASE"
pg_restore --no-owner --no-privileges --exit-on-error --dbname="$PGDATABASE" "$TMP"
log "БД восстановлена из $SRC"
