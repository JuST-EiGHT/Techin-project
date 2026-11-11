#!/usr/bin/env bash
# Файл подкачки (swap): часть диска работает как запасная оперативная память. Нужен серверу с 2 ГБ памяти — без него
# сборка при обновлении занимает всю память, Redis перестаёт принимать запись, фоновые службы падают по кругу.
# Доводит весь swap сервера до нужного размера (уже имеющийся swap провайдера считается), включает его сразу и после
# перезагрузки, и включает настройку памяти, которую требует Redis. Повторный запуск безопасен.
#   sudo ./scripts/swap.sh              swap всего 2,5 ГБ (по умолчанию)
#   sudo ./scripts/swap.sh 4G           swap всего 4 ГБ
#   ./scripts/swap.sh --dry-run         показать, что будет сделано, ничего не меняя
set -euo pipefail

SWAP_FILE="${SWAP_FILE:-/swapfile-ozon}"   # отдельный файл: swap провайдера не трогаем (выключать его опасно)
PROC_SWAPS="${PROC_SWAPS:-/proc/swaps}"
SYSCTL_FILE="${SYSCTL_FILE:-/etc/sysctl.d/90-ozon-analytics.conf}"
DISK_RESERVE_MB=3072                       # на диске после создания должно остаться 3 ГБ — для обновлений и копий БД
TARGET="2560M"
DRY=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *)
      if [[ "$arg" =~ ^[0-9]+[MmGg]$ ]]; then
        TARGET="$arg"
      else
        echo "Неизвестный параметр: $arg (размер — например 2560M или 4G; см. --help)" >&2
        exit 2
      fi ;;
  esac
done

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mОШИБКА: %s\033[0m\n' "$*" >&2; exit 1; }
run() { if [ "$DRY" = 1 ]; then echo "  [проверка] $*"; else "$@"; fi; }

# «2560M» / «4G» → мегабайты
to_mb() {
  local n="${1%[MmGg]}" unit="${1: -1}"
  case "$unit" in
    G|g) echo $((n * 1024)) ;;
    *) echo "$n" ;;
  esac
}

target_mb="$(to_mb "$TARGET")"
[ "$target_mb" -ge 512 ] && [ "$target_mb" -le 16384 ] || fail "размер swap — от 512M до 16G, указано $TARGET"
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || fail "запустите от root: sudo ./scripts/swap.sh"

# весь включённый swap сейчас, МБ (в /proc/swaps размер — в КБ, третья колонка)
current_mb="$(awk 'NR > 1 { kb += $3 } END { print int(kb / 1024) }' "$PROC_SWAPS")"
ours_active=0
awk 'NR > 1 { print $1 }' "$PROC_SWAPS" | grep -qxF "$SWAP_FILE" && ours_active=1
ours=0  # наш файл есть или будет создан — тогда он нужен и в /etc/fstab

say "Сейчас"
echo "Оперативная память: $(awk '/^MemTotal/ { print int($2 / 1024) }' /proc/meminfo) МБ"
echo "Swap включён: $current_mb МБ, нужно: $target_mb МБ"

if [ "$ours_active" = 1 ]; then
  ours=1
  echo "Файл $SWAP_FILE уже включён — новый не создаю."
elif [ -f "$SWAP_FILE" ]; then
  # остался от прошлого запуска (например, сервер перезагрузили до записи в fstab) — просто включить
  ours=1
  say "Включаю имеющийся файл $SWAP_FILE"
  run swapon "$SWAP_FILE"
elif [ "$current_mb" -ge "$target_mb" ]; then
  echo "Swap уже достаточно — новый файл не нужен."
else
  need_mb=$((target_mb - current_mb))
  free_mb="$(df -Pm "$(dirname "$SWAP_FILE")" | awk 'NR == 2 { print $4 }')"
  if [ "$((free_mb - need_mb))" -lt "$DISK_RESERVE_MB" ]; then
    fail "на диске свободно $free_mb МБ: после файла подкачки $need_mb МБ осталось бы меньше $DISK_RESERVE_MB МБ. \
Освободите место (README-DEPLOY.md, «Место на диске») или укажите размер меньше: sudo ./scripts/swap.sh 1536M"
  fi
  ours=1
  say "Создаю файл подкачки $SWAP_FILE на $need_mb МБ (на диске свободно $free_mb МБ)"
  if [ "$DRY" = 1 ]; then
    echo "  [проверка] fallocate -l ${need_mb}M $SWAP_FILE; chmod 600; mkswap; swapon"
  else
    # fallocate — мгновенно; не поддерживается файловой системой — заполняем нулями (дольше, до минуты)
    if ! fallocate -l "${need_mb}M" "$SWAP_FILE" 2>/dev/null; then
      rm -f "$SWAP_FILE"
      dd if=/dev/zero of="$SWAP_FILE" bs=1M count="$need_mb" status=none
    fi
    chmod 600 "$SWAP_FILE"
    mkswap "$SWAP_FILE" >/dev/null
    if ! swapon "$SWAP_FILE"; then
      rm -f "$SWAP_FILE"
      fail "swap не включился (файловая система не поддерживает файл подкачки). Сервер работает как раньше"
    fi
  fi
fi

# после перезагрузки сервера — тоже
if [ "$ours" = 1 ] && [ -f /etc/fstab ] && ! grep -qE "^${SWAP_FILE}[[:space:]]" /etc/fstab; then
  say "Swap — и после перезагрузки (/etc/fstab)"
  if [ "$DRY" = 1 ]; then
    echo "  [проверка] добавить в /etc/fstab: $SWAP_FILE none swap sw 0 0"
  else
    echo "$SWAP_FILE none swap sw 0 0" >> /etc/fstab
  fi
fi

# Redis сохраняет данные на диск через копию процесса; без этой настройки при нехватке памяти копия не создаётся,
# и Redis перестаёт принимать запись («фоновые задачи сейчас недоступны»). Рекомендация самих разработчиков Redis.
say "Настройка памяти для Redis (vm.overcommit_memory = 1)"
if [ "$DRY" = 1 ]; then
  echo "  [проверка] записать в $SYSCTL_FILE и применить"
else
  printf '# Ozon Analytics (scripts/swap.sh): Redis сохраняет данные и при нехватке памяти\nvm.overcommit_memory = 1\n' \
    > "$SYSCTL_FILE"
  sysctl -q -p "$SYSCTL_FILE"
fi

say "Готово"
if [ "$DRY" = 1 ]; then
  echo "Это была проверка — ничего не изменено. Выполнить: sudo ./scripts/swap.sh ${TARGET}"
else
  free -m
  swapon --show
fi
