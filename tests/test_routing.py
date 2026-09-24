"""تست: همه‌ی callback_data های تولیدشده توسط UI با یکی از الگوهای main.register مچ می‌شوند"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubs"))
os.environ.update(BOT_TOKEN="123456789:AA" + "x" * 32, ADMIN_ID="1", DATA_DIR="/tmp/atx_route_test", BACKUP_PASSPHRASE="x" * 8)

import main as m

class FakeApp:
    def __init__(self): self.patterns = []
    def add_handler(self, h, group=0):
        if getattr(h, "kind", None) in ("callback",):
            self.patterns.append(h.pattern)
    def add_error_handler(self, fn): pass

app = FakeApp()
m.register(app)
regexes = [re.compile(p) for p in app.patterns]

def matches(data: str) -> bool:
    return any(r.match(data) for r in regexes)

SAMPLES = [
    "menu:home", "noop", "lang:toggle", "terms:accept", "join:check", "me:home", "me:del", "me:delyes",
    "menu:hosts", "hl:3:1", "h:view:12", "h:fav:12", "h:grp:12", "h:setgrp:12:3", "h:test:12", "h:del:12",
    "h:delyes:12", "h:edit:12", "h:ef:12:label", "hosts:add", "wz:back", "wz:port22", "wz:auth:key",
    "wz:save", "wz:test",
    "h:conn:12", "h:connf:12", "h:sftp:12", "h:mon:12", "conn:cancel", "trust:yes", "trust:no",
    "term:ctrlc", "term:up", "term:exit",
    "sf:rf", "sf:pg:1", "sf:up", "sf:dm", "sf:md", "sf:cd:0", "sf:dl:0", "sf:del:0", "sf:delyes:0", "sf:exit", "sf:uph",
    "snip:list", "snip:view:1", "snip:del:1", "snip:delyes:1", "snip:add", "snip:run:1", "snip:close",
    "grp:list", "grp:view:1", "grp:del:1", "grp:delyes:1", "grp:add",
    "adm:home", "adm:st", "adm:users:all:0", "adm:users:sub:2", "adm:u:5", "adm:ban:5", "adm:unban:5",
    "adm:revoke:5", "adm:revokeyes:5", "adm:del:5", "adm:delyes:5", "adm:grantnew", "adm:grant:5",
    "adm:find", "adm:msg:5", "adm:mode", "adm:modeyes:free", "adm:modeyes:paid",
    "adm:bc", "adm:bcaud:all", "adm:bcaud:subs", "af:bcgo",
    "adm:ch", "adm:chdel:1", "adm:chadd", "adm:bk", "adm:bknow", "adm:rs", "af:rsgo",
    "adm:ss", "adm:kill:5", "adm:set", "adm:live:1", "adm:live:-5", "adm:log",
]

missing = [s for s in SAMPLES if not matches(s)]
print(f"{len(SAMPLES)} samples checked, {len(app.patterns)} patterns registered")
if missing:
    print("NOT MATCHED:", missing)
    sys.exit(1)
print("ALL ROUTES OK")
