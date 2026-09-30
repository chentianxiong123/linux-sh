#!/usr/bin/env python3
"""music.py — B站音乐搜索播放

架构：
  Tkinter GUI  →  B站 API (search / view / playurl)
               →  ffmpeg -vn -f wav pipe  →  paplay
               →  subprocess SIGSTOP/SIGCONT 暂停/继续

依赖：requests, ffmpeg, paplay (已在系统)
"""

import os
import json
import re
import signal
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


def _search(keyword):
    r = _req(API["search"], params={
        "keyword": keyword,
        "search_type": "video",
        "page": 1,
        "pagesize": 30,
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
        })
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


# ── 播放器（ffmpeg → paplay） ───────────────────────────
class Player:
    def __init__(self, on_tick=None, on_end=None):
        self.ffmpeg = None
        self.paplay = None
        self.url = ""
        self.duration = 0
        self.offset = 0.0          # seek 起点（秒）
        self.started_at = 0.0
        self.paused = False
        self._pause_started = 0.0  # 本次暂停开始的时间（计时补偿用）
        self._tick_cb = on_tick
        self._end_cb = on_end
        self.lock = threading.Lock()

    def load(self, url, duration, offset=0.0):
        self.stop()
        self.url = url
        self.duration = duration
        self.offset = offset
        self.paused = False
        self.started_at = 0.0

    def start(self):
        with self.lock:
            if not self.url or self.ffmpeg is not None:
                return
            # ffmpeg 拉流 → 解码 → 转 WAV PCM → 管道给 paplay
            # 注意：不能用 -re！它按系统时钟节流，SIGSTOP 暂停后时钟跳变
            #       导致恢复播放时 ffmpeg 判定输入过期直接退出（僵尸进程）
            # 无 -re：靠管道缓冲阻塞自然限速，SIGSTOP/SIGCONT 稳定工作
            # -ss 放 -i 前：输入快速 seek，不重新下载整个流
            cmd = ["ffmpeg", "-loglevel", "error",
                   "-user_agent", BUILTIN_HEADERS["User-Agent"],
                   "-headers",
                   f"Referer: {BUILTIN_HEADERS['Referer']}\n"
                   f"Origin: {BUILTIN_HEADERS['Origin']}\n"]
            if self.offset > 0:
                cmd += ["-ss", str(self.offset)]
            cmd += ["-i", self.url,
                    "-ac", "2", "-ar", "44100",
                    "-c:a", "pcm_s16le",
                    "-f", "wav", "pipe:1"]
            self.ffmpeg = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            self.paplay = subprocess.Popen(
                ["paplay"], stdin=self.ffmpeg.stdout,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.ffmpeg.stdout = None
            self.started_at = time.time()

    def is_playing(self):
        """进程真的在跑才是播放中（ffmpeg 退出后属性非 None 但 poll 非 None）"""
        return self.ffmpeg is not None and self.ffmpeg.poll() is None

    def pause(self):
        with self.lock:
            if not self.ffmpeg or self.paused:
                return
            for p in (self.ffmpeg, self.paplay):
                if p.poll() is None:
                    os.kill(p.pid, signal.SIGSTOP)
            self.paused = True
            self._pause_started = time.time()

    def resume(self):
        with self.lock:
            if not self.ffmpeg or not self.paused:
                return
            for p in (self.ffmpeg, self.paplay):
                if p.poll() is None:
                    os.kill(p.pid, signal.SIGCONT)
            # 补偿暂停时长：started_at 前移，进度不跳变
            self.started_at += time.time() - self._pause_started
            self.paused = False

    def toggle(self):
        if self.paused:
            self.resume()
        else:
            self.pause()

    def stop(self):
        with self.lock:
            for p in (self.paplay, self.ffmpeg):
                if p and p.poll() is None:
                    try:
                        # 先恢复（如果之前被 SIGSTOP 暂停过）
                        os.kill(p.pid, signal.SIGCONT)
                        # 再杀掉
                        os.kill(p.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            self.ffmpeg = None
            self.paplay = None
            self.paused = False
            self.started_at = 0.0

    def tick(self):
        if not self.ffmpeg or self.ffmpeg.poll() is not None:
            if self.url and self._end_cb:
                self._end_cb()
            return
        if self.paused:
            return
        pos = self.offset + (time.time() - self.started_at)
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
        self.player = Player(on_tick=self._on_tick, on_end=self._on_end)
        self._build()
        self._poll_tick()

    def _on_close(self):
        """窗口关闭时清理所有进程"""
        self.player.stop()
        self.root.destroy()

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

        self.vol = tk.Label(player, text="🔊 音量 100%", font=("Microsoft YaHei", 9),
                            fg=C["muted"], bg=C["card"])
        self.vol.pack(fill="x", padx=12, pady=(0, 8))

        # ── 控制按钮（统一等宽：播放模式 / 播放暂停 / 收藏） ──
        ctrl = tk.Frame(player, bg=C["card"])
        ctrl.pack(fill="x", padx=12, pady=(4, 12))

        self.btn = {}
        for key, text, cmd in [("mode", "🔁 循环", self._cycle_mode),
                               ("play", "▶ 播放", self._play_toggle),
                               ("fav", "☆ 收藏", self._fav_toggle)]:
            bg = C["accent"] if key == "play" else C["active"]
            b = tk.Button(ctrl, text=text, font=f, bg=bg, fg="#fff",
                          activebackground=C["accent"], activeforeground="#fff",
                          relief="flat", width=10, pady=5,
                          command=cmd, cursor="hand2")
            b.pack(side="left", expand=True, padx=4)
            self.btn[key] = b

    # ── 事件 ──
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
            self.listbox.insert(
                "end",
                f"  [{it.get('duration','')}]  {it.get('title','')}  —  {it.get('author','')}",
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
            except Exception as e:
                self.info.configure(text=f"❌ {e}")

        threading.Thread(target=work, daemon=True).start()

    def _play_toggle(self):
        """播放/暂停切换（一个按钮）"""
        if self.player.is_playing():
            self.player.toggle()
            if self.player.paused:
                self.btn["play"].configure(text="▶ 继续")
            else:
                self.btn["play"].configure(text="⏸ 暂停")
        else:
            self._play()

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
        self.cur.configure(text=_fmt(pos))
        self.tot.configure(text=_fmt(dur))
        # 拖动中不覆盖进度条显示
        if self._seek_drag_ratio is None:
            self._draw_seek(min(1.0, pos / dur))

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
        """松开：真正 seek（重启 ffmpeg 从目标位置拉流）"""
        if self._seek_drag_ratio is None:
            return
        ratio = self._seek_ratio(event)
        self._seek_drag_ratio = None
        if not self.player.url or self.player.duration <= 0:
            return
        target = ratio * self.player.duration
        self._do_seek(target)

    def _do_seek(self, target):
        """从 target 秒重新开始播放"""
        url, dur = self.player.url, self.player.duration
        was_paused = self.player.paused
        self.player.stop()
        self.player.load(url, dur, offset=target)
        self.player.start()
        if was_paused:
            self.player.pause()

    def _poll_tick(self):
        self.player.tick()
        self.root.after(250, self._poll_tick)


def _parse_dur(s):
    try:
        m, sec = s.split(":")
        return int(m) * 60 + int(sec)
    except Exception:
        return 0


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
