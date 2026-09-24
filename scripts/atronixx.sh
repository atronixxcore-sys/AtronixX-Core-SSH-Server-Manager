#!/usr/bin/env bash
# atronixx - مدیریت سرویس AtronixX-Core (نصب در /usr/local/bin توسط install.sh)
set -euo pipefail
DIR="${ATX_DIR:-/opt/atronixx-core}"
cd "$DIR" 2>/dev/null || { echo "AtronixX-Core در $DIR پیدا نشد." >&2; exit 1; }
COMPOSE="docker compose"

case "${1:-}" in
  status)
    $COMPOSE ps
    ;;
  logs)
    $COMPOSE logs -f --tail=200 bot
    ;;
  restart)
    $COMPOSE restart bot
    ;;
  update)
    $COMPOSE pull
    $COMPOSE up -d
    ;;
  backup)
    echo "برای بکاپ دستی از پنل ادمین داخل خودِ بات (💾 بکاپ و ریستور) استفاده کن."
    ;;
  uninstall)
    read -rp "⚠️ این کار کانتینر، شبکه و پوشه‌ی $DIR (شامل دیتابیس!) را کاملاً پاک می‌کند. مطمئنی؟ (yes/no) " CONFIRM
    if [[ "$CONFIRM" == "yes" ]]; then
      $COMPOSE down -v --remove-orphans
      cd /
      rm -rf "$DIR"
      rm -f /usr/local/bin/atronixx
      echo "AtronixX-Core حذف شد."
    else
      echo "لغو شد."
    fi
    ;;
  *)
    echo "استفاده: atronixx {status|logs|restart|update|backup|uninstall}"
    exit 1
    ;;
esac
