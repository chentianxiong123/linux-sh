#!/usr/bin/env python3
"""ssh-manage — 便捷 SSH 连接管理器

只读 ~/.ssh/config,列出所有别名,双击连接。
不做任何配置管理,不修改 config 文件。
"""

import tkinter as tk
from tkinter import ttk
import subprocess
import re
import os

# ================================================================
# 解析 ~/.ssh/config
# ================================================================
def load_hosts(config_path=None):
    """解析 SSH config,返回 [{alias, HostName, User, Port, alias_suffix}] 列表"""
    if not config_path:
        config_path = os.path.expanduser("~/.ssh/config")

    hosts = []  # 每条: {alias, HostName, User, Port}

    with open(config_path) as f:
        lines = f.readlines()

    cur_aliases = None  # 当前 Host 块的别名列表,如 ["hi-box"] 或 ["a", "b"]
    cur_props = None    # 当前 Host 块的属性 dict

    def flush_last():
        """文件结束时保存最后一个 Host 块"""
        if cur_aliases is not None and cur_props:
            for alias in cur_aliases:
                hosts.append({
                    "alias": alias,
                    "HostName": cur_props.get("HostName", ""),
                    "User": cur_props.get("User", ""),
                    "Port": cur_props.get("Port", "22"),
                    "alias_suffix": "",
                })

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        m = re.match(r"^Host\s+(.+)$", stripped, re.IGNORECASE)
        if m:
            # 遇到新 Host → 先保存之前的
            flush_last()
            aliases = m.group(1).split()
            if aliases == ["*"]:
                # 通配符,跳过
                cur_aliases = None
                cur_props = None
                continue
            cur_aliases = aliases
            cur_props = {}
        else:
            m2 = re.match(r"^(\w+)\s+(.+)$", stripped)
            if m2 and cur_props is not None:
                key = m2.group(1)
                val = m2.group(2).strip()
                # 处理重复 HostName(比如 openwrt 有 108 和 109 两段)
                if key == "HostName" and "HostName" in cur_props:
                    # 保存之前的 HostName 作为独立条目,然后开新条目
                    old_alias = cur_aliases[0]
                    hosts.append({
                        "alias": old_alias,
                        "HostName": cur_props["HostName"],
                        "User": cur_props.get("User", ""),
                        "Port": cur_props.get("Port", "22"),
                        "alias_suffix": "",
                    })
                    # 新条目,alias 用 -alt
                    cur_aliases = [old_alias + "-alt"]
                    cur_props = {"HostName": val}
                else:
                    cur_props[key] = val

    flush_last()

    # 处理 alias 冲突(同一个别名出现多次,加后缀)
    seen = {}
    for h in hosts:
        a = h["alias"]
        if a in seen:
            seen[a] += 1
            h["alias"] = f"{a}-{seen[a]}"
        else:
            seen[a] = 1

    return hosts


# ================================================================
# 分类
# ================================================================
def categorize(alias, ip=""):
    a = alias.lower()
    if "phone" in a or "meilan" in a or "redmi" in a or "huawei" in a:
        return "📱 手机"
    if "win" in a or "wsl" in a:
        return "💻 Windows"
    if "box" in a:
        return "📦 盒子"
    if "fnos" in a or "nfs" in a:
        return "🗄️ NAS"
    if "pve" in a:
        return "🖥️ PVE"
    if "openwrt" in a:
        return "🌐 路由"
    if "debian" in a or "freebsd" in a:
        return "🐧 Linux"
    return "🏠 内网"


# ================================================================
# 主窗口
# ================================================================
class App:
    def __init__(self):
        self.hosts = load_hosts()

        self.win = tk.Tk()
        self.win.title("SSH 管理器")
        self.win.geometry("640x480")
        self.win.minsize(480, 320)

        # Catppuccin Mocha 配色
        self.C = {
            "bg": "#1e1e2e", "panel": "#313244", "fg": "#cdd6f4",
            "sub": "#a6adc8", "acc": "#89b4fa", "ok": "#a6e3a1",
            "err": "#f38ba8",
        }

        self.win.configure(bg=self.C["bg"])

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=self.C["bg"])
        style.configure("TLabel", background=self.C["bg"], foreground=self.C["fg"])
        style.configure("Sub.TLabel", background=self.C["bg"], foreground=self.C["sub"])
        style.configure("Head.TLabel", background=self.C["bg"],
                        foreground=self.C["acc"], font=("", 14, "bold"))
        style.configure("TEntry", fieldbackground=self.C["panel"],
                        foreground=self.C["fg"], bordercolor=self.C["panel"])
        style.configure("Treeview", background=self.C["bg"], foreground=self.C["fg"],
                        fieldbackground=self.C["bg"], borderwidth=0)
        style.configure("Treeview.Heading", background=self.C["panel"], foreground=self.C["acc"])
        style.configure("Treeview", rowheight=24)
        style.map("Treeview", background=[("selected", self.C["panel"])])
        style.configure("Connect.TButton", background=self.C["acc"],
                        foreground=self.C["bg"], borderwidth=0, padding=8)
        style.configure("TButton", background=self.C["panel"], foreground=self.C["fg"],
                        borderwidth=0, padding=6)
        style.map("TButton", background=[("active", "#45475a")])

        # ---------- 顶部:标题 + 搜索 ----------
        top = ttk.Frame(self.win)
        top.pack(fill="x", padx=14, pady=(12, 6))

        ttk.Label(top, text="🔗 SSH 管理器", style="Head.TLabel").pack(side="left")
        ttk.Label(top, text=f"  {len(self.hosts)} 台", style="Sub.TLabel").pack(side="left")

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.filter())
        ttk.Entry(top, textvariable=self.search_var, width=22).pack(
            side="right", ipady=3)
        self.win.bind("<Key>", self.on_key)

        # ---------- 中间:设备列表 ----------
        mid = ttk.Frame(self.win)
        mid.pack(fill="both", expand=True, padx=14, pady=6)

        cols = ("alias", "user", "ip", "port")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings",
                                 selectmode="browse")
        for c, w, txt in [("alias", 150, "别名"),
                          ("user", 100, "用户"),
                          ("ip", 170, "IP"),
                          ("port", 60, "端口")]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w", stretch=True)

        self.tree.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        # ---------- 底部:详情 ----------
        bottom = ttk.Frame(self.win)
        bottom.pack(fill="x", padx=14, pady=(6, 4))
        self.detail_var = tk.StringVar(value="← 选择一台设备查看")
        ttk.Label(bottom, textvariable=self.detail_var,
                  style="Sub.TLabel").pack(fill="x")

        # ---------- 按钮 ----------
        btns = ttk.Frame(self.win)
        btns.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(btns, text="🚀 连接", style="Connect.TButton",
                   command=self.connect).pack(side="left", padx=4)
        ttk.Button(btns, text="📋 复制命令", command=self.copy_cmd).pack(side="left", padx=4)
        ttk.Button(btns, text="🔄 刷新", command=self.refresh).pack(side="right", padx=4)

        # 事件
        self.tree.bind("<Double-1>", lambda e: self.connect())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_select())

        self.refresh()

    # ===================== 列表 =====================
    def refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for h in self.hosts:
            self.tree.insert("", "end", iid=h["alias"], values=(
                h["alias"],
                h["User"] or "-",
                h["HostName"] or "-",
                h["Port"],
            ), tags=(categorize(h["alias"], h["HostName"]),))
        self.apply_colors()

    def apply_colors(self):
        colors = {
            "📱 手机": "#f9e2af",
            "💻 Windows": "#89b4fa",
            "📦 盒子": "#f5c2e7",
            "🗄️ NAS": "#fab387",
            "🖥️ PVE": "#cba6f7",
            "🌐 路由": "#94e2d5",
            "🐧 Linux": "#a6e3a1",
            "🏠 内网": "#a6adc8",
        }
        for tag, color in colors.items():
            self.tree.tag_configure(tag, foreground=color)

    def filter(self):
        q = self.search_var.get().lower().strip()
        for item in self.tree.get_children():
            vals = self.tree.item(item)["values"]
            if not vals[0]:
                continue
            haystack = f"{vals[0]} {vals[1]} {vals[2]}".lower()
            if q and q not in haystack:
                self.tree.item(item, values=("", "", "", ""))
            else:
                self.tree.item(item, values=vals)

    def on_key(self, e):
        # Ctrl+F 聚焦搜索框
        if e.state & 0x4000 and e.keysym.lower() == "f":
            self.search_var.focus_set()
            return "break"

    # ===================== 选中 =====================
    def on_select(self):
        sel = self.tree.selection()
        if not sel:
            return
        h = self._find(sel[0])
        if h:
            cat = categorize(h["alias"], h["HostName"])
            user_str = h["User"] or "(默认)"
            self.detail_var.set(
                f"{cat}  │  {h['alias']}  │  {user_str}@{h['HostName']}:{h['Port']}"
            )

    def _find(self, alias):
        return next((x for x in self.hosts if x["alias"] == alias), None)

    def selected_host(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self._find(sel[0])

    # ===================== 动作 =====================
    def connect(self):
        h = self.selected_host()
        if not h:
            return
        alias = h["alias"]
        try:
            subprocess.Popen(["konsole", "-e", "ssh", alias],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            self.detail_var.set("❌ 找不到 konsole")

    def copy_cmd(self):
        h = self.selected_host()
        if not h:
            return
        parts = ["ssh"]
        if h["User"]:
            parts.append(f"{h['User']}@{h['HostName']}")
        else:
            parts.append(h["HostName"])
        if h["Port"] not in ("22", ""):
            parts.append(f"-p {h['Port']}")
        cmd = " ".join(parts)
        self.win.clipboard_clear()
        self.win.clipboard_append(cmd)
        self.detail_var.set(f"✅ 已复制: {cmd}")
        self.win.after(3000, self.on_select)

    def run(self):
        self.win.mainloop()


if __name__ == "__main__":
    App().run()
