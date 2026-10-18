#!/usr/bin/env bash
# Команды администратора (шаг 8.4) — в контейнере api: пользователи, пароли, приглашения.
#   ./scripts/admin.sh create-user you@mail.ru --name "Имя"   создать пользователя (пароль покажет один раз)
#   ./scripts/admin.sh reset-password you@mail.ru              новый пароль, выход на всех устройствах
#   ./scripts/admin.sh invite --email friend@mail.ru           приглашение: код и ссылка
#   ./scripts/admin.sh invites | users | revoke-invite ID | deactivate EMAIL | activate EMAIL | unblock EMAIL
#   ./scripts/admin.sh --help                                  все команды
set -euo pipefail
exec "$(dirname "$0")/compose.sh" exec api python -m app.admin "$@"
