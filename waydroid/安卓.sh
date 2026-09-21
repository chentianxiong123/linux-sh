#!/bin/bash
# 安卓 (Waydroid) 标准启动脚本
# 在 Wayland 会话下直接运行；最正规方式：容器服务 + show-full-ui
# 停止方法：关闭本终端，或运行 waydroid session stop

# 1. 确保容器服务在跑（sudo 已配免密）
if ! systemctl is-active --quiet waydroid-container.service; then
    echo "[安卓] 启动容器服务..."
    sudo systemctl start waydroid-container.service
fi

# 2. 标准入口启动安卓完整界面（自动管理会话与显示）
exec waydroid show-full-ui