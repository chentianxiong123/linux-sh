#!/usr/bin/env python3
"""music.py — B站音乐搜索播放

架构：
  Tkinter GUI  →  B站 API (search / view / playurl)
               →  ffmpeg -vn -f wav pipe  →  paplay
               →  subprocess SIGSTOP/SIGCONT 暂停/继续

依赖：requests, ffmpeg, paplay (已在系统)
"""

import os
import re
import signal
import subprocess
import threading
import time
import uuid
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import requests

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
        self.started_at = 0.0
        self.paused = False
        self._tick_cb = on_tick
        self._end_cb = on_end
        self.lock = threading.Lock()

    def load(self, url, duration):
        self.stop()
        self.url = url
        self.duration = duration
        self.paused = False
        self.started_at = 0.0

    def start(self):
        with self.lock:
            if not self.url or self.ffmpeg is not None:
                return
            # ffmpeg 拉流 → 解码 → 转 WAV PCM → 管道给 paplay
            # ffmpeg 拉纯音频 M4A → 解码为 PCM WAV → 管道给 paplay
            # 不需要 -vn/-sn/-dn：dash.audio[] 返回的就是纯音频流
            self.ffmpeg = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error", "-re",
                 "-user_agent",
                 BUILTIN_HEADERS["User-Agent"],
                 "-headers",
                 f"Referer: {BUILTIN_HEADERS['Referer']}\n"
                 f"Origin: {BUILTIN_HEADERS['Origin']}\n",
                 "-i", self.url,
                 "-ac", "2", "-ar", "44100",
                 "-c:a", "pcm_s16le",
                 "-f", "wav", "pipe:1"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            self.paplay = subprocess.Popen(
                ["paplay"], stdin=self.ffmpeg.stdout,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.ffmpeg.stdout = None
            self.started_at = time.time()

    def pause(self):
        with self.lock:
            if not self.ffmpeg or self.paused:
                return
            for p in (self.ffmpeg, self.paplay):
                if p.poll() is None:
                    os.kill(p.pid, signal.SIGSTOP)
            self.paused = True

    def resume(self):
        with self.lock:
            if not self.ffmpeg or not self.paused:
                return
            for p in (self.ffmpeg, self.paplay):
                if p.poll() is None:
                    os.kill(p.pid, signal.SIGCONT)
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
        pos = time.time() - self.started_at
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
        self.root.geometry("720x620")
        self.root.configure(bg=C["bg"])
        self.root.wm_protocol("WM_DELETE_WINDOW", self._on_close)

        self.results = []
        self.current = None
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
        self.bar = ttk.Progressbar(bar, orient="horizontal", mode="determinate",
                                    maximum=1000)
        self.bar.pack(side="left", fill="x", expand=True, padx=6)
        self.tot = tk.Label(bar, text="00:00", font=("Consolas", 9),
                            fg=C["muted"], bg=C["card"])
        self.tot.pack(side="right")

        self.vol = tk.Label(player, text="🔊 音量 100%", font=("Microsoft YaHei", 9),
                            fg=C["muted"], bg=C["card"])
        self.vol.pack(fill="x", padx=12, pady=(0, 8))

        # ── 控制按钮 ──
        ctrl = tk.Frame(player, bg=C["card"])
        ctrl.pack(fill="x", padx=12, pady=(4, 12))

        self.btn = {}
        for text, cmd, w in [("⏮ prev", self._prev, 6),
                              ("▶ play", self._play, 8),
                              ("⏸ pause", self._pause, 8),
                              ("⏹ stop", self._stop, 6),
                              ("next ⏭", self._next, 6)]:
            b = tk.Button(ctrl, text=text, font=f, bg=C["active"], fg=C["fg"],
                          activebackground=C["accent"], activeforeground="#fff",
                          relief="flat", padx=12, pady=5, width=w,
                          command=cmd)
            b.pack(side="left", expand=True, padx=3)
            self.btn[cmd.__name__.lstrip("_")] = b

    # ── 事件 ──
    def _search(self):
        kw = self.entry.get().strip()
        if not kw:
            return
        try:
            self.results = _search(kw)
        except Exception as e:
            notify(self.root, f"❌ {e}", "err")
            return
        self.listbox.delete(0, "end")
        for r in self.results:
            self.listbox.insert("end",
                                f"  [{r['duration']}]  {r['title']}  —  {r['author']}")
        if self.results:
            self.listbox.selection_set(0)
            notify(self.root, f"✅ 找到 {len(self.results)} 首")
        else:
            notify(self.root, "⚠️ 无结果", "err")

    def _on_select(self, _event=None):
        sel = self.listbox.curselection()
        if sel:
            self.current = sel[0]

    def _on_play_from_list(self, _event=None):
        if self.current is not None:
            self._play()

    def _play(self):
        if self.current is None:
            notify(self.root, "⚠️ 先选择歌曲", "err")
            return
        item = self.results[self.current]
        self.info.configure(text=f"⏳ 加载... {item['title']}")
        self.root.update_idletasks()

        def work():
            try:
                cid = _get_cid(item["bvid"])
                # 从下拉框读音质
                qn_map = {"64k": AUDIO_64K, "128k": AUDIO_128K, "192k": AUDIO_192K}
                selected_qn = qn_map.get(self.qn_var.get(), PREFERRED_QN)
                url, actual_qn, _mime = _get_audio_url(item["bvid"], cid, prefer_qn=selected_qn)
                qn_label = {AUDIO_192K: "192k", AUDIO_128K: "128k",
                            AUDIO_64K: "64k", AUDIO_FLAC: "FLAC"}.get(actual_qn, f"{actual_qn}")
                self.player.load(url, _parse_dur(item["duration"]))
                self.player.start()
                self.info.configure(text=f"🎵 {item['title']} — {item['author']} [{qn_label}]")
            except Exception as e:
                self.info.configure(text=f"❌ {e}")

        threading.Thread(target=work, daemon=True).start()

    def _pause(self):
        if self.player.url:
            self.player.toggle()

    def _stop(self):
        self.player.stop()
        self.cur.configure(text="00:00")
        self.bar["value"] = 0
        self.info.configure(text="已停止")

    def _prev(self):
        if self.current is not None and self.current > 0:
            self.current -= 1
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(self.current)
            self._play()

    def _next(self):
        if self.current is not None and self.current < len(self.results) - 1:
            self.current += 1
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(self.current)
            self._play()

    def _on_tick(self, pos, dur):
        if dur <= 0:
            return
        self.cur.configure(text=_fmt(pos))
        self.tot.configure(text=_fmt(dur))
        self.bar["value"] = min(1000, int(pos / dur * 1000))

    def _on_end(self):
        self._next()

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
