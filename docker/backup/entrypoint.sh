#!/bin/sh
# Расписание резервных копий (время — UTC, Москва = UTC+3):
#   каждые 6 часов в :15 (03:15, 09:15, 15:15, 21:15 по Москве) — копия БД (backup.sh);
#   1-го числа в 03:45 UTC — проверка восстановления последней копии в отдельную БД (restore-check.sh).
# crond запускает задания без переменных контейнера — сохраняем их в файл, доступный только root.
set -eu
umask 077
export -p > /etc/backup.env
mkdir -p /backups
cat > /etc/crontabs/root <<CRON
${BACKUP_CRON:-15 */6 * * *} . /etc/backup.env; /usr/local/bin/backup.sh >> /proc/1/fd/1 2>&1
${RESTORE_CHECK_CRON:-45 3 1 * *} . /etc/backup.env; /usr/local/bin/restore-check.sh >> /proc/1/fd/1 2>&1
CRON
echo "Резервные копии: расписание — $(head -n1 /etc/crontabs/root | cut -d' ' -f1-5) (UTC)," \
  "проверка восстановления — $(sed -n 2p /etc/crontabs/root | cut -d' ' -f1-5)"
if [ -z "${BACKUP_S3_BUCKET:-}" ]; then
  echo "ВНИМАНИЕ: Object Storage не настроен (BACKUP_S3_* в .env.prod) — копии только на этом сервере"
fi
exec crond -f -l 8
