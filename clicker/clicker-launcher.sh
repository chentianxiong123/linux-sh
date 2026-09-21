#!/bin/bash
# 连点器启动器 — 按需启动 ydotoold，不开机自启
/usr/bin/ydotoold &
sleep 0.5
exec python3 /home/a1/sh/clicker/clicker.py
