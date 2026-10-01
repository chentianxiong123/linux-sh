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
import sys, json, socket, time, os
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt5.QtGui import QPainter, QColor, QFont
from PyQt5.QtWidgets import (QApplication, QWidget, QToolButton,
                             QHBoxLayout, QPushButton)

PORT = 39462
W, H = 960, 260


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
        # 主体鼠标穿透：只有小拖拽把手和控制条可交互
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._drag = None
        self._handle = None
        self.lines = []          # [(秒, 文本)]
        self.pos = 0.0
        self.offset = 0.0
        self.paused = False
        self.title = ""

        # 底部控制条（透明底小按钮）
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)
        btn_style = (
            "QPushButton{color:#c8c8d8;background:transparent;border:none;font-size:12px;}"
            "QPushButton:hover{color:#ffffff;background:rgba(255,255,255,40);border-radius:3px;}"
        )
        def mk(text, cb):
            b = QPushButton(text)
            b.setStyleSheet(btn_style)
            b.setFixedHeight(22)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            return b
        self.t_title = mk("-", lambda: None)
        self.t_title.setStyleSheet("QPushButton{color:#8a8a98;background:transparent;border:none;font-size:11px;}")
        self.t_pause = mk("⏸", self.on_pause)
        self.t_minus = mk("−0.5s", lambda: self.on_nudge(-0.5))
        self.t_plus = mk("+0.5s", lambda: self.on_nudge(0.5))
        self.t_close = mk("×", self.cmd_close)
        for b in (self.t_title, self.t_pause,
                  self.t_minus, self.t_plus, self.t_close):
            bar.addWidget(b)
        ctrl = QWidget(self)
        ctrl.setLayout(bar)
        ctrl.setGeometry(0, H - 26, W, 26)
        ctrl.setAttribute(Qt.WA_TransparentForMouseEvents, False)

        # 小拖拽把手（唯一可拖区域）
        self._handle = _Handle(self)
        self._handle.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._handle.raise_()

        self.net = NetThread()
        self.net.sig.connect(self.on_line)
        self.net.start()
        self._drag = None

    # ── 渲染 ──
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = W // 2, 104
        idx = self._current_index()
        # 桌面歌词：当前句 + 附近一句两句
        rows = [(-1, 0.55), (0, 1.0), (1, 0.55)]
        for off, alpha in rows:
            k = idx + off
            if 0 <= k < len(self.lines):
                txt = self.lines[k][1]
                if off == 0:
                    # 当前行：黑色阴影 + 白色大字
                    p.setFont(QFont("Microsoft YaHei", 27, QFont.Bold))
                    p.setPen(QColor(0, 0, 0, 200))
                    p.drawText(cx + 2, cy + off * 36 + 2 + 2, W, 44, Qt.AlignHCenter, txt)
                    p.setPen(QColor(255, 255, 255))
                    p.drawText(cx, cy + off * 36 + 2, W, 44, Qt.AlignHCenter, txt)
                else:
                    p.setFont(QFont("Microsoft YaHei", 13))
                    a = int(255 * alpha)
                    p.setPen(QColor(255, 255, 255, a))
                    p.drawText(cx, cy + off * 34 + 2, W, 30, Qt.AlignHCenter, txt)
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
            self.update()
        elif kind == "pos":
            _, p, _d, st = line.split("|")
            self.pos = float(p)
            self.paused = (st == "pause")
            self.t_pause.setText("▶" if self.paused else "⏸")
            self.update()
        elif kind == "title":
            self.title = parts[1]
            self.t_title.setText(parts[1])
        elif kind == "close":
            self.close()

    # ── 发命令给 music.py ──
    def cmd(self, msg):
        try:
            self.net.sock.sendall((msg + "\n").encode())
        except Exception:
            pass

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