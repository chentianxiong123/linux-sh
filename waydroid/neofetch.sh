#!/bin/bash
# 安卓容器内运行 neofetch（已装入 /data/local/bin/neofetch）
# 用法: ./neofetch.sh  或  加参数透传(如 --colors 2)
adb -s 192.168.240.112 shell /system_ext/bin/bash /data/local/bin/neofetch "$@"