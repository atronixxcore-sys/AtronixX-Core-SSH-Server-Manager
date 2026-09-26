#!/usr/bin/env bash
# AX SSH-Manager - installer & control panel for the AtronixX-Core Telegram bot.
# Repo:   https://github.com/atronixxcore-sys/AtronixX-Core-SSH-Server-Manager
# Usage:  AX-Manager                → interactive menu
#         AX-Manager install        → non-interactive first install
#         AX-Manager <command>      → run one action and exit
set -uo pipefail

OWNER="atronixxcore-sys"
REPO="AtronixX-Core-SSH-Server-Manager"
RAW_BASE="https://raw.githubusercontent.com/${OWNER}/${REPO}/main"
IMAGE="ghcr.io/atronixxcore-sys/atronixx-core-ssh-server-manager:latest"

INSTALL_DIR="/opt/atronixx-core"
BIN_PATH="/usr/local/bin/AX-Manager"
CONTAINER="atronixx-core"
COMPOSE="docker compose"

# ---------------------------------------------------------------- colors
C_RESET='\033[0m'
C_DIM='\033[38;5;242m'
C_TXT='\033[38;5;255m'
C_ACC='\033[38;5;135m'    # accent (purple) - brand
C_CYA='\033[38;5;51m'
C_OK='\033[38;5;82m'
C_WARN='\033[38;5;214m'
C_ERR='\033[38;5;196m'
BOLD='\033[1m'

say()  { printf "${C_TXT}%s${C_RESET}\n" "$1"; }
ok()   { printf "${C_OK}✔ %s${C_RESET}\n" "$1"; }
warn() { printf "${C_WARN}⚠ %s${C_RESET}\n" "$1"; }
err()  { printf "${C_ERR}✘ %s${C_RESET}\n" "$1" >&2; }
die()  { err "$1"; exit 1; }
pause() { printf "${C_DIM}Press Enter to continue...${C_RESET}"; read -r _; }

require_root() { [[ $EUID -eq 0 ]] || die "Please run this as root (sudo)."; }

# ---------------------------------------------------------------- status helpers
container_state() {
  docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo "not installed"
}

container_uptime() {
  local started
  started=$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER" 2>/dev/null) || { echo "-"; return; }
  local start_epoch now_epoch diff d h m
  start_epoch=$(date -d "$started" +%s 2>/dev/null) || { echo "-"; return; }
  now_epoch=$(date +%s)
  diff=$(( now_epoch - start_epoch ))
  d=$(( diff / 86400 )); h=$(( (diff % 86400) / 3600 )); m=$(( (diff % 3600) / 60 ))
  echo "${d}d ${h}h ${m}m"
}

container_ram() {
  docker stats --no-stream --format '{{.MemUsage}}' "$CONTAINER" 2>/dev/null | awk '{print $1}' || echo "-"
}

# reads live bot counters straight from the container (best-effort, 3s timeout)
bot_counters() {
  timeout 3 docker exec "$CONTAINER" python3 -c '
import asyncio
from database.db import db
async def main():
    await db.open()
    st = await db.stats()
    mode = await db.get_mode()
    print(f"{mode}|{st[\"users\"]}|{st[\"subs\"]}|{st[\"banned\"]}")
    await db.close()
asyncio.run(main())
' 2>/dev/null
}

# ---------------------------------------------------------------- banner
banner() {
  clear
  local state ram uptime mode users subs banned counters
  state=$(container_state)
  if [[ "$state" == "running" ]]; then
    ram=$(container_ram); uptime=$(container_uptime)
    counters=$(bot_counters)
    if [[ -n "$counters" ]]; then
      IFS='|' read -r mode users subs banned <<< "$counters"
      mode=$([[ "$mode" == "paid" ]] && echo "SUBSCRIPTION" || echo "FREE")
    else
      mode="-"; users="-"; subs="-"; banned="-"
    fi
  else
    ram="-"; uptime="-"; mode="-"; users="-"; subs="-"; banned="-"
  fi

  printf "${C_ACC}"
  cat <<'BANNER'
 █████╗ ██╗  ██╗
██╔══██╗╚██╗██╔╝
███████║ ╚███╔╝
██╔══██║ ██╔██╗
██║  ██║██╔╝ ██╗
╚═╝  ╚═╝╚═╝  ╚═╝
BANNER
  printf "${C_RESET}"
  printf "${C_TXT}${BOLD}SSH SERVER MANAGER${C_RESET} ${C_DIM}· powered by AtronixX${C_RESET}\n"
  printf "${C_DIM}────────────────────────────────────────────────────${C_RESET}\n"

  local state_color="$C_ERR"
  [[ "$state" == "running" ]] && state_color="$C_OK"
  printf " ${C_TXT}● BOT${C_RESET}       ${state_color}%-14s${C_RESET} ${C_TXT}● MODE${C_RESET}   ${C_CYA}%s${C_RESET}\n" "${state^^}" "$mode"
  printf " ${C_TXT}● USERS${C_RESET}     ${C_OK}%-14s${C_RESET} ${C_TXT}● SUBS${C_RESET}   ${C_OK}%s${C_RESET}\n" "$users" "$subs"
  printf " ${C_TXT}● UPTIME${C_RESET}    ${C_CYA}%-14s${C_RESET} ${C_TXT}● RAM${C_RESET}    ${C_CYA}%s${C_RESET}\n" "$uptime" "$ram"
  printf "${C_DIM}────────────────────────────────────────────────────${C_RESET}\n"
  printf " ${C_TXT}📡 Channel: ${C_ACC}@AtronixX_Core${C_RESET}   ${C_TXT}💬 Support: ${C_ACC}@AtronixX_Support${C_RESET}\n"
  printf "${C_DIM}────────────────────────────────────────────────────${C_RESET}\n"
}

# ---------------------------------------------------------------- setup helpers
ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    say "Docker not found; installing it now..."
    curl -fsSL https://get.docker.com | sh || die "Docker installation failed."
    systemctl enable --now docker
  fi
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 not found. Please update Docker."
}

fetch_file() {
  local url="$1" dest="$2"
  curl -fsSL "$url" -o "$dest" || die "Could not download $url"
}

ensure_project_files() {
  mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/data"
  chmod 700 "$INSTALL_DIR/data"
  [[ -f "$INSTALL_DIR/docker-compose.yml" ]] || fetch_file "$RAW_BASE/docker-compose.yml" "$INSTALL_DIR/docker-compose.yml"
  [[ -f "$INSTALL_DIR/.env.example" ]] || fetch_file "$RAW_BASE/.env.example" "$INSTALL_DIR/.env.example"
}

run_wizard() {
  local bot_token admin_id support_username bk1 bk2 self_ips
  echo
  say "Let's configure AtronixX-Core:"
  echo

  read -rp "🤖 Bot token (from @BotFather): " bot_token
  while [[ ! "$bot_token" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]]; do
    err "That doesn't look like a valid token."
    read -rp "🤖 Bot token: " bot_token
  done

  read -rp "🆔 Your numeric Telegram ID (from @userinfobot): " admin_id
  while [[ ! "$admin_id" =~ ^[0-9]+$ ]]; do
    err "Must be numbers only."
    read -rp "🆔 Numeric ID: " admin_id
  done

  read -rp "💬 Support username [default @AtronixX_Support]: " support_username
  support_username="${support_username:-@AtronixX_Support}"

  while true; do
    read -rsp "🔐 Auto-backup password (min 8 chars, remember it!): " bk1; echo
    read -rsp "🔐 Confirm password: " bk2; echo
    [[ "$bk1" == "$bk2" && ${#bk1} -ge 8 ]] && break
    err "Passwords didn't match or were too short. Try again."
  done

  self_ips="$(hostname -I 2>/dev/null | tr ' ' ',' | sed 's/,$//' || true)"

  cat > "$INSTALL_DIR/.env" <<EOF2
BOT_TOKEN=${bot_token}
ADMIN_ID=${admin_id}
SUPPORT_USERNAME=${support_username}
BACKUP_PASSPHRASE=${bk1}
TZ=Asia/Tehran
BACKUP_INTERVAL_HOURS=24
MAX_LIVE_SESSIONS=20
IDLE_TIMEOUT_MIN=10
MAX_SESSION_HOURS=2
BLOCKED_IPS=${self_ips}
EOF2
  chmod 600 "$INSTALL_DIR/.env"
  ok "Configuration saved."
}

install_self() {
  cp "$0" "$BIN_PATH" 2>/dev/null || fetch_file "$RAW_BASE/scripts/ax-manager.sh" "$BIN_PATH"
  chmod +x "$BIN_PATH"
}

# ---------------------------------------------------------------- commands
cmd_install() {
  require_root
  ensure_docker
  ensure_project_files
  if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    run_wizard
  else
    warn ".env already exists; keeping it as-is."
  fi
  install_self
  cd "$INSTALL_DIR"
  say "Pulling the image and starting AtronixX-Core..."
  $COMPOSE pull && $COMPOSE up -d
  ok "Installed and running."
  echo
  say "Manage it anytime by running: ${BOLD}AX-Manager${C_RESET}"
}

cmd_start()   { require_root; cd "$INSTALL_DIR" 2>/dev/null || die "Not installed yet."; $COMPOSE up -d; ok "Started."; }
cmd_stop()    { require_root; cd "$INSTALL_DIR" 2>/dev/null || die "Not installed yet."; $COMPOSE stop; ok "Stopped."; }
cmd_restart() { require_root; cd "$INSTALL_DIR" 2>/dev/null || die "Not installed yet."; $COMPOSE restart; ok "Restarted."; }

cmd_update() {
  require_root
  cd "$INSTALL_DIR" 2>/dev/null || die "Not installed yet."
  say "Pulling the latest image..."
  $COMPOSE pull && $COMPOSE up -d
  ok "Updated."
}

cmd_logs() {
  cd "$INSTALL_DIR" 2>/dev/null || die "Not installed yet."
  $COMPOSE logs -f --tail=200 bot
}

cmd_status() {
  cd "$INSTALL_DIR" 2>/dev/null && $COMPOSE ps
}

cmd_edit_settings() {
  require_root
  [[ -f "$INSTALL_DIR/.env" ]] || die "Not installed yet."
  if command -v nano >/dev/null 2>&1; then
    nano "$INSTALL_DIR/.env"
  else
    ${EDITOR:-vi} "$INSTALL_DIR/.env"
  fi
  read -rp "Restart now to apply changes? [Y/n] " r
  [[ "${r,,}" != "n" ]] && cmd_restart
}

cmd_uninstall() {
  require_root
  [[ -d "$INSTALL_DIR" ]] || die "Not installed."
  warn "This permanently deletes the container, network, and $INSTALL_DIR (including the database)."
  read -rp "Type 'yes' to confirm: " confirm
  [[ "$confirm" == "yes" ]] || { say "Cancelled."; return; }
  cd "$INSTALL_DIR" && $COMPOSE down -v --remove-orphans
  cd /
  rm -rf "$INSTALL_DIR"
  rm -f "$BIN_PATH"
  ok "AtronixX-Core fully removed."
}

# ---------------------------------------------------------------- menu
interactive_menu() {
  while true; do
    banner
    cat <<MENU
 ${C_CYA}1)${C_RESET} Install / Reinstall
 ${C_CYA}2)${C_RESET} Start
 ${C_CYA}3)${C_RESET} Stop
 ${C_CYA}4)${C_RESET} Restart
 ${C_CYA}5)${C_RESET} Update to latest version
 ${C_CYA}6)${C_RESET} View live logs
 ${C_CYA}7)${C_RESET} Edit settings (.env)
 ${C_CYA}8)${C_RESET} Show status
 ${C_ERR}9)${C_RESET} Uninstall
 ${C_DIM}0) Exit${C_RESET}
MENU
    echo
    read -rp " Select an option: " choice
    echo
    case "$choice" in
      1) cmd_install; pause ;;
      2) cmd_start; pause ;;
      3) cmd_stop; pause ;;
      4) cmd_restart; pause ;;
      5) cmd_update; pause ;;
      6) cmd_logs ;;
      7) cmd_edit_settings; pause ;;
      8) cmd_status; pause ;;
      9) cmd_uninstall; pause ;;
      0) exit 0 ;;
      *) warn "Invalid option."; pause ;;
    esac
  done
}

# ---------------------------------------------------------------- entry point
case "${1:-}" in
  install)   cmd_install ;;
  start)     cmd_start ;;
  stop)      cmd_stop ;;
  restart)   cmd_restart ;;
  update)    cmd_update ;;
  logs)      cmd_logs ;;
  settings)  cmd_edit_settings ;;
  status)    cmd_status ;;
  uninstall) cmd_uninstall ;;
  "")        interactive_menu ;;
  *) die "Unknown command: $1 (try: install|start|stop|restart|update|logs|settings|status|uninstall)" ;;
esac
