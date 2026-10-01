#!/usr/bin/env python3
"""桌面歌词悬浮窗（Qt）——独立进程，通过 localhost socket(39462) 与 music.py 双向通信

背景像素级透明(WA_TranslucentBackground)，只显示歌词文字；
五行动态展开：上2行/当前行/下2行，随播放滚动；
底部控制条：播放暂停 / 上下首 / ±0.5s微调 / 关闭。

协议（每行一条，| 分隔）：
  music → qt:  title|<标题>
               lyric|<json [[sec,text],...]>|<offset>
               pos|<秒>|<总长>|<play|pause|stop>
               close
  qt → music: nudge|±0.5     pause     step|±1
"""
import sys, json, socket, time, os, ctypes
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSettings, QTimer, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QPainter, QColor, QFont, QCursor
from PyQt5.QtWidgets import (QApplication, QWidget, QToolButton,
                             QHBoxLayout, QPushButton, QGraphicsOpacityEffect)

PORT = 39462
W, H = 960, 116

# ── X11 真穿透：WA_TransparentForMouseEvents 只让 Qt 忽略事件，
#    必须改 X 输入区域。空 Rectangles 会 BadValue(实测失效)，
#    正确做法是 XShapeCombineMask + 1x1 全0 pixmap(Electron 同款)。
_X11 = ctypes.CDLL("libX11.so.6")
_X11.XOpenDisplay.restype = ctypes.c_void_p
_X11.XOpenDisplay.argtypes = [ctypes.c_char_p]
_X11.XFlush.argtypes = [ctypes.c_void_p]
_X11.XCreateGC.restype = ctypes.c_ulong
_X11.XCreateGC.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
_X11.XSetForeground.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
_X11.XFillRectangle.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
_X11.XFreeGC.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
_X11.XFreePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
_X11.XCreatePixmap.restype = ctypes.c_ulong
_X11.XCreatePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                               ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
_dpy = _X11.XOpenDisplay(None)
_XExt = ctypes.CDLL("libXext.so.6")   # XShape 扩展在 libXext
_XExt.XShapeCombineMask.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_ulong, ctypes.c_int]
_SHAPE_INPUT = 2
_SHAPE_SET = 0


def _input_pixmap_mask(widget, bits, w, h):
    """用 1bpp pixmap 设置窗口输入区域：bits=0 → 全穿透；bits=1 → 该区域可点"""
    wid = int(widget.winId())
    pm = _X11.XCreatePixmap(_dpy, wid, w, h, 1)
    gc = _X11.XCreateGC(_dpy, pm, 0, None)
    _X11.XSetForeground(_dpy, gc, bits)
    _X11.XFillRectangle(_dpy, pm, gc, 0, 0, w, h)
    _XExt.XShapeCombineMask(_dpy, wid, _SHAPE_INPUT, 0, 0, pm, _SHAPE_SET)
    _X11.XFlush(_dpy)
    _X11.XFreeGC(_dpy, gc)
    _X11.XFreePixmap(_dpy, pm)


def input_pass(widget):
    """清空输入区域 → 鼠标完全穿透，下面随便点"""
    _input_pixmap_mask(widget, 0, 1, 1)


def input_block(widget):
    """恢复整窗输入（悬停浮现控制条时可点可拖）；
    HiDPI 下 X 窗口是物理像素，pixmap 必须 ×devicePixelRatio"""
    r = widget.devicePixelRatioF() or 1.0
    _input_pixmap_mask(widget, 1, max(1, int(widget.width() * r)),
                       max(1, int(widget.height() * r)))


class NetThread(QThread):
    """接收 music.py 推送（子线程）"""
    sig = pyqtSignal(str)

    def run(self):
        while True:
            try:
                s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
                self.sock = s
                f = s.makefile("r", encoding="utf-8")
                while True:
                    line = f.readline()
                    if not line:
                        break
                    self.sig.emit(line.strip())
            except Exception:
                time.sleep(1)
            else:
                time.sleep(0.5)


class _Handle(QWidget):
    """左上角小拖拽把手：唯一可拖动区域（其余窗口主体鼠标穿透）"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setGeometry(12, 10, 30, 16)
        self.setCursor(Qt.SizeAllCursor)
        self._drag = None

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(QColor(255, 255, 255, 100))
        for i in range(3):
            x = 8 + i * 7
            p.drawLine(x, 5, x, 11)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            w = self.window()
            self._drag = (e.globalPos().x() - w.x(), e.globalPos().y() - w.y())

    def mouseMoveEvent(self, e):
        if self._drag:
            self.window().move(e.globalPos().x() - self._drag[0],
                               e.globalPos().y() - self._drag[1])

    def mouseReleaseEvent(self, e):
        if self._drag:
            w = self.window()
            w._qs.setValue("x", w.x())
            w._qs.setValue("y", w.y())
        self._drag = None


class LyricWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)   # ★ 背景真透明
        self.setWindowTitle("桌面歌词")
        self.resize(W, H)
        # 位置记忆（校验在屏幕内，否则居中；默认屏幕水平居中）
        qs = QSettings(os.path.expanduser("~/.config/music/lyric_pos.ini"), QSettings.IniFormat)
        self._qs = qs
        sc = QApplication.primaryScreen().availableGeometry()
        dx = int(qs.value("x", (sc.width() - W) // 2, int))
        dy = int(qs.value("y", sc.height() - H - 40, int))
        if dx < 0 or dx + W > sc.width():
            dx = (sc.width() - W) // 2
        if dy < 0 or dy + H > sc.height():
            dy = sc.height() - H - 40
        self.move(dx, dy)
        # 整窗穿透（showEvent 里用 X11 input shape 真正清空输入区域）
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._drag = None
        self.lines = []          # [(秒, 文本)]
        self.pos = 0.0
        self.duration = 0.0
        self._state = "stop"   # play/pause/stop
        self._t_last = time.monotonic()
        self._cur_idx = None   # 当前渲染的句索引（事件驱动重绘）
        # 本地平滑推进：网络 pos 只做校准，Tk 主循环忙也不丢句
        self._adv = QTimer(self)
        self._adv.timeout.connect(self._advance)
        self._adv.start(100)
        self.offset = 0.0
        self.paused = False
        self.title = ""

        # 底部控制条：平时隐藏，鼠标悬停 1.2s 才淡入（窄按钮，不占位置）
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 0, 8, 0)
        bar.setSpacing(6)
        btn_style = (
            "QPushButton{color:#d8d8e8;background:rgba(20,20,30,130);border:none;"
            "font-size:11px;border-radius:4px;}"
            "QPushButton:hover{color:#ffffff;background:rgba(255,255,255,70);}"
        )
        def mk(text, cb, w):
            b = QPushButton(text)
            b.setStyleSheet(btn_style)
            b.setFixedSize(w, 20)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            return b
        self.t_pause = mk("⏸", self.on_pause, 24)
        self.t_minus = mk("−0.5", lambda: self.on_nudge(-0.5), 38)
        self.t_plus = mk("+0.5", lambda: self.on_nudge(0.5), 38)
        self.t_lock = mk("🔓", self.toggle_lock, 26)
        self.t_close = mk("×", self.cmd_close, 22)
        for b in (self.t_pause, self.t_minus, self.t_plus, self.t_lock, self.t_close):
            bar.addWidget(b)
        bar.addStretch(1)
        ctrl = QWidget(self)
        ctrl.setLayout(bar)
        ctrl.setGeometry(0, H - 24, W, 24)
        self._ctrl = ctrl
        self._ctrl.hide()
        # 锁定态的小锁（很小，悬停才出，点击解锁）
        self._lock_btn = QPushButton("🔒", self)
        self._lock_btn.setStyleSheet(
            "QPushButton{color:#c8c8d8;background:rgba(20,20,30,130);border:none;"
            "font-size:9px;border-radius:3px;}"
            "QPushButton:hover{color:#ffffff;background:rgba(255,255,255,70);}")
        self._lock_btn.setFixedSize(16, 16)
        self._lock_btn.setGeometry(6, H - 22, 16, 16)
        self._lock_btn.clicked.connect(self.toggle_lock)
        self._lock_btn.hide()
        self._locked = False
        # 淡入动画
        self._ctrl_eff = QGraphicsOpacityEffect(self._ctrl)
        self._ctrl.setGraphicsEffect(self._ctrl_eff)
        self._ctrl_anim = QPropertyAnimation(self._ctrl_eff, b"opacity", self)
        self._ctrl_anim.setDuration(160)
        self._ctrl_anim.setEasingCurve(QEasingCurve.OutCubic)

        self.net = NetThread()
        self.net.sig.connect(self.on_line)
        self.net.start()
        self._drag = None

        # 悬停检测：鼠标在窗口内停留 ≥1.2s → 浮现控制条；移出 → 隐藏并恢复穿透
        self._hover_t0 = None
        self._ui_shown = False
        self._hover = QTimer(self)
        self._hover.timeout.connect(self._hover_check)
        self._hover.start(150)

    # ── 悬停交互：平时全穿透，悬停浮现控制条/小锁，可点 ──
    def _show_ui(self):
        self._ui_shown = True
        if self._locked:
            self._ctrl.hide()
            self._lock_btn.show()          # 锁定态：只出小锁
        else:
            self._ctrl.show()              # 解锁态：整条控制条
            self._lock_btn.hide()
        self._ctrl_anim.stop()
        self._ctrl_eff.setOpacity(0.0)
        self._ctrl_anim.setStartValue(0.0)
        self._ctrl_anim.setEndValue(1.0)
        self._ctrl_anim.start()
        # 恢复整窗输入（bounding 恒整窗：不能用 setMask 裁剪，
        # 否则控制条落在窗口形状外不可点）
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        input_block(self)

    def _hide_ui(self):
        self._ui_shown = False
        self._ctrl.hide()
        self._lock_btn.hide()
        self._ctrl_anim.stop()
        # 恢复：X11 输入区域清空 → 全穿透（透明区域也不挡）
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        input_pass(self)

    # ── 整窗任意位置拖拽（悬停浮现控制条期间；锁定态不可拖）──
    def mousePressEvent(self, e):
        if self._locked:
            return
        if e.button() == Qt.LeftButton:
            self._drag = (e.globalPos().x() - self.x(), e.globalPos().y() - self.y())

    def mouseMoveEvent(self, e):
        if self._drag:
            self.move(e.globalPos().x() - self._drag[0],
                     e.globalPos().y() - self._drag[1])

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._qs.setValue("x", self.x())
            self._qs.setValue("y", self.y())
        self._drag = None

    def _hover_check(self):
        inside = self.geometry().contains(QCursor.pos())
        now = time.monotonic()
        if inside:
            if self._hover_t0 is None:
                self._hover_t0 = now
            elif now - self._hover_t0 >= 1.2 and not self._ui_shown:
                self._show_ui()
        else:
            self._hover_t0 = None
            if self._ui_shown:
                self._hide_ui()

    # ── 渲染 ──
    def showEvent(self, e):
        """窗口真正创建后(ARGB visual 就绪)才设输入穿透——
        __init__ 里设会强制提前建窗，show 时 Qt 重建导致 winId 变、shape 丢失"""
        super().showEvent(e)
        if not getattr(self, "_shape_done", False):
            self._shape_done = True
            input_pass(self)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        idx = self._cur_idx if self._cur_idx is not None else self._display_idx()
        if 0 <= idx < len(self.lines):
            txt = self.lines[idx][1]
            # 单句：手动精确居中（QFontMetrics 算文本宽，避免 drawText rect 偏移）
            font = QFont("Microsoft YaHei", 30, QFont.Bold)
            p.setFont(font)
            from PyQt5.QtGui import QFontMetrics
            fm = QFontMetrics(font)
            tw = fm.horizontalAdvance(txt)
            # 面积兜底：窗口宽度随文字自适应（不整条 960 占位）
            target = max(240, min(tw + 90, 1500))
            if abs(self.width() - target) > 24:
                self.resize(target, H)
                self._ctrl.setGeometry(0, H - 24, self.width(), 24)
                if getattr(self, "_ui_shown", False):
                    input_block(self)   # resize 后重设输入区域
                elif getattr(self, "_shape_done", False):
                    input_pass(self)
            x = max(0, (self.width() - tw) // 2)
            y_center = H // 2                # 整窗垂直居中（平时无控制条）
            baseline = y_center + (fm.ascent() - fm.descent()) // 2
            p.setPen(QColor(0, 0, 0, 200))
            p.drawText(x + 2, baseline + 2, txt)    # 黑色阴影
            p.setPen(QColor(255, 255, 255))
            p.drawText(x, baseline, txt)            # 白色主字
        p.end()

    def _current_index(self):
        t = self.pos + self.offset
        idx = -1
        for k, (s, _) in enumerate(self.lines):
            if s <= t:
                idx = k
            else:
                break
        return idx

    # ── 接收 ──
    def on_line(self, line):
        parts = line.split("|", 1)
        kind = parts[0]
        if kind == "lyric":
            _, body, off = line.split("|", 2)
            self.lines = json.loads(body)
            self.offset = float(off)
            self.pos = 0.0              # ★ 切歌：新歌词从 0 开始，旧 pos 不残留
            self.duration = 0.0
            self._cur_idx = None
            self._sync_render()
        elif kind == "pos":
            _, p, d, st = line.split("|")
            self.pos = float(p)          # 精确校准（seek/暂停后不会漂移）
            self.duration = float(d)
            self._state = st
            self._t_last = time.monotonic()
            self.paused = (st == "pause")
            self.t_pause.setText("▶" if self.paused else "⏸")
            self._sync_render()
        elif kind == "title":
            self.title = parts[1]
        elif kind == "close":
            self.close()

    # ── 发命令给 music.py ──
    def cmd(self, msg):
        for attempt in range(2):
            try:
                s = getattr(self.net, "sock", None)
                if s is None:
                    s = socket.create_connection(("127.0.0.1", PORT), timeout=2)
                    self.net.sock = s
                s.sendall((msg + "\n").encode())
                return
            except Exception:
                self.net.sock = None          # 连接失效：清掉，重连再试一次
                if attempt == 0:
                    time.sleep(0.15)

    def toggle_lock(self):
        """🔓/🔒 锁定切换：锁定后悬浮只出小锁、不可拖；解锁恢复控制条+可拖"""
        self._locked = not self._locked
        self.t_lock.setText("🔒" if self._locked else "🔓")
        if self._locked:
            self._ctrl.hide()
            self._lock_btn.show()      # 只留小锁
        else:
            self._ctrl.show()
            self._lock_btn.hide()

    def _display_idx(self):
        t = self.pos + self.offset
        idx = -1
        for k, (s, _) in enumerate(self.lines):
            if s <= t:
                idx = k
            else:
                break
        return idx

    def _sync_render(self):
        """只在当前句变化时才重绘（事件驱动：每句画一次，不每100ms全量重绘）"""
        idx = self._display_idx()
        if idx != self._cur_idx:
            self._cur_idx = idx
            self.update()

    def _advance(self):
        """本地按真实时间推进播放位置（播放中），显示不依赖推送频率"""
        if self._state == "play" and self.lines:
            now = time.monotonic()
            self.pos += now - self._t_last
            self._t_last = now
            self._sync_render()
        elif self._state != "play":
            self._t_last = time.monotonic()

    def on_pause(self):
        self.cmd("pause")

    def on_nudge(self, d):
        self.offset += d
        self.update()
        self.cmd(f"nudge|{d}")

    def cmd_close(self):
        self.cmd("close-ctrl")
        self.close()

def main():
    app = QApplication(sys.argv)
    w = LyricWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()