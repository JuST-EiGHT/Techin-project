#!/bin/sh
# Проверка восстановления (шаг 8.4): последняя копия разворачивается в отдельную БД restore_check (рабочая не
# затрагивается), сверяются таблицы и число строк с рабочей БД, проверочная БД удаляется. Раз в месяц по расписанию,
# вручную — ./scripts/compose.sh exec backup restore-check.sh. Сбой — в Sentry.
set -eu
. /usr/local/bin/lib.sh

CHECK_DB=restore_check
TMP=/tmp/restore_check.dump
trap 'rm -f "$TMP"; dropdb --if-exists "$CHECK_DB" >/dev/null 2>&1 || true' EXIT

if s3_enabled; then
  s3_setup
  NAME=$(rclone lsf --files-only "$(remote 6h)" | grep '\.dump$' | sort | tail -n 1)
  [ -n "$NAME" ] || { notify_failure "Проверка восстановления: в Object Storage нет копий"; exit 1; }
  log "проверка восстановления: 6h/$NAME из Object Storage"
  if ! out=$(rclone copyto "$(remote 6h)/$NAME" "$TMP" 2>&1); then
    notify_failure "Проверка восстановления: копия не скачалась: $out"
    exit 1
  fi
else
  NAME=$(ls -1 "$LOCAL_DIR"/ozon_*.dump 2>/dev/null | sort | tail -n 1 || true)
  [ -n "$NAME" ] || { notify_failure "Проверка восстановления: копий нет"; exit 1; }
  log "проверка восстановления: $(basename "$NAME") с сервера (Object Storage не настроен)"
  cp "$NAME" "$TMP"
fi

dropdb --if-exists "$CHECK_DB" 2>/dev/null
createdb "$CHECK_DB"
if ! out=$(pg_restore --no-owner --no-privileges --exit-on-error --dbname="$CHECK_DB" "$TMP" 2>&1); then
  notify_failure "Проверка восстановления: копия $NAME не восстановилась: $out"
  exit 1
fi

count() { psql -d "$1" -tAc "$2" 2>/dev/null | tr -d ' '; }
problems=""
tables_live=$(count "$PGDATABASE" "select count(*) from information_schema.tables where table_schema='public'")
tables_copy=$(count "$CHECK_DB" "select count(*) from information_schema.tables where table_schema='public'")
[ "$tables_live" = "$tables_copy" ] || problems="$problems таблиц в копии $tables_copy, в рабочей $tables_live;"
rev_live=$(count "$PGDATABASE" "select version_num from alembic_version")
rev_copy=$(count "$CHECK_DB" "select version_num from alembic_version")
[ "$rev_live" = "$rev_copy" ] || log "версия схемы: копия $rev_copy, рабочая $rev_live (копия сделана до выкладки — это нормально)"
summary=""
for t in users products product_prices product_stocks accruals reports report_files product_costs audit_log money_operations; do
  live=$(count "$PGDATABASE" "select count(*) from $t") || live=""
  copy=$(count "$CHECK_DB" "select count(*) from $t") || copy=""
  [ -n "$live" ] && [ -n "$copy" ] || { problems="$problems нет таблицы $t;"; continue; }
  summary="$summary $t=$copy/$live"
  # копия старше рабочей БД: строк столько же или меньше, но пустой при непустой рабочей — быть не может
  if [ "$copy" -gt "$live" ] || { [ "$live" -gt 0 ] && [ "$copy" -eq 0 ]; }; then
    problems="$problems $t: в копии $copy строк, в рабочей $live;"
  fi
done
log "строк (копия/рабочая):$summary"

if [ -n "$problems" ]; then
  notify_failure "Проверка восстановления $NAME: расхождения:$problems"
  echo "$(date -u +%Y-%m-%d_%H%M%S) $NAME failed" > "$LOCAL_DIR/last_restore_check.txt"
  exit 1
fi
echo "$(date -u +%Y-%m-%d_%H%M%S) $NAME ok" > "$LOCAL_DIR/last_restore_check.txt"
log "проверка восстановления пройдена: $NAME"
