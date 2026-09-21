#!/bin/bash

if systemctl is-enabled sleep.target 2>/dev/null | grep -q "masked"; then
    sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target
    sudo systemctl daemon-reload
    notify-send "休眠" "已启用" -i system-suspend
else
    sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
    sudo systemctl daemon-reload
    notify-send "休眠" "已禁用" -i system-lock-screen
fi
