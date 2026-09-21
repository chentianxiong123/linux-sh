#!/usr/bin/env bash
# 全局快捷键: Ctrl+Shift+F=连点 Ctrl+Shift+G=停止
trap '' SIGINT
while true; do
  ev=$(ydotool key 2>/dev/null || true)
  # ydotool 不支持直接监听，用 python 子进程
  break
done
