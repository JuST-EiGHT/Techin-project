#!/bin/sh
# Общее для скриптов резервных копий: Object Storage через rclone, уведомление о сбое, журнал.
# Переменные — из .env.prod: BACKUP_S3_ENDPOINT, BACKUP_S3_REGION, BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY,
# BACKUP_S3_SECRET_KEY, BACKUP_S3_PREFIX; SENTRY_DSN — о сбое; BACKUP_HEARTBEAT_URL — «копия сделана».
# БД — PGHOST, PGUSER, PGPASSWORD, PGDATABASE (задаёт docker-compose.prod.yml).

LOCAL_DIR=/backups
PREFIX="${BACKUP_S3_PREFIX:-ozon-analytics}"

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S') [backup] $*"; }

s3_enabled() { [ -n "${BACKUP_S3_BUCKET:-}" ]; }

# rclone без файла настроек: удалённое хранилище «store» — из переменных окружения
s3_setup() {
  export RCLONE_CONFIG_STORE_TYPE=s3 RCLONE_LOG_LEVEL=ERROR  # без NOTICE о файле настроек
  export RCLONE_CONFIG_STORE_PROVIDER=Other
  export RCLONE_CONFIG_STORE_ENDPOINT="${BACKUP_S3_ENDPOINT:-}"
  export RCLONE_CONFIG_STORE_REGION="${BACKUP_S3_REGION:-}"
  export RCLONE_CONFIG_STORE_ACCESS_KEY_ID="${BACKUP_S3_ACCESS_KEY:-}"
  export RCLONE_CONFIG_STORE_SECRET_ACCESS_KEY="${BACKUP_S3_SECRET_KEY:-}"
  export RCLONE_CONFIG_STORE_NO_CHECK_BUCKET=true  # бакет создан заранее; права на создание не нужны
  export RCLONE_CONFIG_STORE_FORCE_PATH_STYLE="${BACKUP_S3_PATH_STYLE:-true}"
}

remote() { echo "store:${BACKUP_S3_BUCKET}/${PREFIX}/$1"; }

# Оставить в папке хранилища только $2 последних копий (имена с датой — сортируются по времени)
prune_remote() {
  dir="$1"; keep="$2"
  list=$(rclone lsf --files-only "$(remote "$dir")" | grep '\.dump$' | sort || true)
  total=$(printf '%s\n' "$list" | grep -c . || true)
  extra=$((total - keep))
  [ "$extra" -gt 0 ] || return 0
  printf '%s\n' "$list" | head -n "$extra" | while read -r name; do
    rclone deletefile "$(remote "$dir")/$name" && log "удалена старая копия $dir/$name"
  done
}

prune_local() {
  pattern="$1"; keep="$2"
  # shellcheck disable=SC2012,SC2086
  ls -1 "$LOCAL_DIR"/$pattern 2>/dev/null | sort \
    | awk -v keep="$keep" '{ f[NR] = $0 } END { for (i = 1; i <= NR - keep; i++) print f[i] }' \
    | while read -r f; do rm -f "$f"; done
}

# Сообщить о сбое в Sentry (если задан SENTRY_DSN): событие уровня error, без данных из БД
notify_failure() {
  msg="$1"
  log "ОШИБКА: $msg"
  [ -n "${SENTRY_DSN:-}" ] || return 0
  proto="${SENTRY_DSN%%://*}"; rest="${SENTRY_DSN#*://}"
  key="${rest%%@*}"; key="${key%%:*}"; hostpath="${rest#*@}"
  project="${hostpath##*/}"; base="${hostpath%/*}"
  text=$(printf '%s' "$msg" | tr '\n\r\t' '   ' | sed 's/\\/\\\\/g; s/"/\\"/g')
  curl -fsS -m 15 -X POST "$proto://$base/api/$project/store/" \
    -H "Content-Type: application/json" \
    -H "X-Sentry-Auth: Sentry sentry_version=7, sentry_key=$key, sentry_client=ozon-backup/1.0" \
    -d "{\"message\": \"$text\", \"level\": \"error\", \"logger\": \"backup\", \"platform\": \"other\", \"environment\": \"${ENVIRONMENT:-production}\", \"tags\": {\"component\": \"backup\"}}" \
    >/dev/null || log "не удалось отправить ошибку в Sentry"
}

heartbeat() {
  [ -n "${BACKUP_HEARTBEAT_URL:-}" ] || return 0
  curl -fsS -m 15 "$BACKUP_HEARTBEAT_URL" >/dev/null || log "не удалось отметиться в мониторинге ($BACKUP_HEARTBEAT_URL)"
}
