#!/usr/bin/env python3
"""toolbox.py — 极简工具启动器

扫描 sh/desktop/*.desktop，网格显示图标 + 文字，点击启动。
"""

import os
import shutil
import subprocess
import time
import tkinter as tk
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
C_OK = "#4ade80"    # 成功
C_ERR = "#f87171"   # 错误

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
        self.root.wm_protocol("WM_DELETE_WINDOW", self._on_close)

        # 注册系统级拖放：从桌面拖 .desktop 文件进来 = 导入工具
        if HAS_DND:
            try:
                self.root.drop_target_register(DND_FILES)
                self.root.dnd_bind("<<Drop>>", self.on_drop)
            except Exception:
                pass

        self.items = []
        self._selected_idx = -1  # 当前选中的格子索引（-1 = 无）
        
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
        """窗口关闭"""
        self.root.destroy()

    def load_desktops(self):
        """扫描 desktop 目录"""
        self.items = []
        if not DESKTOP_DIR.exists():
            return
        for f in sorted(DESKTOP_DIR.glob("*.desktop")):
            self.items.append(parse_desktop(f))
        # 加载保存的顺序
        self._load_order()

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
        """用 Canvas 画网格"""
        if not self.items:
            return

        rows = (len(self.items) + GRID_COLS - 1) // GRID_COLS
        canvas_w = GRID_COLS * CELL_W + (GRID_COLS + 1) * PADDING
        canvas_h = rows * CELL_H + (rows + 1) * PADDING

        # 销毁旧 canvas
        if hasattr(self, 'canvas'):
            self.canvas.destroy()

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

            self.draw_cell(self.canvas, item, x, y, idx)

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

        # 3. 图标
        photo = self.make_photo(item)
        if photo:
            iy = cy - 15
            canvas.create_image(cx, iy, image=photo, anchor="center", tags=tag)
            setattr(self, f"_img_{grid_idx}", photo)  # 防止 GC
        else:
            canvas.create_text(cx, cy - 15, text="📦", font=("Segoe UI Emoji", 28), fill=C_FG, tags=tag)

        # 4. 文字
        name = item["name"]
        if len(name) > 10:
            name = name[:9] + "…"
        canvas.create_text(
            cx, cy + 30,
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
        """根据索引计算格子中心坐标"""
        col = idx % GRID_COLS
        row = idx // GRID_COLS
        return PADDING + col * CELL_W + CELL_W // 2, PADDING + row * CELL_H + CELL_H // 2

    def _idx_at(self, x, y):
        """根据鼠标坐标计算格子索引"""
        col = (x - PADDING) // CELL_W
        row = (y - PADDING) // CELL_H
        return row * GRID_COLS + col

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
        
        # 单击：选中（双击由 Double-Button-1 处理）
        if not was_dragging:
            self.select(item, grid_idx)

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
        
        # 用坐标直接算索引，不用 _pos 缓存
        dst_idx = self._idx_at(event.x, event.y)
        src_idx = self.items.index(self._drag_item)
        total = len(self.items)
        
        if 0 <= dst_idx < total and dst_idx != src_idx:
            self.canvas.itemconfig(f"hl_{dst_idx}", fill="#2a4f7e", outline=C_ACCENT)
            self._last_target_tag = f"hl_{dst_idx}"

    def _complete_drop(self, event):
        """完成拖拽：交换两个格子（局部更新）"""
        src_idx = self.items.index(self._drag_item)
        dst_idx = self._idx_at(event.x, event.y)
        total = len(self.items)
        
        if dst_idx < 0 or dst_idx >= total or dst_idx == src_idx:
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
        """保存图标顺序到文件"""
        import json
        from pathlib import Path
        order_file = Path.home() / ".config" / "toolbox" / "order.json"
        order_file.parent.mkdir(parents=True, exist_ok=True)
        order = [item['path'] for item in self.items]
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
        """右键菜单：极简两项"""
        menu = tk.Menu(self.root, tearoff=0)
        
        # 复制地址
        menu.add_command(label="📄 复制地址", command=lambda: self.copy_path(item))
        menu.add_separator()
        
        # 删除此工具
        menu.add_command(label="🗑 删除此工具", command=lambda: self.delete_item(item))
        
        # 在鼠标位置显示菜单
        menu.post(event.x_root, event.y_root)

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
        if not tk.messagebox.askyesno(
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
                tk.messagebox.showerror("启动失败", f"{item['name']}: {e}")


if __name__ == "__main__":
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    ToolboxApp(root)
    root.mainloop()
