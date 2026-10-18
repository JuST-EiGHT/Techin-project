#!/usr/bin/env bash
# Первый запуск на чистом сервере Ubuntu 24.04 / 22.04 (шаг 8.4): Docker, файрвол, защита SSH, swap, .env.prod с
# секретами, сертификат Let's Encrypt, сборка и запуск. Повторный запуск безопасен — сделанное пропускается.
#   sudo ./scripts/setup.sh                                   спросит домен и почту
#   sudo ./scripts/setup.sh --domain analytics.example.ru --email you@example.ru
# Для проверки: --self-signed — свой сертификат вместо Let's Encrypt (браузер предупредит), --no-system — не ставить
# пакеты и не трогать настройки системы (Docker уже есть).
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE="$(pwd)/scripts/compose.sh"

DOMAIN=""
EMAIL=""
SELF_SIGNED=0
SYSTEM=1
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    --self-signed) SELF_SIGNED=1; shift ;;
    --no-system) SYSTEM=0; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "Неизвестный параметр: $1 (см. --help)" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mОШИБКА: %s\033[0m\n' "$*" >&2; exit 1; }
env_value() { grep -E "^$1=" .env.prod | tail -n 1 | cut -d= -f2- | tr -d "\"' "; }
set_env() {  # заменить значение переменной в .env.prod
  local value="${2//&/\\&}"
  sed -i "s|^$1=.*|$1=${value//|/\\|}|" .env.prod
}

[ "$(id -u)" = 0 ] || fail "запустите от root: sudo ./scripts/setup.sh"

# ── 1. Система: Docker, файрвол, защита SSH, swap ──
if [ "$SYSTEM" = 1 ]; then
  say "Пакеты системы"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q ca-certificates curl git openssl ufw fail2ban unattended-upgrades

  if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
    say "Docker"
    # пакеты Ubuntu — не зависят от доступности download.docker.com из РФ; нет compose — официальный установщик
    apt-get install -y -q docker.io docker-compose-v2 docker-buildx || curl -fsSL https://get.docker.com | sh
    docker compose version >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sh
  fi
  if [ ! -f /etc/docker/daemon.json ]; then
    # зеркало Docker Hub: у Docker Hub лимит скачиваний без входа; журналы контейнеров — с ротацией
    mkdir -p /etc/docker
    cat > /etc/docker/daemon.json <<'JSON'
{
  "registry-mirrors": ["https://mirror.gcr.io"],
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "5" }
}
JSON
    systemctl restart docker
  fi
  systemctl enable --now docker >/dev/null

  if [ -z "$(swapon --show 2>/dev/null)" ]; then
    say "Файл подкачки 2 ГБ (сборка фронтенда требует памяти)"
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi

  say "Файрвол: открыты только SSH, 80 и 443"
  ufw allow OpenSSH >/dev/null
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
  ufw --force enable >/dev/null
  # fail2ban: после 5 неверных паролей SSH адрес блокируется; автообновления безопасности Ubuntu
  systemctl enable --now fail2ban >/dev/null 2>&1 || true
  systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true
fi

command -v docker >/dev/null || fail "Docker не установлен"

# ── 2. Домен и .env.prod ──
if [ -f .env.prod ]; then
  say ".env.prod уже есть — секреты не меняю"
  DOMAIN="$(env_value DOMAIN)"
  EMAIL="$(env_value LETSENCRYPT_EMAIL)"
else
  say "Настройки сервера (.env.prod)"
  if [ -z "$DOMAIN" ]; then read -rp "Домен сайта (без https://, например analytics.example.ru): " DOMAIN; fi
  if [ -z "$EMAIL" ] && [ "$SELF_SIGNED" = 0 ]; then
    read -rp "Ваша почта (для Let's Encrypt: письма о сертификате): " EMAIL
  fi
  DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%%/*}"
  [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || fail "домен «$DOMAIN» — только латинские буквы, цифры, точки и дефисы (кириллический домен — в punycode, xn--…)"
  cp .env.prod.example .env.prod
  chmod 600 .env.prod
  set_env DOMAIN "$DOMAIN"
  set_env LETSENCRYPT_EMAIL "${EMAIL:-admin@$DOMAIN}"
  set_env SITE_URL "https://$DOMAIN"
  set_env ALLOWED_ORIGINS "https://$DOMAIN"
  set_env SECRET_KEY "$(openssl rand -hex 32)"
  set_env ENCRYPTION_KEY "$(openssl rand 32 | base64 | tr '+/' '-_')"  # ключ Fernet: 32 байта в base64url
  set_env POSTGRES_PASSWORD "$(openssl rand -hex 16)"
  echo "Создан .env.prod. Сохраните его копию в менеджере паролей: без ENCRYPTION_KEY ключи Ozon из копии БД не расшифровать."
fi
[ -n "$DOMAIN" ] || fail "в .env.prod не задан DOMAIN"

# ── 3. Сертификат ──
cert_exists() {
  "$COMPOSE" run --rm --no-deps -T --entrypoint sh certbot -c "test -f /etc/letsencrypt/live/$DOMAIN/fullchain.pem" \
    >/dev/null 2>&1
}
if cert_exists; then
  say "Сертификат для $DOMAIN уже есть"
elif [ "$SELF_SIGNED" = 1 ]; then
  say "Свой (самоподписанный) сертификат для $DOMAIN — только для проверки"
  tmp="$(mktemp -d)"
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 -subj "/CN=$DOMAIN" -addext "subjectAltName=DNS:$DOMAIN" \
    -keyout "$tmp/privkey.pem" -out "$tmp/fullchain.pem" 2>/dev/null
  chmod 644 "$tmp"/*.pem
  "$COMPOSE" run --rm --no-deps -T -v "$tmp:/src:ro" --entrypoint sh certbot \
    -c "mkdir -p /etc/letsencrypt/live/$DOMAIN && cp /src/*.pem /etc/letsencrypt/live/$DOMAIN/"
  rm -rf "$tmp"
else
  say "Проверяю, что домен $DOMAIN указывает на этот сервер"
  server_ip="$(curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
  domain_ip="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1 {print $1}')"
  if [ -z "$domain_ip" ]; then
    fail "домен $DOMAIN пока никуда не указывает. Добавьте A-запись с IP сервера ${server_ip:-(см. панель хостинга)} и подождите 15–60 минут (README-DEPLOY.md, шаг 2)"
  elif [ -n "$server_ip" ] && [ "$domain_ip" != "$server_ip" ]; then
    fail "домен $DOMAIN указывает на $domain_ip, а у этого сервера адрес $server_ip. Исправьте A-запись и подождите обновления DNS"
  fi
  say "Выпускаю сертификат Let's Encrypt для $DOMAIN"
  "$COMPOSE" stop nginx >/dev/null 2>&1 || true  # порт 80 нужен certbot на время проверки
  "$COMPOSE" run --rm --no-deps -p 80:80 --entrypoint certbot certbot certonly --standalone --non-interactive \
    --agree-tos --no-eff-email -m "$EMAIL" -d "$DOMAIN" \
    || fail "сертификат не выпущен. Проверьте, что A-запись домена — IP этого сервера, порт 80 открыт, и повторите sudo ./scripts/setup.sh"
fi

# ── 4. Сборка и запуск ──
say "Сборка и запуск (первый раз — до 15 минут)"
./scripts/deploy.sh --no-pull --force

say "Сервер готов: https://$DOMAIN"
cat <<EOF
Дальше:
  1. Создайте себе пользователя:   ./scripts/admin.sh create-user ваш@email --name "Ваше имя"
     (пароль покажется один раз — войдите и смените его в меню аккаунта → «Аккаунт»)
  2. Telegram-бот, резервные копии в Object Storage, Sentry — впишите в .env.prod (nano .env.prod),
     затем: ./scripts/compose.sh up -d          (README-DEPLOY.md, шаги 6–8)
  3. Обновление сервиса:  ./scripts/deploy.sh
EOF
