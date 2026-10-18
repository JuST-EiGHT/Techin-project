#!/usr/bin/env bash
# Восстановление рабочей БД из резервной копии (шаг 8.4). ВНИМАНИЕ: текущие данные заменяются копией — всё, что
# появилось после неё, пропадёт (начисления Ozon догрузятся синхронизацией, снимки цен и остатков за этот промежуток —
# нет). Перед заменой текущая БД сохраняется (predeploy_…), так что шаг можно отменить тем же скриптом.
#   ./scripts/restore.sh                                          список копий: на сервере и в Object Storage
#   ./scripts/restore.sh daily/ozon_2026-10-18_001500.dump       восстановить копию из Object Storage
#   ./scripts/restore.sh ozon_2026-10-18_061500.dump             восстановить копию с сервера
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="$(pwd)/scripts/compose.sh"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mОШИБКА: %s\033[0m\n' "$*" >&2; exit 1; }
backup_run() { "$COMPOSE" run --rm --no-deps -T --entrypoint "$1" backup "${@:2}"; }

if [ $# -eq 0 ]; then
  say "Копии на сервере (том backups)"
  backup_run sh -c 'ls -1 /backups/*.dump 2>/dev/null | xargs -r -n1 basename || echo "нет"'
  say "Копии в Object Storage"
  backup_run sh -c '. /usr/local/bin/lib.sh
    if s3_enabled; then s3_setup; for d in monthly daily 6h; do rclone lsf --files-only "$(remote $d)" | sed "s|^|$d/|"; done
    else echo "не настроено (BACKUP_S3_* в .env.prod)"; fi'
  echo
  echo "Восстановить: ./scripts/restore.sh <имя копии из списка>"
  exit 0
fi

SRC="$1"
DOMAIN="$(grep -E '^DOMAIN=' .env.prod | tail -n 1 | cut -d= -f2- | tr -d "\"' ")"
echo "Рабочая БД будет заменена копией $SRC. Всё, что появилось после этой копии, пропадёт."
read -rp "Чтобы продолжить, напишите ВОССТАНОВИТЬ: " answer
[ "$answer" = "ВОССТАНОВИТЬ" ] || fail "отменено"

"$COMPOSE" up -d --wait db
say "Сохраняю текущую БД (predeploy_…) — на случай, если копия окажется не той"
backup_run backup.sh predeploy || fail "текущую БД сохранить не удалось — восстановление не начинаю"

say "Останавливаю сервисы, которые пишут в БД"
"$COMPOSE" stop api worker beat bot
if ! backup_run restore-db.sh "$SRC"; then
  echo "Восстановление не удалось. Вернуть БД как было: ./scripts/restore.sh <последний predeploy_… из списка>"
  "$COMPOSE" up -d
  fail "копия $SRC не восстановилась"
fi

say "Миграции (копия могла быть сделана до обновления)"
"$COMPOSE" run --rm --no-deps -T api alembic upgrade head
"$COMPOSE" up -d
say "Готово: БД восстановлена из $SRC. Проверьте сайт: https://$DOMAIN"
