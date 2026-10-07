"""Desktop control panel for the Codex compaction proxy."""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import requests


ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "settings.json"
PYTHON = Path(sys.executable)
PYTHONW = PYTHON.with_name("pythonw.exe")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | CREATE_NO_WINDOW


class ControlPanel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Codex 压缩缓存修复")
        self.geometry("860x690")
        self.minsize(760, 620)
        self.configure(background="#f3f5f7")
        self.results = queue.Queue()
        self.busy = False
        self._style()
        self._build()
        self.refresh()
        self.after(150, self._drain)
        self.after(2500, self._tick)

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("vista" if "vista" in style.theme_names() else "clam")
        style.configure("TFrame", background="#f3f5f7")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f3f5f7", foreground="#263238", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"), foreground="#162a36")
        style.configure("Section.TLabel", background="#ffffff", font=("Segoe UI", 11, "bold"), foreground="#162a36")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#5e6c73", wraplength=720)
        style.configure("Action.TButton", padding=(12, 8), font=("Segoe UI", 10))
        style.configure("TRadiobutton", background="#ffffff", font=("Segoe UI", 10))

    def _build(self):
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Codex 压缩缓存修复", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="代理状态、供应商同步与启动设置", padding=(0, 3, 0, 14)).pack(anchor="w")

        top = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        top.pack(fill="x", pady=(0, 12))
        ttk.Label(top, text="运行状态", style="Section.TLabel").pack(anchor="w")
        self.status_var = tk.StringVar(value="正在检查…")
        ttk.Label(top, textvariable=self.status_var, style="Muted.TLabel", padding=(0, 7, 0, 10)).pack(anchor="w")
        actions = ttk.Frame(top, style="Panel.TFrame")
        actions.pack(fill="x")
        self._button(actions, "启动修复", self.start_proxy).pack(side="left", padx=(0, 8))
        self._button(actions, "恢复直连", self.restore_route).pack(side="left", padx=(0, 8))
        self._button(actions, "停止代理", self.stop_proxy).pack(side="left", padx=(0, 8))
        self._button(actions, "刷新状态", self.refresh).pack(side="left")

        sync = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        sync.pack(fill="x", pady=(0, 12))
        ttk.Label(sync, text="供应商切换", style="Section.TLabel").pack(anchor="w")
        ttk.Label(sync, text="选择新供应商地址如何同步到本机代理。切换后通常需要重启 Codex 才会加载新配置。",
                  style="Muted.TLabel", padding=(0, 4, 0, 9)).pack(anchor="w")
        self.mode = tk.StringVar(value=self._saved_mode())
        options = ttk.Frame(sync, style="Panel.TFrame")
        options.pack(fill="x")
        for value, title, detail in (
            ("poll", "1  定时检查", "每秒检查一次配置文件变化，发现供应商切换后自动同步。"),
            ("event", "2  CCSwitch 切换后自动同步", "CCSwitch 写入新配置时由 Windows 文件通知触发，不修改 CCSwitch 安装包。"),
            ("manual", "3  手动同步", "关闭自动监测；切换供应商后点击下方按钮更新当前配置。"),
        ):
            row = ttk.Frame(options, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Radiobutton(row, text=title, value=value, variable=self.mode,
                            command=lambda selected=value: self.set_mode(selected)).pack(side="left", anchor="n")
            ttk.Label(row, text=detail, style="Muted.TLabel").pack(side="left", padx=(10, 0))
        sync_actions = ttk.Frame(sync, style="Panel.TFrame")
        sync_actions.pack(fill="x", pady=(9, 0))
        self._button(sync_actions, "同步当前供应商", self.sync_provider).pack(side="left", padx=(0, 9))
        ttk.Label(sync_actions, text="密钥不会显示或写入同步日志。", style="Muted.TLabel").pack(side="left")

        lower = ttk.Frame(outer)
        lower.pack(fill="both", expand=True)
        left = ttk.Frame(lower, style="Panel.TFrame", padding=14)
        left.pack(side="left", fill="y", padx=(0, 12))
        ttk.Label(left, text="工具", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        for label, command in (("查看压缩记录", self.show_records),
                               ("本地自检", self.self_test),
                               ("开启登录后自启", lambda: self.startup("enable")),
                               ("关闭登录后自启", lambda: self.startup("disable")),
                               ("查看自启状态", lambda: self.startup("status"))):
            self._button(left, label, command).pack(fill="x", pady=3)
        ttk.Separator(left).pack(fill="x", pady=9)
        self._button(left, "合成缓存实验", lambda: self._paid_experiment("experiment.py", "约 6 次模型请求"))\
            .pack(fill="x", pady=3)
        self._button(left, "客户端运行实验", lambda: self._paid_experiment("runtime_smoke.py", "会启动 Codex 客户端并调用模型"))\
            .pack(fill="x", pady=3)
        self._button(left, "重启恢复实验", lambda: self._paid_experiment("restart_smoke.py", "约 3 次模型请求"))\
            .pack(fill="x", pady=3)

        right = ttk.Frame(lower, style="Panel.TFrame", padding=12)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="最近操作", style="Section.TLabel").pack(anchor="w", pady=(0, 7))
        self.output = ScrolledText(right, height=14, wrap="word", relief="flat", background="#fbfcfd",
                                   foreground="#263238", font=("Consolas", 9), padx=9, pady=8)
        self.output.pack(fill="both", expand=True)
        self.output.configure(state="disabled")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _button(self, parent, text, command):
        return ttk.Button(parent, text=text, command=command, style="Action.TButton")

    def _saved_mode(self):
        try:
            value = json.loads(SETTINGS.read_text(encoding="utf-8-sig")).get("provider_sync_mode", "event")
            return value if value in ("poll", "event", "manual") else "event"
        except (OSError, ValueError):
            return "event"

    def _record(self, text):
        self.output.configure(state="normal")
        self.output.insert("end", text.rstrip()+"\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def _run(self, args, callback=None, timeout=60):
        if self.busy:
            return
        self.busy = True

        def worker():
            try:
                result = subprocess.run([str(PYTHON), *map(str, args)], cwd=ROOT, capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                        creationflags=CREATE_NO_WINDOW)
                self.results.put((result.returncode, (result.stdout+result.stderr).strip() or "完成。", callback))
            except (OSError, subprocess.SubprocessError) as error:
                self.results.put((1, "操作失败："+str(error), callback))
        threading.Thread(target=worker, daemon=True).start()

    def _drain(self):
        try:
            while True:
                code, message, callback = self.results.get_nowait()
                self.busy = False
                self._record(message)
                if callback:
                    callback(code, message)
        except queue.Empty:
            pass
        self.after(150, self._drain)

    def _start_background(self):
        executable = PYTHONW if PYTHONW.exists() else PYTHON
        try:
            subprocess.Popen([str(executable), str(ROOT/"control.py"), "start"], cwd=ROOT,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=DETACHED)
            self._record("已发出启动命令，正在等待代理健康检查。")
            self.after(900, self.refresh)
        except OSError as error:
            self._record("启动失败："+str(error))

    def start_proxy(self):
        self._run([ROOT/"control.py", "status"], lambda code, _text: self._start_background())

    def restore_route(self):
        if messagebox.askyesno("恢复直连", "把当前 Codex 供应商地址恢复为直连？"):
            self._run([ROOT/"control.py", "restore"], lambda *_: self.refresh())

    def stop_proxy(self):
        if messagebox.askyesno("停止代理", "恢复当前供应商直连并停止代理？"):
            self._run([ROOT/"control.py", "stop"], lambda *_: self.refresh())

    def sync_provider(self):
        self._run([ROOT/"control.py", "sync-provider"], lambda *_: self.refresh())

    def set_mode(self, value):
        self._run([ROOT/"control.py", "set-sync-mode", "--mode", value])

    def show_records(self):
        self._run([ROOT/"control.py", "records"])

    def startup(self, action):
        self._run([ROOT/"autostart.py", action])

    def _paid_experiment(self, script, details):
        if messagebox.askyesno("实验会产生费用", f"{details}。确认现在运行 {script} 吗？"):
            self._run([ROOT/script], timeout=1800)

    def self_test(self):
        self._run(["-m", "unittest", "-v", "test_config_watch", "test_route_sync", "test_proxy", "test_autostart"])

    def refresh(self):
        self._run([ROOT/"control.py", "status"], self._show_status)

    def _show_status(self, code, output):
        self.status_var.set(output or ("状态检查失败。" if code else "代理未运行。"))

    def _tick(self):
        try:
            settings = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
            response = requests.get(settings["health_url"], timeout=0.7)
            data = response.json()
            if data.get("ok"):
                mode = {"poll": "定时检查", "event": "CCSwitch 切换触发", "manual": "手动同步"}.get(
                    settings.get("provider_sync_mode", "event"), "CCSwitch 切换触发")
                self.status_var.set(f"代理运行中 | 版本 {data.get('version', '?')} | 供应商同步：{mode}")
            else:
                self.status_var.set("代理未运行")
        except (OSError, ValueError, KeyError, requests.RequestException):
            self.status_var.set("代理未运行")
        self.after(2500, self._tick)


if __name__ == "__main__":
    ControlPanel().mainloop()
