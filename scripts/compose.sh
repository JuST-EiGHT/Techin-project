#!/usr/bin/env bash
# Docker Compose прод-сборки с настройками .env.prod и текущей версией (шаг 8.4). Примеры:
#   ./scripts/compose.sh ps                 что запущено и в каком состоянии
#   ./scripts/compose.sh logs -f --tail 100 api    журнал API (Ctrl+C — выйти)
#   ./scripts/compose.sh up -d              применить изменения .env.prod
#   ./scripts/compose.sh restart worker     перезапустить воркер
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f .env.prod ]; then
  echo "Нет файла .env.prod — сначала выполните: sudo ./scripts/setup.sh" >&2
  exit 1
fi
# Версия, которую выложил scripts/deploy.sh (коммит): образы ozon-analytics/*:<версия>
if [ -z "${APP_VERSION:-}" ] && [ -f .deploy/version ]; then
  APP_VERSION="$(cat .deploy/version)"
  export APP_VERSION
fi
exec docker compose --env-file .env.prod -f docker-compose.prod.yml "$@"
