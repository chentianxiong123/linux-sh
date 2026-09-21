#!/bin/bash
# 安卓 X11 版：嵌套 Weston 窗口 + 标准启动
# waydroid 1.6.3 只支持 Wayland，在 X11 下用 Weston 窗口包一层
#
# 分辨率设置（安卓属性会持久保存，与窗口尺寸必须一致）：
#   安卓运行时执行：
#     waydroid prop set persist.waydroid.width  宽
#     waydroid prop set persist.waydroid.height 高
#   然后改下面 W/H 并重启本脚本即可生效

# ── 安卓显示尺寸（与 GUI 状态文件 device.conf 保持一致）──
# 格式：机型名 宽 高；读不到时默认 540x960
read _NAME W H < /home/a1/sh/waydroid/device.conf 2>/dev/null
W=${W:-540}
H=${H:-960}

# 1. 确保容器服务在跑
if ! systemctl is-active --quiet waydroid-container.service; then
    echo "[安卓] 启动容器服务..."
    sudo systemctl start waydroid-container.service
fi

# 2. 清掉残留的 wayland 文件（X11 下无冲突）
rm -f /run/user/$(id -u)/wayland-*.lock

# 3. 在 X11 桌面开嵌套 Weston 窗口（窗口尺寸 = 安卓分辨率）
weston --width=$W --height=$H --shell=kiosk >/dev/null 2>&1 &
sleep 2

# 4. 标准入口启动安卓
SOCK=$(ls -t /run/user/$(id -u)/wayland-*.lock 2>/dev/null | sed 's/.*\///; s/\.lock$//' | head -1)
WAYLAND_DISPLAY=${SOCK:-wayland-0} exec waydroid show-full-ui