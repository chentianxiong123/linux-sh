#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk
import threading
import subprocess
import time

class AutoClicker:
    def __init__(self):
        self.running = False
        self.thread = None

        self.win = tk.Tk()
        self.win.title("连点器")
        self.win.geometry("320x280")
        self.win.resizable(False, False)

        f = ttk.Frame(self.win, padding=15)
        f.pack(fill="both", expand=True)

        # 间隔
        ttk.Label(f, text="点击间隔 (毫秒):").grid(row=0, column=0, sticky="w", pady=3)
        self.delay_var = tk.IntVar(value=100)
        ttk.Spinbox(f, from_=10, to=10000, textvariable=self.delay_var, width=8).grid(row=0, column=1, sticky="w", padx=8)

        # 点击次数（0=无限）
        ttk.Label(f, text="点击次数 (0=无限):").grid(row=1, column=0, sticky="w", pady=3)
        self.count_var = tk.IntVar(value=0)
        ttk.Spinbox(f, from_=0, to=99999, textvariable=self.count_var, width=8).grid(row=1, column=1, sticky="w", padx=8)

        # 按键类型
        ttk.Label(f, text="按键:").grid(row=2, column=0, sticky="w", pady=3)
        self.button_var = tk.StringVar(value="左键")
        ttk.Combobox(f, textvariable=self.button_var, values=["左键", "中键", "右键"], width=8, state="readonly").grid(row=2, column=1, sticky="w", padx=8)

        # 快捷键提示
        ttk.Label(f, text="快捷键: F6 开始/停止", foreground="gray").grid(row=3, column=0, columnspan=2, pady=5)

        # 状态
        self.status_var = tk.StringVar(value="已停止")
        ttk.Label(f, textvariable=self.status_var, foreground="gray").grid(row=4, column=0, columnspan=2, pady=3)

        # 按钮
        btn_frame = ttk.Frame(f)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=10)

        self.start_btn = ttk.Button(btn_frame, text="▶ 开始", width=10, command=self.toggle)
        self.start_btn.pack(side="left", padx=5)

        ttk.Button(btn_frame, text="✕ 退出", width=8, command=self.quit).pack(side="left", padx=5)

        # 热键绑定
        self.win.bind("<F6>", lambda e: self.toggle())

        self.win.protocol("WM_DELETE_WINDOW", self.quit)

    def button_code(self):
        return {"左键": "1", "中键": "2", "右键": "3"}[self.button_var.get()]

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        self.running = True
        self.start_btn.configure(text="■ 停止")
        self.status_var.set("运行中...")
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.start_btn.configure(text="▶ 开始")
        self.status_var.set("已停止")

    def _loop(self):
        b = self.button_code()
        delay = self.delay_var.get() / 1000.0
        max_count = self.count_var.get()
        count = 0

        while self.running:
            subprocess.run(["xdotool", "click", b], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            count += 1
            if max_count > 0 and count >= max_count:
                self.win.after(0, self.stop)
                break
            time.sleep(delay)

    def quit(self):
        self.running = False
        self.win.destroy()

    def run(self):
        self.win.mainloop()

if __name__ == "__main__":
    AutoClicker().run()