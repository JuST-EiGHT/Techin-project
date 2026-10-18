# Резервные копии (шаг 8.4): pg_dump той же версии, что и сервер (postgres:16), rclone — выгрузка в Object Storage
# (S3), crond из busybox — расписание. Скрипты — docker/backup/*.sh, описание — README-DEPLOY.md, «Резервные копии».
FROM postgres:16-alpine
RUN apk add --no-cache rclone curl
COPY lib.sh backup.sh restore-check.sh restore-db.sh entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/*.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
