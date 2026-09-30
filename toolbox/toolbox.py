#!/usr/bin/env python3
"""toolbox.py — 极简工具启动器

扫描 sh/desktop/*.desktop，网格显示图标 + 文字，点击启动。
"""

import os
import subprocess
import tkinter as tk
from pathlib import Path

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

# ── 配置 ──────────────────────────────────────────────
DESKTOP_DIR = Path("/home/a1/sh/desktop")
ICON_SIZE = 64
GRID_COLS = 4
PADDING = 20
CELL_W = 140
CELL_H = 140

# 主题色（跟其他工具一致）
C_BG = "#1a1a2e"
C_CARD = "#16213e"
C_FG = "#e0e0ff"
C_MUTED = "#8888aa"
C_ACCENT = "#7b68ee"

# 图标搜索：主题 + 目录类型 + 尺寸
ICON_THEMES = ["breeze-dark", "breeze", "hicolor", "Adwaita"]
ICON_TYPES = ["devices", "apps", "actions", "categories", "preferences", "places"]
ICON_SIZES = ["64", "48", "32", "24", "16", "128", "256", "96"]


def _build_icon_paths():
    """生成所有可能的图标路径"""
    paths = []
    # hicolor 结构不同
    for size in ICON_SIZES:
        paths.append(f"/usr/share/icons/hicolor/{size}x{size}/apps")
        paths.append(f"/usr/share/icons/hicolor/{size}x{size}/actions")
    # KDE/Adwaita 结构
    for theme in ICON_THEMES:
        if theme == "hicolor":
            continue
        for itype in ICON_TYPES:
            for size in ICON_SIZES:
                paths.append(f"/usr/share/icons/{theme}/{itype}/{size}")
    paths.append("/usr/share/pixmaps")
    return paths


ICON_DIRS = _build_icon_paths()

# 默认图标（找不到时用）
DEFAULT_ICON_PATH = None


def parse_desktop(file_path):
    """解析 .desktop 文件，提取 Name, Icon, Exec"""
    name = file_path.stem  # 兜底用文件名
    icon = None
    exec_cmd = None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("Name="):
                    name = line[5:].strip()
                elif line.startswith("Icon="):
                    icon = line[5:].strip()
                elif line.startswith("Exec="):
                    exec_cmd = line[5:].strip()
    except Exception:
        pass

    return {
        "name": name,
        "icon": icon,
        "exec": exec_cmd,
        "path": str(file_path),
    }


def find_icon(icon_name):
    """在系统目录查找图标文件，返回 PIL Image 或 None
    
    支持 PNG（直接加载）和 SVG（用 rsvg-convert 转 PNG）
    """
    if not icon_name or not Image:
        return None

    # 如果是完整路径，直接加载
    if os.path.isfile(icon_name):
        return _load_image(icon_name)

    # 在常见目录搜索
    for icon_dir in ICON_DIRS:
        for ext in [".png", ".svg", ".xpm"]:
            path = os.path.join(icon_dir, icon_name + ext)
            if os.path.isfile(path):
                img = _load_image(path)
                if img:
                    return img
            # 有些图标带 @2x 后缀
            path2 = os.path.join(icon_dir, icon_name + "@2x" + ext)
            if os.path.isfile(path2):
                img = _load_image(path2)
                if img:
                    return img
        # 单数/复数兜底（如 shortcuts → shortcut）
        singular = icon_name[:-1] if icon_name.endswith("s") else icon_name + "s"
        for ext in [".png", ".svg", ".xpm"]:
            path = os.path.join(icon_dir, singular + ext)
            if os.path.isfile(path):
                img = _load_image(path)
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
        self.root.geometry("800x600")
        self.root.configure(bg=C_BG)
        self.root.resizable(False, False)

        self.items = []
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

    def load_desktops(self):
        """扫描 desktop 目录"""
        if not DESKTOP_DIR.exists():
            return
        for f in sorted(DESKTOP_DIR.glob("*.desktop")):
            self.items.append(parse_desktop(f))

    def draw_grid(self):
        """用 Canvas 画网格"""
        if not self.items:
            return

        rows = (len(self.items) + GRID_COLS - 1) // GRID_COLS
        canvas_w = GRID_COLS * CELL_W + (GRID_COLS + 1) * PADDING
        canvas_h = rows * CELL_H + (rows + 1) * PADDING

        self.canvas = tk.Canvas(
            self.root,
            width=canvas_w,
            height=canvas_h,
            bg=C_BG,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        for idx, item in enumerate(self.items):
            col = idx % GRID_COLS
            row = idx // GRID_COLS
            x = PADDING + col * CELL_W + CELL_W // 2
            y = PADDING + row * CELL_H + CELL_H // 2

            self.draw_cell(self.canvas, item, x, y)

    def draw_cell(self, canvas, item, cx, cy):
        """画一个格子：图标 + 文字"""
        # 背景卡片
        x0, y0 = cx - CELL_W // 2 + 10, cy - CELL_H // 2 + 10
        x1, y1 = cx + CELL_W // 2 - 10, cy + CELL_H // 2 - 10
        canvas.create_rectangle(
            x0, y0, x1, y1,
            fill=C_CARD, outline="",
        )

        # 图标
        photo = self.make_photo(item)
        if photo:
            # 居中偏上
            iy = cy - 15
            canvas.create_image(cx, iy, image=photo, anchor="center")
            # 保存引用防止 GC
            setattr(self, f"_img_{id(item)}", photo)
        else:
            # 兜底：画个 emoji
            canvas.create_text(cx, cy - 15, text="📦", font=("Segoe UI Emoji", 28), fill=C_FG)

        # 文字
        name = item["name"]
        if len(name) > 10:
            name = name[:9] + "…"
        canvas.create_text(
            cx, cy + 30,
            text=name,
            font=("Microsoft YaHei", 9),
            fill=C_FG,
        )

        # 绑定点击
        tag = f"cell_{id(item)}"
        canvas.tag_bind(tag, "<Button-1>", lambda e, i=item: self.launch(i))
        canvas.tag_bind(tag, "<Enter>", lambda e, t=tag: self.on_hover(t, True))
        canvas.tag_bind(tag, "<Leave>", lambda e, t=tag: self.on_hover(t, False))

        # 用矩形作为点击区域
        canvas.create_rectangle(
            x0, y0, x1, y1,
            fill="", outline="",
            tags=tag,
        )

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
        """悬停效果"""
        if entering:
            self.canvas.itemconfig(tag, fill="#1e2f5e", outline=C_ACCENT)
        else:
            self.canvas.itemconfig(tag, fill="", outline="")

    def launch(self, item):
        """启动工具"""
        if item["exec"]:
            try:
                subprocess.Popen(
                    item["exec"],
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                tk.messagebox.showerror("启动失败", f"{item['name']}: {e}")


if __name__ == "__main__":
    root = tk.Tk()
    ToolboxApp(root)
    root.mainloop()
