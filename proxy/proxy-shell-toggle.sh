#!/bin/bash

PROXY_HOST="192.168.31.82"
PROXY_PORT="7890"
PROXY_ADDR="http://${PROXY_HOST}:${PROXY_PORT}"
BASHRC="$HOME/.bashrc"

if grep -q "^export HTTP_PROXY=${PROXY_ADDR}$" "$BASHRC" 2>/dev/null; then
    sed -i '/^# proxy config$/d' "$BASHRC"
    sed -i '/^export HTTP_PROXY=/d' "$BASHRC"
    sed -i '/^export HTTPS_PROXY=/d' "$BASHRC"
    unset HTTP_PROXY
    unset HTTPS_PROXY
    notify-send "Shell代理" "已关闭" -i network-proxy
else
    if ! grep -q "^export HTTP_PROXY=${PROXY_ADDR}$" "$BASHRC" 2>/dev/null; then
        echo "" >> "$BASHRC"
        echo "# proxy config" >> "$BASHRC"
        echo "export HTTP_PROXY=${PROXY_ADDR}" >> "$BASHRC"
        echo "export HTTPS_PROXY=${PROXY_ADDR}" >> "$BASHRC"
    fi
    export HTTP_PROXY="$PROXY_ADDR"
    export HTTPS_PROXY="$PROXY_ADDR"
    notify-send "Shell代理" "已开启 -> $PROXY_ADDR" -i network-proxy
fi