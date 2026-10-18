#!/usr/bin/env bash
# Обновление сервиса (шаг 8.4): git pull → сборка → копия БД → миграции → перезапуск → проверка /health и страницы
# входа. Новая версия не поднялась — откат: миграции назад, прежние образы, прежний код. Сайт во время сборки работает.
#   ./scripts/deploy.sh             скачать обновление из GitHub и выложить
#   ./scripts/deploy.sh --no-pull   выложить то, что уже в папке (так его вызывает setup.sh)
#   ./scripts/deploy.sh --force     пересобрать и перезапустить, даже если версия не изменилась
set -euo pipefail
# Скрипт целиком в { … }: bash прочитает его до запуска — git pull и откат меняют этот же файл
{
cd "$(dirname "$0")/.."
COMPOSE="$(pwd)/scripts/compose.sh"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"  # секунд ждать, пока новая версия ответит

PULL=1
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --no-pull) PULL=0 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "Неизвестный параметр: $arg (см. --help)" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mОШИБКА: %s\033[0m\n' "$*" >&2; exit 1; }
env_value() { grep -E "^$1=" .env.prod | tail -n 1 | cut -d= -f2- | tr -d "\"' "; }

[ -f .env.prod ] || fail "нет .env.prod — сначала выполните sudo ./scripts/setup.sh"
DOMAIN="$(env_value DOMAIN)"
[ -n "$DOMAIN" ] || fail "в .env.prod не задан DOMAIN"

mkdir -p .deploy
PREV_VERSION="$(cat .deploy/version 2>/dev/null || true)"
PREV_COMMIT="$(git rev-parse HEAD)"

restore_code() {
  if [ "$(git rev-parse HEAD)" != "$PREV_COMMIT" ]; then
    git reset --hard -q "$PREV_COMMIT"
  fi
}

if [ "$PULL" = 1 ]; then
  say "Скачиваю обновление из GitHub"
  if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "в папке изменены файлы проекта (git status) — обновление их перезаписало бы. Верните их: git checkout -- ."
  fi
  git pull --ff-only || fail "git pull не удался — сайт работает на прежней версии"
fi
NEW_VERSION="$(git rev-parse --short=12 HEAD)"

if [ "$NEW_VERSION" = "$PREV_VERSION" ] && [ "$FORCE" = 0 ]; then
  echo "Обновлений нет — работает версия $NEW_VERSION. Пересобрать всё равно: ./scripts/deploy.sh --force"
  exit 0
fi

export APP_VERSION="$NEW_VERSION"
say "Собираю версию $NEW_VERSION (5–10 минут)"
if ! "$COMPOSE" build; then
  restore_code
  fail "сборка не удалась — сайт работает на прежней версии ${PREV_VERSION:-(нет)}"
fi

say "БД и Redis"
"$COMPOSE" up -d --wait db redis

current_revision() {
  "$COMPOSE" exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select version_num from alembic_version" 2>/dev/null' \
    | tr -d '[:space:]' || true
}
PREV_REV="$(current_revision)"

if [ -n "$PREV_REV" ]; then
  say "Копия БД перед выкладкой (на сервере, том backups)"
  if ! "$COMPOSE" run --rm --no-deps -T --entrypoint backup.sh backup predeploy; then
    restore_code
    fail "копия БД не создана — выкладку не начинаю, сайт работает на прежней версии"
  fi
fi

say "Миграции БД"
if ! "$COMPOSE" run --rm --no-deps -T api alembic upgrade head; then
  restore_code
  fail "миграции не применились (изменения БД отменены) — сайт работает на прежней версии ${PREV_VERSION:-(нет)}"
fi
NEW_REV="$(current_revision)"

# Отвечает ли сайт через Nginx и HTTPS: /health с нужной версией (БД и Redis в порядке) и страница входа
wait_healthy() {
  local version="$1" deadline=$((SECONDS + HEALTH_TIMEOUT)) body
  local curl_opts=(-fsS -k --max-time 10 --resolve "$DOMAIN:443:127.0.0.1")
  while [ "$SECONDS" -lt "$deadline" ]; do
    if body="$(curl "${curl_opts[@]}" "https://$DOMAIN/health" 2>/dev/null)" \
      && grep -q "\"version\":\"$version\"" <<<"$body" \
      && curl "${curl_opts[@]}" -o /dev/null "https://$DOMAIN/login" 2>/dev/null; then
      return 0
    fi
    sleep 3
  done
  return 1
}

say "Перезапускаю сервисы на версии $NEW_VERSION"
# Ошибка запуска (например, API не прошёл healthcheck) — не выход: дальше проверка ответа сайта и откат
"$COMPOSE" up -d --remove-orphans || true
"$COMPOSE" restart nginx || true  # настройки Nginx — файлы из репозитория: перечитать (контейнер сам не пересоздаётся)
echo "Жду ответа сайта (до $HEALTH_TIMEOUT с)…"
if wait_healthy "$NEW_VERSION"; then
  echo "$NEW_VERSION" > .deploy/version
  if [ -n "$PREV_VERSION" ] && [ "$PREV_VERSION" != "$NEW_VERSION" ]; then
    echo "$PREV_VERSION" > .deploy/previous
  fi
  # Образы: оставляем текущую и прошлую версии (для отката), остальные удаляем
  for repo in ozon-analytics/backend ozon-analytics/frontend ozon-analytics/backup; do
    { docker images "$repo" --format '{{.Tag}}' | grep -vxF -e "$NEW_VERSION" -e "${PREV_VERSION:-$NEW_VERSION}" \
      | while read -r tag; do docker rmi "$repo:$tag" >/dev/null 2>&1 || true; done; } || true  # нечего удалять
  done
  docker image prune -f >/dev/null 2>&1 || true
  say "Готово: работает версия $NEW_VERSION — https://$DOMAIN"
  exit 0
fi

echo "Новая версия не отвечает. Последние строки журналов:"
"$COMPOSE" ps || true
"$COMPOSE" logs --tail 40 api frontend nginx || true
if [ -z "$PREV_VERSION" ]; then
  fail "первый запуск не удался — смотрите журнал выше и README-DEPLOY.md, «Если сайт не открывается»"
fi

say "Откат на версию $PREV_VERSION"
if [ -n "$PREV_REV" ] && [ "$NEW_REV" != "$PREV_REV" ]; then
  # Откатывает новый код: прежний не знает новых миграций
  "$COMPOSE" run --rm --no-deps -T api alembic downgrade "$PREV_REV" \
    || echo "Миграции назад не откатились. Копия БД до выкладки — ./scripts/restore.sh (файл predeploy_…)"
fi
restore_code
APP_VERSION="$PREV_VERSION" "$COMPOSE" up -d --remove-orphans || true
"$COMPOSE" restart nginx || true
if APP_VERSION="$PREV_VERSION" wait_healthy "$PREV_VERSION"; then
  fail "версия $NEW_VERSION не заработала — вернул прежнюю $PREV_VERSION, сайт работает. Журнал выше"
fi
fail "откат на $PREV_VERSION тоже не поднялся — README-DEPLOY.md, «Если сайт не открывается»"
}
