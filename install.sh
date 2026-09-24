#!/usr/bin/env bash
# install.sh - نصب خودکار AtronixX-Core در یک پوشه‌ی کاملاً مجزا (پیش‌فرض /opt/atronixx-core)
# استفاده:
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/install.sh | bash -s -- <owner>/<repo>
# یا بعد از clone کردن ریپو:
#   ./install.sh <owner>/<repo>
set -euo pipefail

REPO="${1:-}"
INSTALL_DIR="${ATX_DIR:-/opt/atronixx-core}"
COMPOSE="docker compose"

log()  { printf '\033[1;36m[AtronixX-Core]\033[0m %s\n' "$1"; }
err()  { printf '\033[1;31m[AtronixX-Core]\033[0m %s\n' "$1" >&2; }
die()  { err "$1"; exit 1; }

[[ $EUID -eq 0 ]] || die "این اسکریپت را با root یا sudo اجرا کن."
[[ -n "$REPO" ]] || die "نام ریپو را بده، مثلاً: ./install.sh youruser/atronixx-core"

# ---------------------------------------------------------------- پیش‌نیازها
if ! command -v docker >/dev/null 2>&1; then
  log "Docker نصب نیست؛ در حال نصب (اسکریپت رسمی Docker)..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
else
  log "Docker از قبل نصب است؛ تنظیمات فعلی‌اش دست‌نخورده می‌ماند."
fi
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 پیدا نشد. لطفاً به‌روزرسانی کن."

# ---------------------------------------------------------------- دانلود پروژه
mkdir -p "$INSTALL_DIR"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  log "نسخه‌ی قبلی پیدا شد؛ به‌روزرسانی کد..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  log "دریافت کد از GitHub..."
  git clone --depth 1 "https://github.com/${REPO}.git" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
sed -i "s#ghcr.io/OWNER/REPO#ghcr.io/${REPO,,}#g" docker-compose.yml

# ---------------------------------------------------------------- تنظیمات (.env)
mkdir -p data
chmod 700 data
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo
  log "تنظیمات اولیه را وارد کن (بعداً هم می‌تونی فایل .env را ویرایش کنی):"

  read -rp "🤖 توکن بات (از @BotFather): " BOT_TOKEN
  while [[ ! "$BOT_TOKEN" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]]; do
    err "فرمت توکن درست نیست."
    read -rp "🤖 توکن بات: " BOT_TOKEN
  done

  read -rp "🆔 آیدی عددی ادمین (از @userinfobot): " ADMIN_ID
  while [[ ! "$ADMIN_ID" =~ ^[0-9]+$ ]]; do
    err "باید فقط عدد باشد."
    read -rp "🆔 آیدی عددی ادمین: " ADMIN_ID
  done

  read -rp "💬 یوزرنیم پشتیبانی [پیش‌فرض @AtronixX_Support]: " SUPPORT_USERNAME
  SUPPORT_USERNAME="${SUPPORT_USERNAME:-@AtronixX_Support}"

  while true; do
    read -rsp "🔐 رمز بکاپ خودکار (حداقل ۸ کاراکتر، حتماً یادت بماند): " BK1; echo
    read -rsp "🔐 تکرار رمز بکاپ: " BK2; echo
    [[ "$BK1" == "$BK2" && ${#BK1} -ge 8 ]] && { BACKUP_PASSPHRASE="$BK1"; break; }
    err "رمزها یکی نبودند یا کمتر از ۸ کاراکتر بودند؛ دوباره امتحان کن."
  done

  SELF_IPS="$(hostname -I 2>/dev/null | tr ' ' ',' | sed 's/,$//' || true)"

  cat > .env <<EOF2
BOT_TOKEN=${BOT_TOKEN}
ADMIN_ID=${ADMIN_ID}
SUPPORT_USERNAME=${SUPPORT_USERNAME}
BACKUP_PASSPHRASE=${BACKUP_PASSPHRASE}
TZ=Asia/Tehran
BACKUP_INTERVAL_HOURS=24
MAX_LIVE_SESSIONS=20
IDLE_TIMEOUT_MIN=10
MAX_SESSION_HOURS=2
BLOCKED_IPS=${SELF_IPS}
EOF2
  chmod 600 .env
  log ".env ساخته شد."
else
  log ".env از قبل وجود دارد؛ دست‌نخورده می‌ماند."
fi

# ---------------------------------------------------------------- دستور مدیریتی
install -m 755 scripts/atronixx.sh /usr/local/bin/atronixx

# ---------------------------------------------------------------- بالا آوردن
log "دریافت ایمیج و اجرای بات..."
$COMPOSE pull
$COMPOSE up -d

echo
log "✅ نصب کامل شد. AtronixX-Core در حال اجراست."
log "مدیریت با دستور: atronixx {status|logs|restart|update|uninstall}"
