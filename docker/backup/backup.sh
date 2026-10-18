#!/bin/sh
# Резервная копия БД (шаг 8.4, docs/DATA_HISTORY.md): pg_dump в формате custom (сжат, восстанавливается pg_restore).
# В Object Storage: 6h/ — каждые 6 часов, 30 последних (неделя); daily/ — первая копия дня, 30 дней; monthly/ — первая
# копия месяца, 12 месяцев. На сервере (/backups, том backups) — 4 последние копии для быстрого восстановления.
# Файлы отчётов и выписок хранятся в БД — входят в копию.
#   backup.sh            — по расписанию (crond) или вручную: ./scripts/compose.sh exec backup backup.sh
#   backup.sh predeploy  — только на сервер, перед выкладкой (scripts/deploy.sh), 5 последних
set -eu
. /usr/local/bin/lib.sh

KIND="${1:-scheduled}"
STAMP=$(date -u +%Y-%m-%d_%H%M%S)
mkdir -p "$LOCAL_DIR"

if [ "$KIND" = "predeploy" ]; then
  FILE="$LOCAL_DIR/predeploy_${STAMP}.dump"
else
  FILE="$LOCAL_DIR/ozon_${STAMP}.dump"
fi

log "копия БД $PGDATABASE → $(basename "$FILE")"
if ! out=$(pg_dump --format=custom --compress=6 --file="$FILE.part" 2>&1); then
  rm -f "$FILE.part"
  notify_failure "Резервная копия не создана: pg_dump: $out"
  exit 1
fi
# Копия читается — иначе это не копия
if ! out=$(pg_restore --list "$FILE.part" 2>&1 >/dev/null); then
  rm -f "$FILE.part"
  notify_failure "Резервная копия повреждена: $out"
  exit 1
fi
mv "$FILE.part" "$FILE"
log "готово: $(du -h "$FILE" | cut -f1)"

if [ "$KIND" = "predeploy" ]; then
  prune_local 'predeploy_*.dump' 5
  exit 0
fi
prune_local 'ozon_*.dump' 4

if ! s3_enabled; then
  log "Object Storage не настроен — копия только на этом сервере (настройте BACKUP_S3_* в .env.prod)"
  echo "$STAMP local-only" > "$LOCAL_DIR/last_backup.txt"
  heartbeat
  exit 0
fi

s3_setup
NAME=$(basename "$FILE")
DAY=$(date -u +%Y-%m-%d)
MONTH=$(date -u +%Y-%m)
upload() {
  if ! out=$(rclone copyto "$FILE" "$(remote "$1")/$NAME" 2>&1); then
    notify_failure "Копия не выгружена в Object Storage ($1): $out"
    exit 1
  fi
  log "выгружено: $1/$NAME"
}
upload 6h
if ! rclone lsf --files-only "$(remote daily)" 2>/dev/null | grep -q "^ozon_${DAY}_"; then
  upload daily
fi
if ! rclone lsf --files-only "$(remote monthly)" 2>/dev/null | grep -q "^ozon_${MONTH}-"; then
  upload monthly
fi
prune_remote 6h 30
prune_remote daily 30
prune_remote monthly 12

echo "$STAMP ok" > "$LOCAL_DIR/last_backup.txt"
heartbeat
log "резервная копия готова"
