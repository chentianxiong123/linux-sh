#!/usr/bin/env bash
# cc-switch → pi MCP 同步脚本
# 从 cc-switch 数据库读取 MCP 配置，转换为 pi 的 mcp.json 格式
# 用法：~/sh/ccswitch-pi/sync-ccswitch-pi.sh

set -euo pipefail

DB="$HOME/.cc-switch/cc-switch.db"
PI_MCP="$HOME/.pi/agent/mcp.json"
BACKUP="${PI_MCP}.bak.$(date +%Y%m%d-%H%M%S)"
KEEP=10   # 保留最近 N 份备份，多余的自动清理

if [ ! -f "$DB" ]; then
    echo "✗ 找不到数据库: $DB"
    exit 1
fi

# 1. 读 cc-switch 数据库（enabled_opencode=1 的 MCP），输出 name<TAB>server_config
rows=$(sqlite3 -separator $'\t' "$DB" \
    "SELECT name, server_config FROM mcp_servers WHERE enabled_opencode=1;")

if [ -z "$rows" ]; then
    echo "⚠ cc-switch 里没有 enabled_opencode=1 的 MCP，不覆盖现有配置"
    exit 1
fi

# 2. 生成 mcpServers 条目（用 python 转换格式，只处理 MCP 转换逻辑）
SERVERS_JSON=$(python3 - "$rows" <<'PYEOF'
import json, sys
rows_raw = sys.argv[1]
servers = {}
for line in rows_raw.split("\n"):
    if not line.strip():
        continue
    name, raw = line.split("\t", 1)
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        print(f"  ⚠ {name}: server_config 解析失败，跳过", file=sys.stderr)
        continue
    entry = {}
    if cfg.get("type") == "http" or "url" in cfg:
        entry["url"] = cfg.get("url", "")
        if cfg.get("headers"):
            entry["headers"] = cfg["headers"]
    else:
        entry["command"] = cfg.get("command", "npx")
        if cfg.get("args"):
            entry["args"] = cfg["args"]
    servers[name] = entry
    print(f"  ✓ {name}", file=sys.stderr)
print(json.dumps(servers, ensure_ascii=False, indent=2))
PYEOF
)

# 3. 备份现有 pi mcp.json（带时间戳，保留最近 KEEP 份）
if [ -f "$PI_MCP" ]; then
    cp -a "$PI_MCP" "$BACKUP"
    echo "备份 → $(basename "$BACKUP")"
    # 清理超过 KEEP 份的旧备份
    ls -1t "${PI_MCP}".bak.* 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
fi

# 4. 合并写入（保留 pi mcp.json 里已有的其他键）
python3 - "$PI_MCP" "$SERVERS_JSON" <<'PYEOF'
import json, sys
path, servers_json = sys.argv[1], sys.argv[2]
new_servers = json.loads(servers_json)
try:
    with open(path, "r", encoding="utf-8") as f:
        old = json.load(f)
    if isinstance(old, dict):
        old["mcpServers"] = new_servers
        new_data = old
    else:
        new_data = {"mcpServers": new_servers}
except (json.JSONDecodeError, FileNotFoundError):
    new_data = {"mcpServers": new_servers}

with open(path, "w", encoding="utf-8") as f:
    json.dump(new_data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PYEOF

echo "✓ 已写入 $PI_MCP，重启 pi 生效"
