# ~/sh - 脚本与图标统一管理

所有自写脚本、快捷方式、图标统一放在这里。

## 组织原则

**同一类别放进同一个目录**，脚本 + 图标 + 配置文件放一起：

```
~/sh/
├── clicker/         连点器全套（程序+启动器+快捷键助手）
├── dev-tools/       开发/维护小工具
├── battery/         联想小新 Air 14 电源/性能管理（GUI）
├── docker/          Docker daemon 切换
├── nfs/             NFS 挂载切换
├── proxy/           代理切换
├── sleep/           系统休眠切换
├── mogu/            蘑菇工具
├── waydroid/        Waydroid 安卓（GUI + 启动脚本）
└── README.md
```

## 目录清单

| 目录 | 用途 |
|---|---|
| `clicker/` | 连点器全套：`clicker.py` 本体、`clicker-launcher.sh` 启动器、`clicker-hotkey.sh` 快捷键助手 |
| `dev-tools/` | 开发/维护小工具：`new-app.sh.template` 新建应用模板、`extract-icon.sh` 提取图标、`cleanup-agent-repo.sh` 清理仓库 |
| `battery/` | 联想小新 Air 14 电池与性能管理 GUI：`battery.sh` |
| `docker/` | Docker daemon 切换 |
| `nfs/` | NFS 挂载切换 |
| `proxy/` | 代理切换（KDE / shell 两版） |
| `sleep/` | 系统休眠切换 |
| `mogu/` | 蘑菇工具 |
| `waydroid/` | Waydroid 安卓（GUI + 启动脚本，见 `安卓Waydroid技术总结.md`） |

## 桌面入口与脚本的对应

| 桌面快捷方式 | 指向 |
|---|---|
| 连点器 | `clicker/clicker-launcher.sh` |
| 电池管理 | `battery/battery.sh` |
| 安卓桌面 | `waydroid/waydroid-android` |
| 安卓 (Wayland 直连) | `waydroid/安卓.sh` |