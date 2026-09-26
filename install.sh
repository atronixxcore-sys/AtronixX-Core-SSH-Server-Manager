#!/usr/bin/env bash
# Bootstrap for AX SSH-Manager: downloads the manager script and runs the installer.
# One-liner:
#   bash <(curl -fsSL https://raw.githubusercontent.com/atronixxcore-sys/AtronixX-Core-SSH-Server-Manager/main/install.sh)
set -euo pipefail

RAW="https://raw.githubusercontent.com/atronixxcore-sys/AtronixX-Core-SSH-Server-Manager/main/scripts/ax-manager.sh"
BIN="/usr/local/bin/AX-Manager"

[[ $EUID -eq 0 ]] || { echo "Please run this as root (sudo)." >&2; exit 1; }

curl -fsSL "$RAW" -o "$BIN"
chmod +x "$BIN"
"$BIN" install

echo
echo "Run 'AX-Manager' anytime to open the control panel."
