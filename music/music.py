#!/usr/bin/env python3
"""music.py — B站音乐搜索播放

架构：
  Tkinter GUI  →  B站 API (search / view / playurl)
               →  mpv 无窗口播放（IPC 控制暂停/seek）

依赖：requests, mpv (apt install mpv)
"""

import os
import json
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

import requests

# ── 收藏：只存 ID/标题，不缓存音频 ────────────────
FAV_FILE = Path.home() / ".config" / "music" / "favorites.json"
LYRIC_MAP_FILE = Path.home() / ".config" / "music" / "lyrics.json"  # bvid → 已选歌词版本+偏移
CFG_FILE = Path.home() / ".config" / "music" / "settings.json"   # 音量等偏好
DEFAULT_VOLUME = 100


def _load_pref():
    """读取偏好设置（当前只存音量）"""
    try:
        if CFG_FILE.exists():
            with open(CFG_FILE) as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def _save_pref(pref):
    """写偏好设置，ensure_ascii=True 防非法字符"""
    try:
        CFG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(pref, f, ensure_ascii=True)
    except Exception as e:
        print(f"[设置保存失败] {e}", file=sys.stderr)


def _load_favs():
    """读取收藏（[{bvid,title,author,duration,qn}]）"""
    try:
        if FAV_FILE.exists():
            with open(FAV_FILE) as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save_fav(entry):
    """收藏一条，同 bvid 去重"""
    try:
        FAV_FILE.parent.mkdir(parents=True, exist_ok=True)
        favs = _load_favs()
        favs = [f for f in favs if f.get("bvid") != entry.get("bvid")]
        favs.insert(0, entry)
        # ensure_ascii=True：任何 unicode（含孤儿代理项）都转义存储，绝不抛错
        # B站标题可能含 \ud800 之类非法字符，ensure_ascii=False 写入会 UnicodeEncodeError
        with open(FAV_FILE, "w", encoding="utf-8") as f:
            json.dump(favs, f, ensure_ascii=True)
    except Exception as e:
        print(f"[收藏保存失败] {e}", file=sys.stderr)


def _load_lyric_map():
    """读取已选歌词记录 {bvid: {id,name,artist,dur,offset}}"""
    try:
        if LYRIC_MAP_FILE.exists():
            with open(LYRIC_MAP_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_lyric_map(lyric_map):
    """持久化已选歌词记录"""
    try:
        LYRIC_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LYRIC_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(lyric_map, f, ensure_ascii=True)
    except Exception as e:
        print(f"[歌词记录保存失败] {e}", file=sys.stderr)


def _remove_fav(bvid):
    """取消收藏"""
    try:
        favs = _load_favs()
        favs = [f for f in favs if f.get("bvid") != bvid]
        with open(FAV_FILE, "w", encoding="utf-8") as f:
            json.dump(favs, f, ensure_ascii=True)
    except Exception as e:
        print(f"[收藏删除失败] {e}", file=sys.stderr)

# ── B站 API ──────────────────────────────────────────────
BUILTIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://search.bilibili.com/",
    "Origin": "https://search.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# ── DASH 音频音轨 id（纯音频，不是视频码率）─────────
# 这些是 B站 dash.audio[].id 的取值，不是“视频清晰度”
AUDIO_64K    = 30216   # 64k
AUDIO_128K   = 30232   # 128k
AUDIO_192K   = 30280   # 192k（免费最高）
AUDIO_FLAC   = 30250   # FLAC（仅会员）
AUDIO_PRIORITY = [AUDIO_192K, AUDIO_128K, AUDIO_64K, AUDIO_FLAC]
PREFERRED_QN = AUDIO_192K

_s = requests.Session()
_s.headers.update(BUILTIN_HEADERS)
# 先过一遍首页拿基础 cookie，再挂 buvid3
_s.get("https://www.bilibili.com", timeout=10)
_s.headers["Cookie"] = f"buvid3={uuid.uuid4()}"

API = {
    "search":  "https://api.bilibili.com/x/web-interface/search/type",
    "view":    "https://api.bilibili.com/x/web-interface/view",
    "playurl": "https://api.bilibili.com/x/player/playurl",
}

_tag_re = re.compile(r"<[^>]+>")


def _req(url, **kw):
    r = _s.get(url, timeout=10, **kw)
    r.raise_for_status()
    return r


def _parse_dur(s):
    """B站 时长串 'mm:ss' / 'h:mm:ss' → 秒；解析失败返回 -1"""
    try:
        sec = 0
        for v in s.strip().split(":"):
            sec = sec * 60 + int(v)
        return sec
    except Exception:
        return -1


def _rank(item):
    """排序加权分：越低越靠前。包装度(符号/包装词) + 碎片/教学降权"""
    t = item["title"]
    pkg = 0
    # 括号修饰越多越沉底（[4K]/【爷青回】…是搬运/包装）
    for ch in "[]【】（）()":
        pkg += t.count(ch)
    low = t.lower()
    for w in ("4k", "无损", "hi-res", "hires", "爷青回", "修复", "极致",
              "超清", "高清", "最高音质", "官方", "精彩", "现场"):
        if w in low:
            pkg += 1
    # 非歌内容降权：教学区 / 零碎片段(<1:30) / 过长
    if item.get("typename") in ("音乐教学",):
        pkg += 2
    if item["sec"] < 90 or item["sec"] > 600:
        pkg += 2
    return pkg


def _search(keyword):
    """B站搜索：API 原生 duration=1(10分钟以下) 过滤短视频，本地再按时长升序
    参数：duration 0全/1<10分/2 10-30分/3 30-60分/4>60分；order 排序；tids 分区"""
    r = _req(API["search"], params={
        "keyword": keyword,
        "search_type": "video",
        "page": 1,
        "pagesize": 50,
        "duration": 1,   # 服务器端就只要 10 分钟以下的
        "tids": 3,       # 音乐区：服务器端只搜音乐（滤掉日常/影视剪辑等）
    }).json()
    if r.get("code") != 0:
        raise ValueError(r.get("message", "搜索失败"))
    out = []
    for it in r.get("data", {}).get("result", []):
        out.append({
            "bvid": it.get("bvid", ""),
            "title": _tag_re.sub("", it.get("title", "")),
            "duration": it.get("duration", ""),
            "author": it.get("author", ""),
            "typename": it.get("typename", ""),   # 分区名：MV/翻唱/音乐现场…
            "sec": _parse_dur(it.get("duration", "")),
        })
    # 解析失败/异常时长滤掉，剩余排序加权：朴素标题+完整歌在前，包装/教学/碎片沉底；同权重内短优先
    out = [it for it in out if it["sec"] > 0]
    out.sort(key=lambda it: (_rank(it), it["sec"]))
    return out


def _get_cid(bvid):
    r = _req(API["view"], params={"bvid": bvid}).json()
    if r.get("code") != 0:
        raise ValueError(r.get("message", "获取 cid 失败"))
    return r["data"]["cid"]


def _get_audio_url(bvid, cid, prefer_qn=PREFERRED_QN):
    """走 DASH 纯音频流（dash.audio[]），不拉视频

    原理：fnval=16 开启 DASH 模式，音视频分离。
          dash.audio[] 里的 URL 天生是纯音频 M4A（.m4s），
          不需要 ffmpeg 剥视频，带宽小、音质高。

    qn 用 30xxx 系列是“选音轨 id”，不是选视频清晰度。
    """
    r = _req(API["playurl"], params={
        "bvid": bvid, "cid": cid,
        "qn": prefer_qn, "fnval": 16,
        "fourk": 1,
        # 注意：不加 platform=html5 / highbit=1，
        # 这两个参数会强制返回视频模式（durl），而不是纯音频（dash.audio）
    }).json()
    if r.get("code") != 0:
        raise ValueError(r.get("message", "获取音频流失败"))
    d = r.get("data", {})
    dash = d.get("dash") or {}
    audio_list = dash.get("audio") or []
    if not audio_list:
        raise ValueError("无可用音频流（dash.audio 为空）")

    # 优先精确匹配 prefer_qn，否则按优先级降级
    by_id = {int(a.get("id", 0)): a for a in audio_list}
    for qn in [prefer_qn] + AUDIO_PRIORITY:
        if qn in by_id:
            a = by_id[qn]
            url = a.get("baseUrl") or a.get("base_url") or ""
            if not url:
                backup = a.get("backupUrl") or a.get("backup_url") or []
                if isinstance(backup, list) and backup:
                    url = backup[0]
            if url:
                return url, qn, a.get("mimeType", "audio/mp4")

    # 最后兜底：直接拿第一条
    a = audio_list[0]
    url = a.get("baseUrl") or a.get("base_url") or ""
    if not url:
        raise ValueError("无可用音频 URL")
    return url, int(a.get("id", 0)), a.get("mimeType", "audio/mp4")


# ── 播放器（mpv 无窗口 + IPC 控制） ─────────────────
# mpv 替代 ffmpeg+paplay：
#   1. 暂停/seek 是 mpv 内部原子操作，立即生效无缓冲残留
#   2. seek 不需要重启进程（原生 seek）
#   3. 解码/网络/断线重连都由 mpv 处理
class Player:
    def __init__(self, on_tick=None, on_end=None):
        self.mpv = None
        self.url = ""
        self.duration = 0
        self.offset = 0.0          # seek 起点（秒）
        self.started_at = 0.0
        self.paused = False
        self._pause_started = 0.0  # 本次暂停开始的时间（计时补偿用）
        self._volume = 100.0       # 音量 0-100（mpv 原生属性）
        self._tick_cb = on_tick
        self._end_cb = on_end
        self._ipc_path = f"/tmp/music-mpv-{os.getpid()}.sock"

    # ── IPC（mpv input-ipc-server 用 JSON 协议） ──
    def _ipc(self, cmd):
        """发送 JSON 命令（fire-and-forget，微秒级）"""
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(self._ipc_path)
            s.sendall((json.dumps({"command": cmd}) + "\n").encode())
            s.close()
        except Exception:
            pass

    def _ipc_reply(self, cmd):
        """发送 JSON 命令并读应答（查询用）"""
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(self._ipc_path)
            s.sendall((json.dumps({"command": cmd}) + "\n").encode())
            time.sleep(0.1)
            s.settimeout(0.3)
            resp = b""
            try:
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
            except socket.timeout:
                pass
            s.close()
            try:
                return json.loads(resp.decode())
            except Exception:
                return None
        except Exception:
            return None

    # ── 生命周期 ──
    def load(self, url, duration, offset=0.0):
        self.stop()
        self.url = url
        self.duration = duration
        self.offset = offset
        self.paused = False
        self.started_at = 0.0

    def start(self):
        """启动 mpv 无窗口播放"""
        if not self.url or self.mpv is not None:
            return
        try:
            if os.path.exists(self._ipc_path):
                os.unlink(self._ipc_path)
        except OSError:
            pass

        cmd = [
            "mpv", "--no-video", "--no-terminal", "--no-audio-display",
            "--input-ipc-server=" + self._ipc_path,
            "--audio-buffer=0.2",
            "--http-header-fields=Referer: https://search.bilibili.com/, "
            "Origin: https://search.bilibili.com, "
            f"Cookie: buvid3={uuid.uuid4()}, "
            "Accept: application/json, text/plain, */*",
            "--user-agent=" + BUILTIN_HEADERS["User-Agent"],
        ]
        if self.offset > 0:
            cmd.append(f"--start={self.offset}")
        cmd.append(self.url)

        self.mpv = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # 等 IPC socket 就绪
        for _ in range(50):
            if self.mpv.poll() is not None or os.path.exists(self._ipc_path):
                break
            time.sleep(0.1)
        self.set_volume(self._volume)   # 启动后套用音量和静音状态
        self.started_at = time.time()

    def pause(self):
        """暂停：mpv 内部原子暂停，立即静音无缓冲残留"""
        if self.mpv is None or self.paused:
            return
        self._ipc(["set_property", "pause", True])
        self.paused = True
        self._pause_started = time.time()

    def resume(self):
        if self.mpv is None or not self.paused:
            return
        self._ipc(["set_property", "pause", False])
        # 用 mpv 真实位置校准，彻底消除估算误差
        r = self._ipc_reply(["get_property", "time-pos"])
        if r and r.get("data") is not None:
            self.offset = float(r["data"])
            self.started_at = time.time()
        else:
            self.started_at += time.time() - self._pause_started
        self.paused = False

    def toggle(self):
        if self.paused:
            self.resume()
        else:
            self.pause()

    def seek(self, target):
        """跳转：mpv 内部 seek，不重启进程"""
        if self.mpv is not None and self.mpv.poll() is None:
            self._ipc(["seek", target, "absolute"])
        self.offset = target
        self.started_at = time.time()

    def stop(self):
        if self.mpv is not None:
            if self.mpv.poll() is None:
                self._ipc(["quit"])
                try:
                    self.mpv.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.mpv.kill()
            self.mpv = None
        try:
            if os.path.exists(self._ipc_path):
                os.unlink(self._ipc_path)
        except OSError:
            pass
        self.url = ""
        self.paused = False
        self.started_at = 0.0
        self.offset = 0.0

    def set_volume(self, vol, mute=None):
        """音量 0-100：mpv 原生 volume 属性，一条 IPC 即生效"""
        self._volume = max(0.0, min(100.0, float(vol)))
        if self.mpv is not None and self.mpv.poll() is None:
            self._ipc(["set_property", "volume", self._volume])
            if mute is not None:
                self._ipc(["set_property", "mute", bool(mute)])

    def is_playing(self):
        return self.mpv is not None and self.mpv.poll() is None

    def tick(self):
        """定时上报播放位置；暂停时也上报(固定 pos + pause 状态)，字幕才能同步暂停"""
        if self.mpv is None or self.mpv.poll() is not None:
            if self.url and self._end_cb:
                self._end_cb()
            return
        if self.paused:
            if self._tick_cb:
                # 暂停：pos 固定在最后已知位置，状态带 pause（挂件据此停止推进）
                self._tick_cb(getattr(self, "_last_pos", self.offset), self.duration)
            return
        pos = self.offset + (time.time() - self.started_at)
        self._last_pos = pos
        if self._tick_cb:
            self._tick_cb(pos, self.duration)


# ── GUI ──────────────────────────────────────────────────
C = {
    "bg": "#1a1a2e", "card": "#16213e",
    "fg": "#e0e0ff", "muted": "#8888aa",
    "accent": "#7b68ee", "active": "#0f3460",
    "ok": "#4ade80", "err": "#f87171",
}


def notify(root, msg, kind="ok"):
    label = tk.Label(
        root, text=msg, font=("Microsoft YaHei", 10, "bold"),
        fg=C["ok"] if kind == "ok" else C["err"],
        bg="#0f0f23", relief="flat",
    )
    label.place(relx=0.5, y=8, anchor="n")
    label.after(1800, label.destroy)


class MusicApp:
    def __init__(self, root):
        self.root = root
        self.root.title("B站音乐")
        self.root.geometry("900x600")   # 默认横向更宽
        self.root.configure(bg=C["bg"])
        self.root.wm_protocol("WM_DELETE_WINDOW", self._on_close)

        self.results = []
        self.current = None
        self._mode = "search"        # 列表模式: search / fav
        self._source = self.results  # 当前列表数据源（搜索/收藏共用）
        self._play_mode = "order"    # 播放模式: order循环 / random随机 / single单曲
        self._lyric_lines = []   # 当前歌词 [(秒, 行)]
        self._lyric_offset = 0.0 # 歌词手动微调偏移（秒）
        self._last_lyric = None  # 上次显示的歌词行（去重刷新）
        self._lyric_win = None   # (废弃)原 Tk 歌词窗引用
        self._lyric_label = None # (废弃)
        self._pick_win = None    # 歌词候选窗
        self._lyric_map = _load_lyric_map()  # bvid → 已选歌词版本
        self._cur_bvid = None    # 当前播放/选词歌曲 bvid
        self._lyric_on = tk.BooleanVar(value=self._lyric_map.get("_on", True))  # 桌面歌词总开关（持久化）
        self._lyric_canvas = None  # 歌词页 KTV Canvas
        # 桌面透明挂件(Qt 进程)通信
        self._lyr_sock = None
        self._lyr_srv = None
        self._lyr_proc = None
        self._start_lyric_server()
        self.volume = int(_load_pref().get("volume", DEFAULT_VOLUME))
        self.vol_pct = None   # 在 _build 里创建，滑杆回调可能先触发
        self.player = Player(on_tick=self._on_tick, on_end=self._on_end)
        self.player.set_volume(self.volume)
        self._build()
        self._poll_tick()

    def _on_close(self):
        """窗口关闭：停播放器、关歌词页/候选窗/Qt 挂件"""
        try:
            self.player.stop()
        except Exception:
            pass
        if self._lyr_sock:
            try:
                self._lyr_sock.sendall(b"close\n")
                self._lyr_sock.close()
            except Exception:
                pass
        if self._lyr_proc:
            try:
                self._lyr_proc.terminate()
            except Exception:
                pass
        for w in (self._pick_win, self._lyric_win):
            if w:
                try:
                    w.destroy()
                except Exception:
                    pass
        self.root.destroy()

    def _start_lyric_server(self):
        """本地监听，等 Qt 挂件连入"""
        try:
            self._lyr_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._lyr_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._lyr_srv.bind(("127.0.0.1", 39462))
            self._lyr_srv.listen(1)
            self._lyr_srv.settimeout(1)
        except Exception:
            self._lyr_srv = None
        threading.Thread(target=self._lyr_accept, daemon=True).start()

    def _lyr_accept(self):
        """接受 Qt 挂件连接，读它的命令"""
        while self._lyr_srv:
            try:
                conn, _ = self._lyr_srv.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            self._lyr_sock = conn
            self._lyr_sock.settimeout(0.5)
            f = conn.makefile("r", encoding="utf-8")
            while True:
                try:
                    line = f.readline()
                except Exception:
                    break
                if not line:
                    break
                if line.strip():
                    self.root.after(0, self._lyr_cmd, line.strip())
            try:
                conn.close()
            except Exception:
                pass
            self._lyr_sock = None

    def _lyr_send(self, msg):
        """推送消息给 Qt 挂件"""
        if self._lyr_sock:
            try:
                self._lyr_sock.sendall((msg + "\n").encode())
            except Exception:
                pass

    def _lyr_cmd(self, line):
        """Qt 挂件发来的命令"""
        parts = line.split("|")
        if parts[0] == "nudge" and len(parts) > 1:
            try:
                self._nudge_lyric(float(parts[1]))
            except Exception:
                pass
        elif parts[0] == "pause":
            self._play_toggle()
        elif parts[0] == "step" and len(parts) > 1:
            try:
                self._step(int(parts[1]))
            except Exception:
                pass
        elif parts[0] == "close-ctrl":
            self._close_lyric()

    def _ensure_lyric_qt(self):
        """确保 Qt 挂件进程在跑"""
        if self._lyr_proc and self._lyr_proc.poll() is None:
            return
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lyric_widget.py")
        try:
            self._lyr_proc = subprocess.Popen(
                [sys.executable, script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            self._lyr_proc = None

    def _push_lyric(self, title):
        """推送歌词给 Qt 挂件（spawn + 等连接）"""
        self._ensure_lyric_qt()

        def send():
            for _ in range(4):
                if self._lyr_sock:
                    break
                time.sleep(0.5)
            self._lyr_send(f"title|{title}")
            self._lyr_send(f"lyric|{json.dumps(self._lyric_lines, ensure_ascii=True)}|{self._lyric_offset}")
        threading.Thread(target=send, daemon=True).start()

    def _build(self):
        pad = {"padx": 12, "pady": 6}
        f = ("Microsoft YaHei", 10)
        fb = ("Microsoft YaHei", 10, "bold")

        # ── 搜索区 ──
        top = tk.Frame(self.root, bg=C["bg"])
        top.pack(fill="x", **pad)

        self.entry = tk.Entry(top, bg=C["card"], fg=C["fg"],
                              insertbackground=C["fg"], font=f, relief="flat",
                              width=38)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.entry.bind("<Return>", lambda e: self._search())

        # 音质选择
        self.qn_var = tk.StringVar(value="192k")
        self.qn_combo = ttk.Combobox(
            top, textvariable=self.qn_var,
            values=["64k", "128k", "192k"],
            state="readonly", width=5, font=("Microsoft YaHei", 9),
        )
        self.qn_combo.pack(side="left", padx=(0, 6))

        self.btn_search = tk.Button(
            top, text="🔍 搜索", font=fb, bg=C["accent"], fg="#fff",
            activebackground=C["accent"], activeforeground="#fff",
            relief="flat", padx=18, pady=4, command=self._search,
        )
        self.btn_search.pack(side="left")

        # 收藏夹（切到收藏模式，与搜索共用列表）
        self.btn_favs = tk.Button(
            top, text="★ 收藏夹", font=f, bg=C["active"], fg=C["fg"],
            activebackground=C["accent"], activeforeground="#fff",
            relief="flat", padx=12, pady=4, command=self._toggle_favs,
        )
        self.btn_favs.pack(side="left", padx=(6, 0))

        # ── 结果列表 ──
        list_frame = tk.Frame(self.root, bg=C["card"], relief="flat")
        list_frame.pack(fill="both", expand=True, padx=12, pady=6)

        self.listbox = tk.Listbox(
            list_frame, bg=C["card"], fg=C["fg"],
            selectbackground=C["active"], selectforeground=C["fg"],
            font=f, relief="flat", activestyle="none",
        )
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.listbox.bind("<Double-Button-1>", self._on_play_from_list)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        # ── 播放区 ──
        player = tk.Frame(self.root, bg=C["card"])
        player.pack(fill="x", padx=12, pady=(6, 12))

        self.info = tk.Label(player, text="未播放", font=fb,
                             fg=C["fg"], bg=C["card"], anchor="w")
        self.info.pack(fill="x", padx=12, pady=(8, 2))

        bar = tk.Frame(player, bg=C["card"])
        bar.pack(fill="x", padx=12, pady=(0, 4))

        self.cur = tk.Label(bar, text="00:00", font=("Consolas", 9),
                            fg=C["muted"], bg=C["card"])
        self.cur.pack(side="left")
        # 可拖拽进度条（Canvas 自绘，松开才 seek）
        self.seek = tk.Canvas(bar, height=20, bg=C["card"], highlightthickness=0)
        self.seek.pack(side="left", fill="x", expand=True, padx=6)
        self.seek.bind("<Button-1>", self._seek_press)
        self.seek.bind("<B1-Motion>", self._seek_drag)
        self.seek.bind("<ButtonRelease-1>", self._seek_release)
        self._seek_drag_ratio = None   # 拖动中的位置 0~1
        self.tot = tk.Label(bar, text="00:00", font=("Consolas", 9),
                            fg=C["muted"], bg=C["card"])
        self.tot.pack(side="right")

        # 音量滑块（mpv 原生 volume，0-100）+ 百分比显示
        vol_row = tk.Frame(player, bg=C["card"])
        vol_row.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(vol_row, text="🔊", font=("Microsoft YaHei", 11),
                 fg=C["muted"], bg=C["card"]).pack(side="left")
        self.vol_slider = tk.Scale(
            vol_row, from_=0, to=100, orient="horizontal",
            showvalue=False, bd=0, highlightthickness=0,
            bg=C["card"], fg=C["fg"], troughcolor="#2a2a4a",
            activebackground=C["accent"],
            command=self._set_volume)
        self.vol_slider.set(self.volume)
        self.vol_slider.pack(side="left", fill="x", expand=True, padx=8)
        self.vol_pct = tk.Label(vol_row, text=f"{self.volume}%",
                              font=("Microsoft YaHei", 9), fg=C["muted"], bg=C["card"])
        self.vol_pct.pack(side="left")

        # ── 控制按钮（统一等宽：播放模式 / 播放暂停 / 收藏） ──
        ctrl = tk.Frame(player, bg=C["card"])
        ctrl.pack(fill="x", padx=12, pady=(4, 12))

        self.btn = {}
        for key, text, cmd in [("mode", "🔁 循环", self._cycle_mode),
                               ("play", "▶ 播放", self._play_toggle),
                               ("fav", "☆ 收藏", self._fav_toggle),
                               ("lyric", "🎤 歌词", self._open_lyric_page)]:
            bg = C["accent"] if key == "play" else C["active"]
            b = tk.Button(ctrl, text=text, font=f, bg=bg, fg="#fff",
                          activebackground=C["accent"], activeforeground="#fff",
                          relief="flat", width=10, pady=5,
                          command=cmd, cursor="hand2")
            b.pack(side="left", expand=True, padx=4)
            self.btn[key] = b

    # ── 事件 ──
    def _set_volume(self, val):
        """音量滑块回调：套用 mpv + 更新显示 + 持久化"""
        try:
            v = int(float(val))
        except ValueError:
            return
        self.volume = v
        self.player.set_volume(v)
        if self.vol_pct is not None:
            self.vol_pct.configure(text=f"{v}%")
        _save_pref({**_load_pref(), "volume": v})

    def _search(self):
        """搜索：切回搜索模式，填充共用列表"""
        kw = self.entry.get().strip()
        if not kw:
            return
        try:
            self.results = _search(kw)
        except Exception as e:
            notify(self.root, f"❌ {e}", "err")
            return
        self._mode = "search"
        self._source = self.results
        self._fill_list()
        if self.results:
            notify(self.root, f"✅ 找到 {len(self.results)} 首")
        else:
            notify(self.root, "⚠️ 无结果", "err")

    def _toggle_favs(self):
        """收藏夹：切到收藏模式（与搜索共用列表）"""
        self._mode = "fav"
        self._source = _load_favs()
        self._fill_list()
        if not self._source:
            notify(self.root, "收藏夹为空", "err")

    def _fill_list(self):
        """按当前模式填充共用列表"""
        self.listbox.delete(0, "end")
        for it in self._source:
            tag = f"[{it.get('typename','')}]" if it.get("typename") else ""
            self.listbox.insert(
                "end",
                f"  [{_fmt_dur(it.get('duration',''))}]{tag}  {it.get('title','')}  —  {it.get('author','')}",
            )
        if self._source:
            self.listbox.selection_set(0)
            self.current = 0
            self._update_fav_btn()
        else:
            self.current = None

    def _on_select(self, _event=None):
        sel = self.listbox.curselection()
        if sel:
            self.current = sel[0]
            self._update_fav_btn()

    def _on_play_from_list(self, _event=None):
        """双击：与播放按钮走同一套状态"""
        self._play()

    def _play(self):
        """播放当前选中（双击/播放按钮共享）"""
        if self.current is None or not self._source:
            notify(self.root, "⚠️ 先选择歌曲", "err")
            return
        self._play_item(self._source[self.current])

    def _play_item(self, item):
        """播放任意条目（搜索结果或历史），只拉流不缓存"""
        self.info.configure(text=f"⏳ 加载... {item['title']}")
        self.root.update_idletasks()

        def work():
            try:
                cid = _get_cid(item["bvid"])
                # 历史条目带固定音质；搜索条目从下拉框读
                if item.get("qn"):
                    selected_qn = item["qn"]
                else:
                    qn_map = {"64k": AUDIO_64K, "128k": AUDIO_128K, "192k": AUDIO_192K}
                    selected_qn = qn_map.get(self.qn_var.get(), PREFERRED_QN)
                url, actual_qn, _mime = _get_audio_url(item["bvid"], cid, prefer_qn=selected_qn)
                qn_label = {AUDIO_192K: "192k", AUDIO_128K: "128k",
                            AUDIO_64K: "64k", AUDIO_FLAC: "FLAC"}.get(actual_qn, f"{actual_qn}")
                self.player.load(url, _parse_dur(item["duration"]))
                self.player.start()
                self.info.configure(text=f"🎵 {item['title']} — {item['author']} [{qn_label}]")
                self.btn["play"].configure(text="⏸ 暂停")
                self._cur_bvid = item.get("bvid")
                self._autoload_lyric(item)   # 若这首已选过歌词，自动加载（不弹窗）
            except Exception as e:
                self.info.configure(text=f"❌ {e}")

        threading.Thread(target=work, daemon=True).start()

    def _resolve_lyric_src(self, item):
        """解析当前歌歌词源（网络阻塞，须在线程里调）→ (lines, title, offset) or None
        设置过 → 用设置源（拉不到歌词则退回第一源）；没设置过 → 搜到的第一个源"""
        bvid = item.get("bvid", "")
        rec = self._lyric_map.get(bvid)
        song, _a = _title_to_song(item.get("title", ""))
        if rec:
            lines = _parse_lrc(_fetch_lyric(rec["id"]))
            if lines:
                return lines, f"{rec['name']} — {rec['artist']}", rec.get("offset", 0.0)
            # 设置源无词/拉取失败 → 退回第一源
        if not song:
            return None
        cands = _lyric_candidates(song)
        if cands:
            return cands[0]["lines"], f"{cands[0]['name']} — {cands[0]['artist']}", 0.0
        return None

    def _open_lyric_page(self):
        """🎤 歌词按钮：打开歌词面板，歌词永远是当前歌的（设置源/第一源）"""
        if self.current is None or not self._source:
            notify(self.root, "⚠️ 先选择歌曲", "err")
            return
        item = self._source[self.current]
        bvid = item.get("bvid")
        # 同一首歌且已有歌词 → 直接开面板
        if self._cur_bvid == bvid and self._lyric_lines:
            rec = self._lyric_map.get(bvid)
            title = f"{rec['name']} — {rec['artist']}" if rec else f"{item['title']} — {item.get('author','')}"
            self._show_lyric_win(title)
            return
        self._cur_bvid = bvid
        song, _a = _title_to_song(item.get("title", ""))
        if not song:
            notify(self.root, "⚠️ 标题无《歌名》，无法搜歌词", "err")
            return
        self.info.configure(text=f"⏳ 搜歌词...《{song}》")

        def work():
            try:
                data = self._resolve_lyric_src(item)
                if data:
                    lines, title, offset = data
                    self.root.after(0, lambda: self._panel_with(lines, title, offset, bvid))
                else:
                    self.root.after(0, lambda: notify(self.root, "⚠️ 没搜到有歌词的版本", "err"))
            except Exception as e:
                self.root.after(0, lambda: notify(self.root, f"❌ {e}", "err"))
        threading.Thread(target=work, daemon=True).start()

    def _panel_with(self, lines, title, offset, bvid=None):
        """填充当前歌词并开面板；bvid 与当前不一致则忽略（用户可能又切歌）"""
        if bvid and bvid != self._cur_bvid:
            return
        self._lyric_lines = lines
        self._lyric_offset = offset
        self._last_lyric = None
        self._show_lyric_win(title)

    def _open_lyric_picker(self):
        """歌词面板内【🔎 搜索歌词】按钮：对当前曲弹候选窗换源"""
        if self.current is None or not self._source:
            notify(self.root, "⚠️ 先选择歌曲", "err")
            return
        item = self._source[self.current]
        song, _artist = _title_to_song(item.get("title", ""))
        if not song:
            notify(self.root, "⚠️ 标题无《歌名》，无法搜歌词", "err")
            return
        self._cur_bvid = item.get("bvid")
        self.info.configure(text=f"⏳ 搜歌词...《{song}》")

        def work():
            try:
                cands = _lyric_candidates(song)
                if cands:
                    self.root.after(0, lambda: self._show_lyric_picker(song, cands, item.get("bvid")))
                else:
                    self.root.after(0, lambda: notify(self.root, "⚠️ 没搜到有歌词的版本", "err"))
            except Exception as e:
                self.root.after(0, lambda: notify(self.root, f"❌ {e}", "err"))
        threading.Thread(target=work, daemon=True).start()

    def _show_lyric_picker(self, song, cands, bvid=None):
        """歌词候选窗：列表供选择，双击/回车选定"""
        if self._pick_win:
            try:
                self._pick_win.destroy()
            except Exception:
                pass
        win = tk.Toplevel(self.root)
        win.title(f"🎤 歌词候选《{song}》")
        win.configure(bg=C["card"])
        win.attributes("-topmost", True)
        win.geometry("560x460")
        saved = self._lyric_map.get(bvid or "")
        head = f"《{song}》 选择歌词版本：" + (f"（已存：{saved['name']}）" if saved else "")
        tk.Label(win, text=head, font=("Microsoft YaHei", 10, "bold"),
                 bg=C["card"], fg=C["fg"]).pack(anchor="w", padx=12, pady=(10, 2))
        lb = tk.Listbox(win, bg=C["card"], fg=C["fg"], selectbackground=C["accent"],
                        selectforeground="#fff", font=("Microsoft YaHei", 9), relief="flat")
        for c in cands:
            lb.insert("end", f"{c['name']} — {c['artist']}  ({c['dur']//60}:{c['dur']%60:02d})")
            if saved and c["id"] == saved.get("id"):
                lb.selection_set(lb.size() - 1)
        if not lb.curselection() and cands:
            lb.selection_set(0)
        lb.pack(fill="both", expand=True, padx=12, pady=4)
        def pick():
            sel = lb.curselection()
            if sel:
                self._apply_lyric(cands[sel[0]], bvid, show_win=self._lyric_on.get())
                win.destroy()
        lb.bind("<Double-Button-1>", lambda e: pick())
        row = tk.Frame(win, bg=C["card"])
        row.pack(fill="x", padx=12, pady=(0, 8))
        def keep_on():
            self._lyric_map["_on"] = self._lyric_on.get()   # 开关持久化
            _save_lyric_map(self._lyric_map)
        tk.Checkbutton(row, text="播放时自动桌面歌词", variable=self._lyric_on,
                       bg=C["card"], fg=C["fg"], selectcolor=C["card"],
                       activebackground=C["card"], activeforeground=C["fg"],
                       font=("Microsoft YaHei", 9), command=keep_on).pack(side="left")
        tk.Button(win, text="选定歌词", font=("Microsoft YaHei", 9),
                  bg=C["accent"], fg="#fff", relief="flat", command=pick).pack()
        self._pick_win = win

    def _apply_lyric(self, cand, bvid=None, show_win=True, persist=True):
        """应用歌词源；persist=False 表示默认第一源（不写设置，播放时总默认第一源）"""
        self._lyric_lines = cand["lines"]
        self._lyric_offset = 0.0
        self._last_lyric = None
        if show_win:
            self._show_lyric_win(f"{cand['name']} — {cand['artist']}")
        elif self._lyric_on.get():
            self._push_lyric(f"{cand['name']} — {cand['artist']}")
        if bvid and persist:
            self._lyric_map[bvid] = {
                "id": cand["id"], "name": cand["name"],
                "artist": cand["artist"], "dur": cand["dur"], "offset": 0.0,
            }
            _save_lyric_map(self._lyric_map)
            notify(self.root, f"💾 已记住《{cand['name']}》歌词，播放时自动桌面歌词")

    def _autoload_lyric(self, item):
        """播放时：开关开着 → 设置源/第一源，出桌面挂件（不弹面板）"""
        if not self._lyric_on.get():
            return
        bvid = item.get("bvid", "")

        def work():
            try:
                data = self._resolve_lyric_src(item)
                if data:
                    lines, title, offset = data
                    self.root.after(0, lambda: self._autoload_apply(title, lines, offset, bvid))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _autoload_apply(self, title, lines, offset, bvid=None):
        """播放中应用歌词：只推桌面挂件（不弹页面）"""
        if bvid and bvid != self._cur_bvid:
            return
        self._lyric_lines = lines
        self._lyric_offset = offset
        self._last_lyric = None
        self._push_lyric(title)
        song_name = title.split("—")[0].strip() if title else ""
        notify(self.root, f"🎤 桌面歌词《{song_name}》")

    def _show_lyric_win(self, title):
        """歌词面板（手动打开不受自动开关限制，开关只管播放时自动出挂件）"""
        self._close_lyric_win_only()
        win = tk.Toplevel(self.root)
        win.title("🎤 歌词")
        win.configure(bg=C["card"])
        win.geometry("660x600")
        win.transient(self.root)

        tk.Label(win, text=title, font=("Microsoft YaHei", 13, "bold"),
                 bg=C["card"], fg=C["fg"]).pack(pady=(14, 2))

        # KTV 歌词区：Canvas 绘制，当前句居中流动
        canvas = tk.Canvas(win, bg=C["card"], highlightthickness=0)
        canvas.pack(fill="both", expand=True, padx=24, pady=8)
        self._lyric_canvas = canvas
        self._last_lyric_idx = None

        # 控制条：播放暂停 / 搜索歌词 / 自动桌面歌词开关 / 微调 / 关闭
        bar = tk.Frame(win, bg=C["card"])
        bar.pack(pady=(0, 14))
        self._lyric_play_btn = tk.Button(
            bar, text="⏸", font=("Microsoft YaHei", 11), bg=C["accent"], fg="#fff",
            relief="flat", activebackground=C["accent"], activeforeground="#fff",
            command=self._play_toggle)
        self._lyric_play_btn.pack(side="left", padx=5)
        tk.Button(bar, text="🔎 搜索歌词", font=("Microsoft YaHei", 9), bg=C["active"], fg=C["fg"],
                  relief="flat", activebackground=C["accent"], activeforeground="#fff",
                  command=self._open_lyric_picker).pack(side="left", padx=5)
        tk.Checkbutton(bar, text="自动桌面歌词", variable=self._lyric_on,
                       bg=C["card"], fg=C["fg"], selectcolor=C["card"],
                       activebackground=C["card"], activeforeground=C["fg"],
                       font=("Microsoft YaHei", 9), command=self._save_lyric_on).pack(side="left", padx=5)
        for text, cmd in (("−0.5s", lambda: self._nudge_lyric(-0.5)),
                          ("+0.5s", lambda: self._nudge_lyric(0.5))):
            tk.Button(bar, text=text, font=("Microsoft YaHei", 10), bg=C["active"], fg=C["fg"],
                      relief="flat", activebackground=C["accent"], activeforeground="#fff",
                      repeatdelay=300, repeatinterval=120, command=cmd).pack(side="left", padx=5)
        tk.Button(bar, text="×", font=("Microsoft YaHei", 11, "bold"), bg=C["active"], fg=C["fg"],
                  relief="flat", activebackground="#8a1f1f", activeforeground="#fff",
                  command=self._close_lyric).pack(side="left", padx=8)
        self._lyric_win = win
        win.lift()   # 确保浮到前台（KDE transient 有时需要）
        # 打开即同步当前句
        self.root.after(1, lambda: self._update_lyric(getattr(self, "_last_pos", 0)))
        # 同时把歌词推到桌面透明挂件
        self._push_lyric(title)

    def _save_lyric_on(self):
        """自动桌面歌词开关 → 持久化"""
        self._lyric_map["_on"] = self._lyric_on.get()
        _save_lyric_map(self._lyric_map)

    def _close_lyric_win_only(self):
        """只关歌词页窗口，不清数据"""
        if self._lyric_win:
            try:
                self._lyric_win.destroy()
            except Exception:
                pass
            self._lyric_win = None

    def _nudge_lyric(self, delta):
        """手动微调歌词偏移（即时刷新 + 持久化 + 同步挂件）"""
        self._lyric_offset += delta
        self._last_lyric = None
        self._update_lyric(getattr(self, "_last_pos", 0))
        self._lyr_send(f"lyric|{json.dumps(self._lyric_lines, ensure_ascii=True)}|{self._lyric_offset}")
        if self._cur_bvid and self._cur_bvid in self._lyric_map:
            self._lyric_map[self._cur_bvid]["offset"] = self._lyric_offset
            _save_lyric_map(self._lyric_map)

    def _close_lyric(self):
        self._lyric_lines = []
        self._lyr_send("close")
        self._close_lyric_win_only()

    def _update_lyric(self, pos):
        """歌词页 Canvas 流动渲染 + 无条件推送位置给桌面挂件"""
        canvas = getattr(self, "_lyric_canvas", None)
        if self._lyric_lines and canvas:
            t = pos + self._lyric_offset
            idx = -1
            for k, (s, _t) in enumerate(self._lyric_lines):
                if s <= t:
                    idx = k
                else:
                    break
            if idx != self._last_lyric_idx:
                self._last_lyric_idx = idx
                canvas.delete("lyr")
                cx = canvas.winfo_width() / 2
                cy = canvas.winfo_height() / 2
                rows = [(-2, 11, "#5a5a6a"), (-1, 13, "#8a8a9a"),
                        (0, 25, "#ffffff"),
                        (1, 13, "#8a8a9a"), (2, 11, "#5a5a6a")]
                for off, size, color in rows:
                    k = idx + off
                    if 0 <= k < len(self._lyric_lines):
                        txt = self._lyric_lines[k][1]
                        font = ("Microsoft YaHei", size, "bold") if off == 0 else ("Microsoft YaHei", size)
                        if off == 0:
                            canvas.create_text(cx + 2, cy + off * 36 + 2, text=txt,
                                               font=("Microsoft YaHei", size, "bold"),
                                               fill="#1a1a2a", tags="lyr")
                        canvas.create_text(cx, cy + off * 36, text=txt, font=font,
                                           fill=color, tags="lyr")
        # 桌面挂件位置推送（无论是否开歌词页，都保证挂件滚动）
        self._lyr_send(f"pos|{pos}|{getattr(self.player, 'duration', 0)}|"
                       + ("pause" if getattr(self.player, "paused", False) else "play"))

    def _play_toggle(self):
        """播放/暂停切换（主界面按钮 + 歌词页按钮同步）"""
        if self.player.is_playing():
            self.player.toggle()
            if self.player.paused:
                self.btn["play"].configure(text="▶ 继续")
                if getattr(self, "_lyric_play_btn", None):
                    self._lyric_play_btn.configure(text="▶")
            else:
                self.btn["play"].configure(text="⏸ 暂停")
                if getattr(self, "_lyric_play_btn", None):
                    self._lyric_play_btn.configure(text="⏸")
        else:
            self._play()

    def _step(self, delta):
        """上下首（悬浮窗 ⏮/⏭）"""
        if self.current is None or not self._source:
            return
        n = len(self._source)
        self.current = (self.current + delta) % n
        self._play_item(self._source[self.current])

    def _cycle_mode(self):
        """播放模式循环：顺序 → 随机 → 单曲"""
        modes = [("order", "🔁 循环"), ("random", "🔀 随机"), ("single", "🔂 单曲")]
        names = [m[0] for m in modes]
        idx = names.index(self._play_mode)
        self._play_mode = modes[(idx + 1) % len(modes)][0]
        self.btn["mode"].configure(text=modes[(idx + 1) % len(modes)][1])
        notify(self.root, f"播放模式: {modes[(idx + 1) % len(modes)][1]}")

    def _fav_toggle(self):
        """收藏/取消收藏当前歌曲（共享状态）"""
        if self.current is None or not self._source:
            notify(self.root, "⚠️ 先选择歌曲", "err")
            return
        item = self._source[self.current]
        bvid = item["bvid"]
        favs = _load_favs()
        already = any(f.get("bvid") == bvid for f in favs)

        if self._mode == "fav":
            # 收藏夹模式：删除当前收藏
            _remove_fav(bvid)
            self._source = _load_favs()
            self._fill_list()
            notify(self.root, "已取消收藏")
        elif already:
            notify(self.root, "★ 已在收藏夹")
        else:
            qn_map = {"64k": AUDIO_64K, "128k": AUDIO_128K, "192k": AUDIO_192K}
            qn = qn_map.get(self.qn_var.get(), PREFERRED_QN)
            _save_fav({
                "bvid": bvid,
                "title": item["title"],
                "author": item.get("author", ""),
                "duration": item.get("duration", ""),
                "qn": qn,
            })
            self._update_fav_btn()
            notify(self.root, f"★ 已收藏: {item['title']}")

    def _update_fav_btn(self):
        """根据当前歌曲收藏状态刷新收藏按钮"""
        if self.current is None or not self._source:
            return
        item = self._source[self.current]
        favs = _load_favs()
        if any(f.get("bvid") == item.get("bvid") for f in favs):
            self.btn["fav"].configure(text="★ 已收藏", bg=C["ok"])
        else:
            self.btn["fav"].configure(text="☆ 收藏", bg=C["active"])

    def _on_tick(self, pos, dur):
        if dur <= 0:
            return
        self._last_pos = pos
        self.cur.configure(text=_fmt(pos))
        self.tot.configure(text=_fmt(dur))
        # 拖动中不覆盖进度条显示
        if self._seek_drag_ratio is None:
            self._draw_seek(min(1.0, pos / dur))
        self._update_lyric(pos)

    def _on_end(self):
        """一曲结束：按播放模式切换下一曲"""
        # 播放已结束，按钮复位
        self.btn["play"].configure(text="▶ 播放")
        n = len(self._source)
        if n <= 0:
            return
        if self._play_mode == "single":
            self._play()  # 单曲循环：重播当前
        elif self._play_mode == "random":
            import random
            self.current = random.randrange(n)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(self.current)
            self._play()
        else:  # order：顺序循环，末尾回到开头
            self.current = (self.current + 1) % n
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(self.current)
            self._play()

    # ── 可拖拽进度条（seek） ──
    def _draw_seek(self, ratio):
        """绘制进度条：轨道 + 已播放 + 进度点"""
        c = self.seek
        w = c.winfo_width()
        if w < 20:
            return
        h = 20
        c.delete("all")
        ratio = max(0.0, min(1.0, ratio))
        mid = h // 2
        # 轨道
        c.create_rectangle(2, mid - 2, w - 2, mid + 2, fill=C["active"], outline="")
        # 已播放
        if ratio > 0:
            c.create_rectangle(2, mid - 2, 2 + (w - 4) * ratio, mid + 2,
                               fill=C["accent"], outline="")
        # 进度点
        px = 2 + (w - 4) * ratio
        c.create_oval(px - 5, mid - 5, px + 5, mid + 5,
                      fill="#ffffff", outline=C["accent"])

    def _seek_ratio(self, event):
        """鼠标 x → 播放比例 0~1"""
        w = self.seek.winfo_width()
        if w < 10:
            return 0.0
        return max(0.0, min(1.0, (event.x - 2) / (w - 4)))

    def _seek_press(self, event):
        """按下：开始拖动，仅更新显示"""
        if not self.player.url or self.player.duration <= 0:
            return
        self._seek_drag_ratio = self._seek_ratio(event)
        self._draw_seek(self._seek_drag_ratio)
        self.cur.configure(text=_fmt(self._seek_drag_ratio * self.player.duration))

    def _seek_drag(self, event):
        """拖动：实时更新显示"""
        if self._seek_drag_ratio is None:
            return
        self._seek_drag_ratio = self._seek_ratio(event)
        self._draw_seek(self._seek_drag_ratio)
        self.cur.configure(text=_fmt(self._seek_drag_ratio * self.player.duration))

    def _seek_release(self, event):
        """松开：真正 seek（mpv 原生跳转）"""
        if self._seek_drag_ratio is None:
            return
        ratio = self._seek_ratio(event)
        self._seek_drag_ratio = None
        if not self.player.url or self.player.duration <= 0:
            return
        target = ratio * self.player.duration
        self._do_seek(target)

    def _do_seek(self, target):
        """跳转：mpv 原生 seek，不重启进程"""
        self.player.seek(target)
        if self.player.paused:
            self.player.pause()

    def _poll_tick(self):
        self.player.tick()
        self.root.after(250, self._poll_tick)


def _fmt_dur(s):
    """B站 时长串 → 规整 'm:ss'（'1:9'→'1:09'）；解析失败原样返回"""
    sec = _parse_dur(s)
    if sec < 0:
        return s
    return f"{sec // 60}:{sec % 60:02d}"


def _title_to_song(title):
    """B站标题 → (歌名, 歌手提示)；剥 []【】后取《歌名》，取不到返回 None"""
    clean = re.sub(r"[\[【].*?[\]】]", "", title or "")
    m = re.search(r"《([^》]+)》", clean)
    if m:
        return m.group(1).strip(), None
    return None, None


def _fetch_lyric(sid):
    """网易云歌词 LRC 文本（sid=歌曲id）；无词返回空串"""
    try:
        r = _req("https://music.163.com/api/song/lyric",
                 params={"id": sid, "lv": 1, "kv": 1, "tv": -1})
        j = r.json()
        if j.get("code") == 200:
            return j.get("lrc", {}).get("lyric", "") or ""
    except Exception:
        pass
    return ""


def _parse_lrc(text):
    """LRC 文本 → [(秒, 歌词行)]，按时间升序"""
    out = []
    for ln in text.splitlines():
        m = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\]", ln.strip())
        if m:
            t = int(m.group(1)) * 60 + float(m.group(2))
            txt = ln.strip()[m.end():].strip()
            if txt and not txt.startswith(("作词", "作曲", "编曲", "制作", "录音", "混音", "监制", "OP:", "SP:", "和声", "配唱")):
                out.append((t, txt))
    out.sort()
    return out


def _lyric_candidates(song, limit=8):
    """搜歌名 → 候选列表（只留有歌词的），[(id,name,artist,dur,lines)]"""
    try:
        r = _req("https://music.163.com/api/search/get/web",
                 params={"s": song, "type": 1, "limit": limit, "offset": 0})
        songs = r.json().get("result", {}).get("songs", []) or []
    except Exception:
        return []
    out = []
    for c in songs:
        sid = c.get("id")
        if not sid:
            continue
        lines = _parse_lrc(_fetch_lyric(sid))
        if len(lines) >= 3:
            out.append({
                "id": sid,
                "name": c.get("name", ""),
                "artist": c["artists"][0]["name"] if c.get("artists") else "",
                "dur": c.get("duration", 0) // 1000,
                "lines": lines,
            })
    return out


def _fmt(sec):
    try:
        sec = int(sec)
    except Exception:
        return "00:00"
    return f"{sec // 60:02d}:{sec % 60:02d}"


if __name__ == "__main__":
    r = tk.Tk()
    MusicApp(r)
    r.mainloop()
