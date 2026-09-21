# ~/sh - 脚本与图标统一管理

所有自写脚本、快捷方式、图标统一放在这里。

## 组织原则

**每个"服务/工具"一个子目录**，脚本 + 图标 + 配置文件放一起：

```
~/sh/
├── <service>/               每项一个目录
│   ├── <service>.sh         主脚本
│   ├── <service>.png        图标
│   └── ...                  其他配置文件
└── README.md
```

## 目录清单

| 目录 | 用途 |
|---|---|
| `cleanup-agent-repo/` | 清理 agent 仓库脚本 |
| `clicker/` | 连点器程序本体（clicker.py） |
| `clicker-hotkey/` | 连点器全局快捷键助手（目前是占位 stub） |
| `clicker-launcher/` | 连点器启动器（桌面"连点器"入口 → 这里） |
| `docker/` | Docker daemon 切换 |
| `extract-icon/` | 从 exe 提取图标的工具 |
| `mogu/` | 蘑菇图标/脚本 |
| `new-app/` | 新建应用时的脚本模板 |
| `nfs/` | NFS 挂载切换 |
| `proxy/` | 代理切换（KDE / shell 两版） |
| `sleep/` | 系统休眠切换 |
| `waydroid/` | Waydroid 安卓（GUI + 启动脚本，见 `安卓Waydroid技术总结.md`） |
| `xiaoai-battery/` | 电源管理 GUI（桌面"XiaoXinAir-电源管理"入口 → 这里） |
| `xiaoai-profile/` | 性能模式切换 |

## 桌面入口与脚本的对应

| 桌面快捷方式 | 指向 |
|---|---|
| 连点器 | `clicker-launcher/clicker-launcher.sh` |
| XiaoXinAir-电源管理 | `xiaoai-battery/xiaoai-battery` |
| 安卓桌面 | `waydroid/waydroid-android`（GUI，唯一入口） |
| 安卓 (Wayland 直连) | `waydroid/安卓.sh` |

> 注意：`clicker-launcher.sh` 内部目前调用 `/home/a1/.local/bin/clicker.py`（该路径不存在，调用会失败）——如需修复指向 `../clicker/clicker.py`。