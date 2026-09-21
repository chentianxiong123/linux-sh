#!/usr/bin/env bash
# MoGu RomZhuShou 启动器
# 蘑菇游戏客户端 (Windows via Wine)
# 位置: C:\Program Files (x86)\MoGu\RomZhuShou\MoGuRomZhuShou.exe

set -euo pipefail

WINE_PREFIX="${WINE_PREFIX:-$HOME/.wine}"
EXE='C:\Program Files (x86)\MoGu\RomZhuShou\MoGuRomZhuShou.exe'

# 检查前置条件
if ! command -v wine &>/dev/null; then
    echo "❌ 未安装 wine: sudo apt install wine-staging" >&2
    exit 1
fi

if [ ! -d "$WINE_PREFIX" ]; then
    echo "❌ 找不到 wine 前缀: $WINE_PREFIX" >&2
    exit 1
fi

# 后台启动（不阻塞终端）
setsid nohup env "WINEPREFIX=$WINE_PREFIX" wine "$EXE" \
    > /tmp/mogu.log 2>&1 < /dev/null & disown

echo "✅ MoGu 已启动 (PID=$!)"
echo "   日志: /tmp/mogu.log"
