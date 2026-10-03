#!/usr/bin/env python3
"""toolbox.py — 极简工具启动器

扫描 sh/desktop/*.desktop，网格显示图标 + 文字，点击启动。
"""

import os
import shutil
import subprocess
import time
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

# ── 配置 ──────────────────────────────────────────────
DESKTOP_DIR = Path("/home/a1/sh/desktop")

# 布局常量（cell 大小可被 Ctrl+滚轮缩放，持久化保存）
ICON_SIZE = 64
CELL_W = 140
CELL_H = 140
PADDING = 20
GRID_COLS = 5          # 列数，随窗口宽度自适应
ROWS_PER_PAGE = 2      # 每页行数（随窗口高度自适应，初始值）
ROW_STEP = CELL_H + PADDING   # 动态行距（随画布实际高度重新分配）
COL_STEP = CELL_W + PADDING   # 动态列距（随画布实际宽度重新分配）
SETTINGS_FILE = Path.home() / ".config" / "toolbox" / "settings.json"

# 主题色（跟其他工具一致）
C_BG = "#1a1a2e"
C_CARD = "#16213e"
C_FG = "#e0e0ff"
C_MUTED = "#8888aa"
C_ACCENT = "#7b68ee"
C_OK = "#4ade80"    # 成功
C_ERR = "#f87171"   # 错误

# 图标搜索：主题 + 目录类型 + 尺寸
def _get_current_theme():
    """读取桌面当前激活图标主题（官方：kreadconfig = KDE 配置解析）"""
    if not hasattr(_get_current_theme, "val"):
        theme = "hicolor"
        try:
            import subprocess as sp
            for tool in ("kreadconfig6", "kreadconfig5"):
                try:
                    r = sp.run([tool, "--group", "Icons", "--key", "Theme"],
                               capture_output=True, text=True, timeout=3)
                    t = r.stdout.strip()
                    if t and os.path.exists(f"/usr/share/icons/{t}"):
                        theme = t
                        break
                except Exception:
                    continue
        except Exception:
            pass
        _get_current_theme.val = theme
    return _get_current_theme.val


_theme_cache = {}


def _parse_theme(theme):
    """读取主题 index.theme → (inherits[], dirs{原名:目录}); 缓存"""
    if theme not in _theme_cache:
        idx = f"/usr/share/icons/{theme}/index.theme"
        inherits = []
        dirs = {}
        section = None
        if os.path.isfile(idx):
            try:
                with open(idx, encoding="utf-8", errors="ignore") as f:
                    for raw in f:
                        line = raw.strip()
                        if line.startswith("[") and line.endswith("]"):
                            section = line[1:-1].strip()
                            continue
                        if "=" in line and section:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip()
                            if section == "Icon Theme":
                                if k == "Inherits":
                                    inherits = [x for x in v.split(",") if x]
                                elif k == "Directories":
                                    for d in v.split(","):
                                        if d.strip():
                                            dirs[d.strip()] = {}
                            elif section in dirs:
                                if k == "Size":
                                    try:
                                        dirs[section]["Size"] = int(v)
                                    except ValueError:
                                        pass
            except Exception:
                pass
        _theme_cache[theme] = (inherits, dirs)
    return _theme_cache[theme]


def _theme_file(theme, name):
    """在指定主题里搜 name 图标（用户级 ~/.local/share/icons 优先，再系统 /usr/share/icons）"""
    _, dirs = _parse_theme(theme)
    home = os.path.expanduser("~")
    bases = [f"{home}/.local/share/icons/{theme}",
             f"/usr/share/icons/{theme}"]
    for td in bases:
        if not os.path.isdir(td):
            continue
        best, best_sz = None, None
        for d, meta in (dirs or {}).items():
            for ext in (".svg", ".png", ".xpm"):
                p = os.path.join(td, d, name + ext)
                if os.path.isfile(p):
                    if ext == ".svg":   # 矢量优先
                        return p
                    sz = meta.get("Size", 64)
                    if best is None or abs(sz - ICON_SIZE) < best_sz:
                        best, best_sz = p, abs(sz - ICON_SIZE)
        if best:
            return best
        # 目录未声明时兑底：全树搜索
        for root, _, files in os.walk(td):
            if "cursors" in root:
                continue
            for fn in files:
                base, ext = os.path.splitext(fn)
                if base == name and ext.lower() in (".svg", ".png", ".xpm"):
                    return os.path.join(root, fn)
    return None


def _resolve_icon(name):
    """Freedesktop 规范：当前主题 → 继承链 → hicolor → pixmaps 兜底
    返回文件路径或 None"""
    current = _get_current_theme()
    order = []
    seen = set()
    def add(t):
        if t and t not in seen:
            seen.add(t)
            order.append(t)
    add(current)
    stack = [current]
    while stack:
        t = stack.pop()
        if t in seen:
            continue
        add(t)
        inh, _ = _parse_theme(t)
        for x in inh:
            if x not in seen:
                stack.append(x)
    add("hicolor")   # 规范兜底主题
    for th in order:
        hit = _theme_file(th, name)
        if hit:
            return hit
    p = os.path.join("/usr/share/pixmaps", name + ".png")
    if os.path.isfile(p):
        return p
    return None

# 分类筛选（按 Exec 自动识别生态）
ECOSYSTEMS = [
    ("linux",   "🐧 Linux"),
    ("wine",    "🍷 Wine"),
    ("android", "🤖 安卓"),
    ("chrome",  "🌐 浏览器"),
]


def detect_ecosystem(exec_cmd):
    """从 Exec 命令判断生态：waydroid→安卓, wine→Wine, chrome/app→浏览器, 否则 Linux 原生"""
    if not exec_cmd:
        return "linux"
    e = exec_cmd
    if "waydroid" in e:
        return "android"
    if "WINEPREFIX" in e or e.lstrip().startswith("wine"):
        return "wine"
    if "--app-id" in e or "--app=" in e:
        return "chrome"
    return "linux"


# 默认图标（找不到时用）
DEFAULT_ICON_PATH = None


def parse_desktop(file_path):
    """解析 .desktop 文件：只解析 [Desktop Entry] 主段，提取 Name/Icon/Exec + 生态"""
    name = file_path.stem  # 兜底用文件名
    icon = None
    exec_cmd = None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            in_main = False
            for line in f:
                s = line.strip()
                # 遇到段标题：[Desktop Entry] 进入；[Desktop Action ...] 及其他段忽略
                if s.startswith("[") and s.endswith("]"):
                    in_main = (s == "[Desktop Entry]")
                    continue
                if not in_main:
                    continue
                if s.startswith("Name=") and not s.startswith("Name["):
                    name = s[5:].strip()
                elif s.startswith("Icon=") and not s.startswith("Icon["):
                    icon = s[5:].strip()
                elif s.startswith("Exec="):
                    exec_cmd = s[5:].strip()
    except Exception:
        pass

    return {
        "name": name,
        "icon": icon,
        "exec": exec_cmd,
        "path": str(file_path),
        "ecosystem": detect_ecosystem(exec_cmd),
    }


def _system_icon_path(name, size=64):
    """用系统官方(Gtk.IconTheme)解析图标名 → 返回真实文件路径（跟桌面同一套）"""
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        ic = Gtk.IconTheme.get_default().lookup_icon(name, size, 0)
        if ic:
            return ic.get_filename()
    except Exception:
        pass
    return None


def find_icon(icon_name):
    """图标查找（以真实桌面为准）：
    1. Icon 是完整路径 → 直接用
    2. 系统 Gtk.IconTheme 官方解析 → 拿真实路径
    3. 兜底 Freedesktop 规范解析
    """
    if not icon_name or not Image:
        return None
    # 完整路径直接用
    if os.path.isfile(icon_name):
        return _load_image(icon_name)
    # 系统官方解析拿真实路径
    p = _system_icon_path(icon_name)
    if p and os.path.isfile(p):
        img = _load_image(p)
        if img:
            return img
    # 兜底：Freedesktop 规范解析（含单复数变体）
    variants = [icon_name]
    variants.append(icon_name[:-1] if icon_name.endswith("s") else icon_name + "s")
    for name in variants:
        p = _resolve_icon(name)
        if p:
            img = _load_image(p)
            if img:
                return img
    return None


def _load_image(path):
    """加载图片文件，SVG 用 rsvg-convert 转 PNG"""
    if path.endswith(".svg"):
        # 用 rsvg-convert 转成 PNG
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            subprocess.run(
                ["rsvg-convert", "-w", "128", "-h", "128", path, "-o", tmp_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
            if os.path.isfile(tmp_path):
                return Image.open(tmp_path).convert("RGBA")
        except Exception:
            pass
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        return None
    else:
        try:
            return Image.open(path).convert("RGBA")
        except Exception:
            return None


def load_icon_pil(item):
    """加载图标的 PIL Image，找不到用默认"""
    img = find_icon(item["icon"])
    if img:
        return img.convert("RGBA")
    # 默认：画一个纯色圆角矩形
    return make_default_icon(item["name"])


def make_default_icon(name):
    """生成默认图标（纯色块 + 首字母）"""
    if not Image:
        return None
    # 48x48 蓝色块
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (123, 104, 238, 255))
    # 获取首字母（中文取第一个字）
    char = name[0].upper() if name else "?"
    # 简单画个字母（用 PIL 的默认字体）
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), char, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((ICON_SIZE - tw) // 2, (ICON_SIZE - th) // 2), char, fill=(255, 255, 255), font=font)
    return img


class ToolboxApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Toolbox")
        self.root.configure(bg=C_BG)
        self.root.wm_protocol("WM_DELETE_WINDOW", self._on_close)

        # 加载设置（cell 大小 + 窗口大小）
        self._load_settings()
        self.root.geometry(self._win_geometry)
        
        # 窗口可自由缩放，列数自适应
        self.root.resizable(True, True)
        self.root.minsize(480, 300)
        self.root.bind("<Configure>", self._on_resize)
        # Ctrl+滚轮缩放格子大小
        self.root.bind("<Control-MouseWheel>", self._zoom)
        self._resize_pending = None

        # 注册系统级拖放：从桌面拖 .desktop 文件进来 = 导入工具
        if HAS_DND:
            try:
                self.root.drop_target_register(DND_FILES)
                self.root.dnd_bind("<<Drop>>", self.on_drop)
            except Exception:
                pass

        self.items = []
        self._all_items = []   # 全量（顺序/过滤的源）
        self._filter = "linux"  # 当前分类，默认 Linux
        self._filter_btns = {}
        self._selected_idx = -1  # 当前选中的格子索引（-1 = 无）
        self._active_menu = None  # 当前打开的右键菜单
        self._name_tip = None     # 完整名字提示（单击显示）
        
        # 点击窗口任意处关闭右键菜单
        self.root.bind("<Button-1>", lambda e: self._close_menu())
        self.root.bind("<FocusOut>", lambda e: self._close_menu())
        
        # 拖拽状态
        self._drag_item = None
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._drag_ids = []
        self._drag_photo = None
        self._is_dragging = False
        self._drag_threshold = 5  # 像素阈值，超过才算拖拽
        
        # 双击检测（用 Tkinter 原生 Double-Button-1，无需自建）
        self._last_target_tag = None
        
        # 分页状态
        self._page = 0
        self._page_count = 1
        self._page_label = None
        
        self._build_filter_bar()
        self._build_nav()
        self.load_desktops()
        self.draw_grid()

        if not self.items:
            # 空状态
            label = tk.Label(
                self.root,
                text=f"未找到 .desktop 文件\n目录: {DESKTOP_DIR}",
                font=("Microsoft YaHei", 11),
                fg=C_MUTED,
                bg=C_BG,
                justify="center",
            )
            label.pack(expand=True)

    def _on_close(self):
        """窗口关闭：保存设置 + 销毁"""
        self._win_geometry = self.root.geometry()   # 关闭瞬间真实几何(含位置)
        self._save_settings()
        self.root.destroy()

    def load_desktops(self):
        """扫描 desktop 目录 → 全量 + 加载顺序 + 按当前分类过滤"""
        self._filter_set = None  # 标记：顺序恢复后仍按当前 filter 重算
        if not DESKTOP_DIR.exists():
            return
        self._all_items = [parse_desktop(f)
                           for f in sorted(DESKTOP_DIR.glob("*.desktop"))]
        # 在全部列表上恢复全局顺序
        self.items = self._all_items
        self._load_order()
        self._apply_filter()

    def _build_filter_bar(self):
        """顶部分类筛选栏（全部 / Linux / Wine / 安卓 / 浏览器）—— 按钮等宽，随窗口拉伸"""
        bar = tk.Frame(self.root, bg=C_BG)
        bar.pack(side="top", fill="x", pady=(6, 0))
        self._filter_bar = bar
        for i, (key, text) in enumerate(ECOSYSTEMS):
            b = tk.Button(
                bar, text=text,
                bg=C_CARD if key != self._filter else C_ACCENT,
                fg=C_FG, relief="flat", font=("Microsoft YaHei", 10),
                activebackground=C_ACCENT, activeforeground="#fff",
                cursor="hand2",
                command=lambda k=key: self._set_filter(k))
            b.grid(row=0, column=i, padx=3, pady=2, sticky="ew")
            bar.columnconfigure(i, weight=1, uniform="filter")
            self._filter_btns[key] = b

    def _set_filter(self, key):
        """切换分类筛选项"""
        self._filter = key
        # 高亮当前分类按钮
        for k, b in self._filter_btns.items():
            b.configure(bg=C_ACCENT if k == key else C_CARD)
        self._apply_filter()

    def _apply_filter(self):
        """按当前分类过滤 items 并重绘；同时重置选中/分页"""
        self._close_name_tip()
        self.items = [it for it in self._all_items
                      if it.get("ecosystem") == self._filter]
        self._page = 0
        self._selected_idx = -1
        self._last_target_tag = None
        # 无条件 draw_grid：空分类分支会 del canvas，若这里按 has_canvas
        # 判断，从空分类切回有内容的分类就不重建画布 → 图标全消失。
        # draw_grid 内部自处理空/非空、canvas 与空标签的重建。
        self.draw_grid()

    def on_drop(self, event):
        """从外部拖入 .desktop 文件：复制进持久化目录 + 刷新网格"""
        imported = 0
        skipped = 0
        try:
            files = self.root.tk.splitlist(event.data)
        except Exception:
            files = [event.data]
        
        for f in files:
            f = f.strip()
            if not f:
                continue
            src = Path(f)
            if src.suffix.lower() != ".desktop":
                skipped += 1
                continue
            try:
                dst = DESKTOP_DIR / src.name
                shutil.copy2(src, dst)
                imported += 1
            except Exception as e:
                self._show_notification(f"导入失败 {src.name}: {e}", True)
        
        if imported:
            # 重新扫描 + 重绘（新文件追加到最后）
            self.load_desktops()
            self.draw_grid()
            self._selected_idx = -1
            self._show_notification(f"已导入 {imported} 个工具 → {DESKTOP_DIR}")
        elif skipped:
            self._show_notification("只接受 .desktop 文件", True)
        
        return event.action

    def draw_grid(self):
        """画当前页的网格（分页）"""
        if not self.items:
            # 空状态提示
            if hasattr(self, 'canvas'):
                self.canvas.destroy()
                del self.canvas
            if hasattr(self, '_empty_label'):
                self._empty_label.destroy()
            self._empty_label = tk.Label(
                self.root,
                text=f"暂无工具\n拖入 .desktop 文件可添加",
                font=("Microsoft YaHei", 11),
                fg=C_MUTED,
                bg=C_BG,
                justify="center",
            )
            self._empty_label.pack(expand=True)
            self._update_page_label()
            return
        if hasattr(self, '_empty_label'):
            self._empty_label.destroy()

        # 页数 + 页码修正
        self._page_count = max(1, (len(self.items) + GRID_COLS * ROWS_PER_PAGE - 1) // (GRID_COLS * ROWS_PER_PAGE))
        if self._page >= self._page_count:
            self._page = self._page_count - 1
        start = self._page * GRID_COLS * ROWS_PER_PAGE
        end = min(len(self.items), start + GRID_COLS * ROWS_PER_PAGE)

        # 销毁旧 canvas
        if hasattr(self, 'canvas'):
            self.canvas.destroy()

        self.canvas = tk.Canvas(
            self.root,
            width=GRID_COLS * CELL_W,
            height=ROWS_PER_PAGE * CELL_H,
            bg=C_BG,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both")

        # 根据窗口实际剩余高度重新分配行距，让 canvas 恰好填满，不留白
        global ROW_STEP, COL_STEP
        # winfo 在首次渲染前返回 1，取不到真实值时用估算值兜底
        bar_h = self._filter_bar.winfo_height() if hasattr(self, '_filter_bar') else 0
        nav_h = self._nav.winfo_height() if hasattr(self, '_nav') else 0
        if bar_h <= 1: bar_h = 44
        if nav_h <= 1: nav_h = 36
        avail_w = self.root.winfo_width()
        avail_h = self.root.winfo_height() - bar_h - nav_h
        if avail_w > 0:
            COL_STEP = max(CELL_W + PADDING, avail_w // GRID_COLS)
        else:
            COL_STEP = CELL_W + PADDING
        if ROWS_PER_PAGE > 0 and avail_h > 0:
            ROW_STEP = max(CELL_H + PADDING, avail_h // ROWS_PER_PAGE)
        else:
            ROW_STEP = CELL_H + PADDING
        # canvas 尺寸精确匹配动态步长
        self.canvas.config(width=GRID_COLS * COL_STEP, height=ROWS_PER_PAGE * ROW_STEP)

        # 只画当前页的 items（tag 用全局索引），y 用动态行距 + 行内垂直居中
        for gidx in range(start, end):
            local = gidx - start
            col = local % GRID_COLS
            row = local // GRID_COLS
            x = col * COL_STEP + COL_STEP // 2
            y = row * ROW_STEP + ROW_STEP // 2

            self.draw_cell(self.canvas, self.items[gidx], x, y, gidx)

        self._update_page_label()

    def _build_nav(self):
        """底部翻页栏"""
        self._nav = tk.Frame(self.root, bg=C_BG)
        self._nav.pack(side="bottom", pady=4)
        tk.Button(self._nav, text="◀ 上一页", command=self._prev_page,
                  bg=C_CARD, fg=C_FG, relief="flat", font=("Microsoft YaHei", 10),
                  activebackground=C_ACCENT, activeforeground="#fff",
                  cursor="hand2").pack(side="left", padx=10)
        self._page_label = tk.Label(self._nav, text="",
                                    font=("Microsoft YaHei", 10), fg=C_MUTED, bg=C_BG)
        self._page_label.pack(side="left", padx=10)
        tk.Button(self._nav, text="下一页 ▶", command=self._next_page,
                  bg=C_CARD, fg=C_FG, relief="flat", font=("Microsoft YaHei", 10),
                  activebackground=C_ACCENT, activeforeground="#fff",
                  cursor="hand2").pack(side="left", padx=10)

    def _on_resize(self, event):
        """窗口尺寸变化 → 重算列数 → 重绘（防抖）"""
        if event.widget is not self.root:
            return
        # 移动/缩放都更新几何缓存（含位置），退出时保存
        self._win_geometry = self.root.geometry()
        if self._resize_pending:
            self.root.after_cancel(self._resize_pending)
        self._resize_pending = self.root.after(120, self._apply_layout)

    def _apply_layout(self):
        """根据当前窗口宽高重算列数和行数并重绘"""
        global GRID_COLS, ROWS_PER_PAGE
        self._resize_pending = None
        if not hasattr(self, "canvas"):
            return
        # 只在宽高实际变化时才重绘，移动窗口不重绘
        w, h = self.root.winfo_width(), self.root.winfo_height()
        if getattr(self, '_last_layout_wh', None) == (w, h):
            return
        self._last_layout_wh = (w, h)

        # 列数：宽度 / (格子宽 + 间距)，保底 2，上限 12
        root_w = self.root.winfo_width()
        cols = max(2, min(12, (root_w - 40) // (CELL_W + PADDING)))
        # 行数：高度扣掉筛选栏+导航栏后 / (格子高 + 间距)，保底 1，上限 10
        bar_h = self._filter_bar.winfo_height() if hasattr(self, '_filter_bar') else 0
        if bar_h <= 1: bar_h = 44
        nav_h = self._nav.winfo_height() if hasattr(self, '_nav') else 0
        if nav_h <= 1: nav_h = 36
        avail_h = self.root.winfo_height() - bar_h - nav_h - 20
        rows = max(1, min(10, avail_h // (CELL_H + PADDING)))

        GRID_COLS = cols
        ROWS_PER_PAGE = rows
        # 任何 resize 都重绘（重绘时会重算 COL_STEP/ROW_STEP 摊满 canvas）
        self.draw_grid()

    def _zoom(self, event):
        """Ctrl+滚轮：缩放格子/图标大小"""
        global CELL_W, CELL_H, ICON_SIZE
        delta = 8 if event.delta > 0 else -8
        new_w = max(80, min(220, CELL_W + delta))
        if new_w == CELL_W:
            return
        CELL_W = new_w
        CELL_H = new_w                    # 格子正方形
        ICON_SIZE = int(new_w * 0.45)     # 图标约占 45%
        self._save_settings()
        self._apply_layout()              # 列数随格子大小变化
        self._show_notification(f"格子 {CELL_W}px · {GRID_COLS} 列")

    def _save_settings(self):
        """保存设置（格子大小 + 窗口大小）"""
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SETTINGS_FILE, "w") as f:
                import json
                json.dump({"cell_w": CELL_W, "geometry": self._win_geometry}, f)
        except Exception:
            pass

    def _load_settings(self):
        """加载设置"""
        global CELL_W, CELL_H, ICON_SIZE
        self._win_geometry = "860x460"
        try:
            import json
            if SETTINGS_FILE.exists():
                with open(SETTINGS_FILE) as f:
                    data = json.load(f)
                cw = data.get("cell_w", 140)
                if 80 <= cw <= 220:
                    CELL_W = cw
                    CELL_H = cw
                    ICON_SIZE = int(cw * 0.45)
                if data.get("geometry"):
                    self._win_geometry = data["geometry"]
        except Exception:
            pass

    def _update_page_label(self):
        """更新页码显示"""
        if self._page_label:
            self._page_label.config(text=f"第 {self._page + 1} / {self._page_count} 页")

    def _prev_page(self):
        if self._page > 0:
            self._page -= 1
            self.draw_grid()

    def _next_page(self):
        if self._page < self._page_count - 1:
            self._page += 1
            self.draw_grid()

    def draw_cell(self, canvas, item, cx, cy, grid_idx):
        """画一个格子：背景卡片 + 图标 + 文字 + 高亮层"""
        x0, y0 = cx - CELL_W // 2 + 10, cy - CELL_H // 2 + 10
        x1, y1 = cx + CELL_W // 2 - 10, cy + CELL_H // 2 - 10

        tag = f"cell_{grid_idx}"

        # 1. 背景卡片（最底层）
        canvas.create_rectangle(
            x0, y0, x1, y1,
            fill=C_CARD, outline="",
            tags=tag,
        )

        # 2. 高亮层（在背景之上，图标之下，独立 tag 只作用于自己）
        canvas.create_rectangle(
            x0, y0, x1, y1,
            fill="", outline="",
            tags=(tag, f"hl_{grid_idx}"),
        )

        # 3. 图标（相对位置，适配不同尺寸档位）
        photo = self.make_photo(item)
        if photo:
            iy = cy - int(CELL_H * 0.15)
            canvas.create_image(cx, iy, image=photo, anchor="center", tags=tag)
            setattr(self, f"_img_{grid_idx}", photo)  # 防止 GC
        else:
            canvas.create_text(cx, cy - int(CELL_H * 0.15), text="📦", font=("Segoe UI Emoji", 28), fill=C_FG, tags=tag)

        # 4. 文字
        name = item["name"]
        if len(name) > 10:
            name = name[:9] + "…"
        canvas.create_text(
            cx, cy + int(CELL_H * 0.25),
            text=name,
            font=("Microsoft YaHei", 9),
            fill=C_FG,
            tags=tag,
        )

        # 5. 点击区域（最上层，透明）
        click_tag = f"click_{grid_idx}"
        canvas.create_rectangle(
            x0, y0, x1, y1,
            fill="", outline="",
            tags=click_tag,
        )

        # 绑定事件（全部在 click_tag 上）
        canvas.tag_bind(click_tag, "<Button-1>", lambda e, i=item, g=grid_idx: self._on_press(e, i, g))
        canvas.tag_bind(click_tag, "<Double-Button-1>", lambda e, i=item: self.launch(i))
        canvas.tag_bind(click_tag, "<B1-Motion>", lambda e: self._on_drag(e))
        canvas.tag_bind(click_tag, "<ButtonRelease-1>", lambda e, i=item, g=grid_idx: self._on_release(e, i, g))
        canvas.tag_bind(click_tag, "<Button-3>", lambda e, i=item: self.show_menu(e, i))
        canvas.tag_bind(click_tag, "<Enter>", lambda e, t=f"hl_{grid_idx}": self.on_hover(t, True))
        canvas.tag_bind(click_tag, "<Leave>", lambda e, t=f"hl_{grid_idx}": self.on_hover(t, False))

    def _cell_center(self, idx):
        """全局索引 → 当前页内中心坐标（行列都用动态步长）"""
        start = self._page * GRID_COLS * ROWS_PER_PAGE
        local = idx - start
        col = local % GRID_COLS
        row = local // GRID_COLS
        return col * COL_STEP + COL_STEP // 2, row * ROW_STEP + ROW_STEP // 2

    def _idx_at(self, x, y):
        """鼠标坐标 → 全局索引（仅当前页内，页外返回 -1）"""
        col = x // COL_STEP
        row = y // ROW_STEP   # 行列都用动态步长，与 draw_grid 的 x/y 计算一致
        local = row * GRID_COLS + col
        start = self._page * GRID_COLS * ROWS_PER_PAGE
        end = min(len(self.items), start + GRID_COLS * ROWS_PER_PAGE)
        gidx = start + local
        if gidx < start or gidx >= end:
            return -1
        return gidx

    def _on_press(self, event, item, grid_idx):
        """鼠标按下：记录起始位置，准备拖拽"""
        self._drag_item = item
        self._drag_grid_idx = grid_idx
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_drag(self, event):
        """拖动中：超过阈值则开始拖拽"""
        if not self._drag_item:
            return
        
        dx = event.x - self._drag_start_x
        dy = event.y - self._drag_start_y
        
        if not self._is_dragging and (abs(dx) > self._drag_threshold or abs(dy) > self._drag_threshold):
            # 开始拖拽
            self._is_dragging = True
            self._drag_ids = []
            self._create_drag_visual(event)
        
        if self._is_dragging:
            self._update_drag_visual(event)
            self._highlight_drop_target(event)

    def _on_release(self, event, item, grid_idx):
        """松开：完成拖拽或单击选中"""
        was_dragging = self._is_dragging
        try:
            if was_dragging:
                # 完成拖拽
                self._complete_drop(event)
        finally:
            # 无论是否异常，都移除拖拽视觉 + 重置状态
            self._remove_drag_visual()
            self._drag_item = None
            self._is_dragging = False
        
        # 单击：选中（双击由 Double-Button-1 处理）+ 显示完整名字提示
        if not was_dragging:
            self.select(item, grid_idx)
            self._show_name_tip(item, event)

    def _close_name_tip(self):
        """关闭完整名字提示"""
        if getattr(self, "_name_tip", None) is not None:
            try:
                self._name_tip.destroy()
            except Exception:
                pass
            self._name_tip = None

    def _show_name_tip(self, item, event):
        """单击格子：若名字被截断，在鼠标下方浮出完整名字"""
        self._close_name_tip()
        if not item or len(item.get("name", "")) <= 10:
            return  # 没被截断，不用提示
        try:
            tip = tk.Toplevel(self.root)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            fr = tk.Frame(tip, bg="#2a2a4a", bd=1, relief="solid")
            fr.pack(fill="both", expand=True)
            tk.Label(
                fr, text=item["name"], bg="#2a2a4a", fg="#fff",
                font=("Microsoft YaHei", 9), padx=8, pady=4, anchor="w",
            ).pack(fill="both", expand=True)
            x = event.x_root if event is not None else PADDING
            y = event.y_root if event is not None else PADDING
            tip.geometry(f"+{x + 12}+{y + 14}")
            self._name_tip = tip
        except Exception:
            self._name_tip = None

    def _create_drag_visual(self, event):
        """创建拖拽视觉（半透明图标跟随鼠标）"""
        item = self._drag_item
        ids = []
        # 半透明背景
        ids.append(self.canvas.create_rectangle(
            event.x - 40, event.y - 40, event.x + 40, event.y + 40,
            fill="#2a3f6e", outline=C_ACCENT, width=2,
            tags="drag_visual",
        ))
        # 图标（保存引用防止 GC）
        photo = self.make_photo(item)
        self._drag_photo = photo
        if photo:
            ids.append(self.canvas.create_image(
                event.x, event.y - 10, image=photo, anchor="center",
                tags="drag_visual",
            ))
        else:
            ids.append(self.canvas.create_text(
                event.x, event.y - 10, text="📦", font=("Segoe UI Emoji", 24),
                tags="drag_visual",
            ))
        # 文字
        name = item["name"][:8]
        ids.append(self.canvas.create_text(
            event.x, event.y + 20, text=name, font=("Microsoft YaHei", 9), fill=C_FG,
            tags="drag_visual",
        ))
        self._drag_ids = ids

    def _update_drag_visual(self, event):
        """更新拖拽视觉位置"""
        if not self._drag_ids:
            return
        # 背景
        self.canvas.coords(self._drag_ids[0],
                         event.x - 40, event.y - 40, event.x + 40, event.y + 40)
        # 图标
        self.canvas.coords(self._drag_ids[1], event.x, event.y - 10)
        # 文字
        self.canvas.coords(self._drag_ids[2], event.x, event.y + 20)

    def _remove_drag_visual(self):
        """移除拖拽视觉（用统一 tag 兜底删除）"""
        # 兜底：删除所有带 drag_visual tag 的元素（防止残留）
        self.canvas.delete("drag_visual")
        self._drag_ids = []
        self._drag_photo = None

    def _highlight_drop_target(self, event):
        """高亮鼠标下的目标格子"""
        if self._last_target_tag:
            self.canvas.itemconfig(self._last_target_tag, fill="", outline="")
            self._last_target_tag = None
        
        # 用坐标直接算索引（页内映射到全局），不用 _pos 缓存
        dst_idx = self._idx_at(event.x, event.y)
        src_idx = self.items.index(self._drag_item)
        
        if dst_idx >= 0 and dst_idx != src_idx:
            self.canvas.itemconfig(f"hl_{dst_idx}", fill="#2a4f7e", outline=C_ACCENT)
            self._last_target_tag = f"hl_{dst_idx}"

    def _complete_drop(self, event):
        """完成拖拽：交换两个格子（局部更新）"""
        src_idx = self.items.index(self._drag_item)
        dst_idx = self._idx_at(event.x, event.y)
        
        if dst_idx < 0 or dst_idx == src_idx:
            return
        
        # 交换列表
        self.items[src_idx], self.items[dst_idx] = self.items[dst_idx], self.items[src_idx]
        
        # 选中状态跟着 item 走：如果选中的是交换的两个格子之一，更新索引
        if self._selected_idx == src_idx:
            self._selected_idx = dst_idx
        elif self._selected_idx == dst_idx:
            self._selected_idx = src_idx
        
        # 局部更新：只删除并重绘这两个格子（位置由索引算，不会错）
        self.canvas.delete(f"cell_{src_idx}", f"click_{src_idx}", f"cell_{dst_idx}", f"click_{dst_idx}")
        
        sx, sy = self._cell_center(src_idx)
        dx, dy = self._cell_center(dst_idx)
        self.draw_cell(self.canvas, self.items[src_idx], sx, sy, src_idx)
        self.draw_cell(self.canvas, self.items[dst_idx], dx, dy, dst_idx)
        
        # 恢复选中高亮（重绘后高亮层是空的，用 hl tag）
        if self._selected_idx >= 0:
            self.canvas.itemconfig(f"hl_{self._selected_idx}", fill="#1e2f5e", outline=C_ACCENT)
        
        # 清理拖拽目标高亮残留（指向已删除的 item）
        self._last_target_tag = None
        
        # 保存顺序
        self._save_order()

    def _save_order(self):
        """保存图标顺序到文件（把当前显示顺序同步回全量，再落盘）"""
        import json
        from pathlib import Path
        # 当前屏幕顺序合并回全量：显示的按新序，未显示的排末尾保持原序
        pos = {it['path']: i for i, it in enumerate(self.items)}
        self._all_items.sort(key=lambda it: pos.get(it['path'], len(pos)))
        order = [item['path'] for item in self._all_items]
        order_file = Path.home() / ".config" / "toolbox" / "order.json"
        order_file.parent.mkdir(parents=True, exist_ok=True)
        with open(order_file, 'w') as f:
            json.dump(order, f)

    def _load_order(self):
        """加载图标顺序"""
        import json
        from pathlib import Path
        order_file = Path.home() / ".config" / "toolbox" / "order.json"
        if not order_file.exists():
            return
        try:
            with open(order_file) as f:
                order = json.load(f)
            # 按保存的顺序重新排列
            path_to_item = {item['path']: item for item in self.items}
            new_items = []
            for path in order:
                if path in path_to_item:
                    new_items.append(path_to_item[path])
            # 添加未在保存顺序中的新文件
            for item in self.items:
                if item['path'] not in order:
                    new_items.append(item)
            self.items = new_items
            # ★ 同步回全量：后续 _apply_filter 从 _all_items 过滤时
            #   顺序也按 order（否则过滤后被打回文件名序，顺序还原）
            if hasattr(self, '_all_items'):
                self._all_items = list(new_items)
        except Exception:
            pass

    def make_photo(self, item):
        """把 PIL Image 转成 Tkinter PhotoImage"""
        if not ImageTk:
            return None
        try:
            img = load_icon_pil(item)
            if not img:
                return None
            img = img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def on_hover(self, tag, entering):
        """悬停效果：只改高亮层 hl_tag，背景卡片不受影响"""
        if entering:
            self.canvas.itemconfig(tag, fill="#1e2f5e", outline=C_ACCENT)
        else:
            # 如果是选中的格子，保持高亮
            idx = int(tag.split("_")[1])
            if self._selected_idx != idx:
                self.canvas.itemconfig(tag, fill="", outline="")

    def select(self, item, idx):
        """单击选中，保持高亮（只改高亮层）"""
        # 取消之前的选中
        if self._selected_idx >= 0:
            self.canvas.itemconfig(f"hl_{self._selected_idx}", fill="", outline="")
        # 选中当前
        self._selected_idx = idx
        self.canvas.itemconfig(f"hl_{idx}", fill="#1e2f5e", outline=C_ACCENT)

    def show_menu(self, event, item):
        """右键菜单：复制地址 / 删除"""
        self._close_menu()
        
        menu = tk.Menu(self.root, tearoff=0)
        
        # 复制地址
        menu.add_command(label="📄 复制地址", command=lambda: self.copy_path(item))
        menu.add_separator()
        
        # 删除此工具
        menu.add_command(label="🗑 删除此工具", command=lambda: self.delete_item(item))
        
        self._active_menu = menu
        
        # menu post will steal focus -> root FocusOut -> menu unposted immediately
        # so unbind root Button-1/FocusOut before posting, restore after menu closes
        self.root.bind("<Button-1>", None)
        self.root.bind("<FocusOut>", None)
        
        # restore bindings when menu is closed (unmapped)
        def _on_menu_closed(_e):
            self._restore_root_bindings()
        menu.bind("<Unmap>", _on_menu_closed)
        
        menu.post(event.x_root, event.y_root)

    def _close_menu(self):
        """close active menu + name tip"""
        if self._active_menu:
            try:
                self._active_menu.unpost()
            except Exception:
                pass
            self._active_menu = None
            self._restore_root_bindings()
        self._close_name_tip()

    def _restore_root_bindings(self):
        """restore root Button-1/FocusOut bindings"""
        try:
            self.root.bind("<Button-1>", lambda e: self._close_menu())
            self.root.bind("<FocusOut>", lambda e: self._close_menu())
        except Exception:
            pass

    def copy_path(self, item):
        """复制 .desktop 文件路径到剪贴板"""
        path = item.get("path", "")
        if path:
            self._copy_to_clipboard(path)
            self._show_notification(f"已复制地址: {item['name']}")
        else:
            self._show_notification("无路径可复制", True)

    def delete_item(self, item):
        """删除此工具（确认后删文件 + 从网格移除）"""
        if not messagebox.askyesno(
            "删除工具",
            f"确定删除 [{item['name']}] 吗？\n\n文件: {item['path']}",
        ):
            return
        
        try:
            os.remove(item["path"])
        except Exception as e:
            self._show_notification(f"删除失败: {e}", True)
            return
        
        # 从列表移除
        idx = self.items.index(item)
        self.items.pop(idx)
        # 同时从全量列表移除（否则重新扫描/切换筛选会加回来）
        if item in self._all_items:
            self._all_items.remove(item)
        self._save_order()   # 同步 + 落盘
        
        # 调整选中索引
        if self._selected_idx == idx:
            self._selected_idx = -1
        elif self._selected_idx > idx:
            self._selected_idx -= 1
        
        # 重绘 + 恢复选中高亮
        self.draw_grid()
        if self._selected_idx >= 0:
            self.canvas.itemconfig(f"hl_{self._selected_idx}", fill="#1e2f5e", outline=C_ACCENT)
        
        self._save_order()
        self._show_notification(f"已删除: {item['name']}")

    def _copy_to_clipboard(self, text):
        """复制到剪贴板"""
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _show_notification(self, msg, is_error=False):
        """顶部提示"""
        lbl = tk.Label(self.root, text=msg, font=("Microsoft YaHei", 10, "bold"),
                      fg=C_ERR if is_error else C_OK, bg=C_CARD, relief="flat")
        lbl.place(relx=0.5, y=6, anchor="n")
        lbl.after(2000, lbl.destroy)

    def launch(self, item):
        """双击启动工具（完全脱离父进程）"""
        if item["exec"]:
            try:
                subprocess.Popen(
                    item["exec"],
                    shell=True,
                    stdin=subprocess.DEVNULL,   # 不继承 toolbox 的 stdin
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,             # 不继承任何多余 fd
                    start_new_session=True,     # 新会话（等价 setsid）
                )
            except Exception as e:
                messagebox.showerror("启动失败", f"{item['name']}: {e}")


if __name__ == "__main__":
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    ToolboxApp(root)
    root.mainloop()
