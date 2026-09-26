<div align="center">

# 🛰 AtronixX-Core

### Termius, reborn as a Telegram bot.

A full SSH terminal, file manager, and server monitor for your infrastructure —
running live inside Telegram. Dockerized, sandboxed, and built for servers that
already have other things running on them.

[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](https://github.com/atronixxcore-sys/AtronixX-Core-SSH-Server-Manager/pkgs/container/atronixx-core-ssh-server-manager)
[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Build](https://img.shields.io/github/actions/workflow/status/atronixxcore-sys/AtronixX-Core-SSH-Server-Manager/docker.yml?branch=main&label=build)](https://github.com/atronixxcore-sys/AtronixX-Core-SSH-Server-Manager/actions)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

📡 [@AtronixX_Core](https://t.me/AtronixX_Core) &nbsp;·&nbsp; 💬 [@AtronixX_Support](https://t.me/AtronixX_Support)

</div>

---

## ✨ What it does

- ⚡ **Live SSH terminal** — a real PTY session rendered live inside a Telegram message, with a control pad (Ctrl+C, Tab, arrows, clear) and buttery-smooth updates.
- 📁 **File manager (SFTP)** — browse, upload, download, create folders, delete — all from chat.
- 📊 **Server monitor** — CPU, RAM, disk, uptime, at a tap.
- ⚡ **Snippets** — save your go-to commands and fire them with one button.
- 🗂 **Groups & favorites** — organize servers your way.
- 👑 **Full admin panel** — user management, ban/unban, broadcast messages, forced channel-join, live session control, and an activity log — all inside Telegram.
- 🎟 **Free or subscription mode** — open access for everyone, or admin-gated access with per-user expiry and server limits.
- 💾 **Encrypted backup & restore** — AES-256 encrypted backups, automatic or on-demand, restorable from a single file.
- 🌐 **Persian & English**, switchable per user.
- 🐳 **Fully containerized** — no open ports, a dedicated Docker network, capped CPU/RAM, and a read-only filesystem, so it sits in its own corner without touching anything else on your server.

## 🔐 Security highlights

- Server credentials (passwords/SSH keys) are encrypted with **AES-256-GCM**; the master key lives in its own file, never in the database.
- Every outbound SSH connection is checked against private/internal/loopback/cloud-metadata ranges before it's made — no pivoting into your own infrastructure.
- Host keys are verified TOFU-style, with an explicit warning if a server's key ever changes.
- Runs as a non-root user, with a read-only root filesystem and no exposed ports.

## 🚀 Install

One command, on a fresh Ubuntu/Debian server:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/atronixxcore-sys/AtronixX-Core-SSH-Server-Manager/main/install.sh)
```

It installs Docker if needed, asks for your bot token, your numeric Telegram ID,
and a backup password — and that's it. From then on, manage everything with:

```bash
AX-Manager
```

<div align="center">

```
 █████╗ ██╗  ██╗
██╔══██╗╚██╗██╔╝
███████║ ╚███╔╝
██╔══██║ ██╔██╗
██║  ██║██╔╝ ██╗
╚═╝  ╚═╝╚═╝  ╚═╝
SSH SERVER MANAGER · powered by AtronixX
```

</div>

An interactive menu: install, start/stop/restart, update, view logs, edit
settings, check status, or uninstall — no need to remember flags.

## ⚙️ Configuration

All settings live in `.env` inside the install directory and can be edited
anytime via `AX-Manager` → *Edit settings*.

| Variable | What it does |
|---|---|
| `BOT_TOKEN` | Your bot's token from [@BotFather](https://t.me/BotFather) |
| `ADMIN_ID` | Your numeric Telegram ID — the bot's only admin |
| `SUPPORT_USERNAME` | Shown to users without an active subscription |
| `BACKUP_PASSPHRASE` | Encrypts automatic backups (AES-256) — keep it safe, it can't be recovered |
| `TZ` | Timezone (default `Asia/Tehran`) |
| `BACKUP_INTERVAL_HOURS` | How often automatic backups are sent (default `24`) |
| `MAX_LIVE_SESSIONS` | Concurrent live terminal cap (also adjustable from the admin panel) |
| `IDLE_TIMEOUT_MIN` / `MAX_SESSION_HOURS` | Idle timeout and max session lifetime |
| `BLOCKED_IPS` | Extra IPs to block outbound (comma-separated) |

## 🧱 Architecture

```
config.py            settings loaded from .env
main.py              entry point, handler registration
core/                encryption, SSRF-safe target checks, the SSH/SFTP engine,
                      session registry, encrypted backups, background housekeeping
database/db.py       SQLite (WAL mode)
handlers/            bot logic: access gate, servers, terminal, SFTP, tools, admin
locales/             Persian / English strings
scripts/             AX-Manager, the install & control script
```

## 🧪 Tests

```bash
pip install -r requirements.txt --break-system-packages
python3 tests/test_core.py          # crypto, database, subscriptions, backup/restore, dates, SSRF guard
python3 tests/scan_strings.py       # every string key exists and every placeholder resolves
PYTHONPATH=tests/stubs:. python3 tests/test_routing.py   # every button maps to a registered handler
```

These cover internal logic with certainty; real Telegram/SSH connectivity is
covered by the manual checklist in [`TESTING.md`](TESTING.md).

## 📜 License & responsibility

MIT-licensed. This bot connects to servers *you* add — you're responsible for
how it's used and for the credentials you store in it.

---

<div align="center">
<sub>Built for anyone who wants their servers one Telegram message away.</sub>
</div>
