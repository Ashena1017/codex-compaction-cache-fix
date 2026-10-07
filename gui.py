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
import requests
from layout import APP_DIR, DATA_DIR, INSTALL_DIR, SETTINGS_FILE
from gui_widgets import ScrollableTools
from window_theme import apply_titlebar


ROOT = INSTALL_DIR
SETTINGS = SETTINGS_FILE
try:
    _saved_settings = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
except (OSError, ValueError):
    _saved_settings = {}
PYTHON = Path(_saved_settings.get("python_executable") or sys.executable)
PYTHONW = PYTHON.with_name("pythonw.exe")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | CREATE_NO_WINDOW


class ControlPanel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Codex 压缩缓存修复")
        self.geometry("1020x840")
        self.minsize(960, 640)
        self.configure(background="#f3f5f7")
        self.results = queue.Queue()
        self.busy = False
        self.dark = self._saved_theme() != "light"
        self._style()
        self._build()
        self._apply_theme()
        self.bind("<Map>", self._on_map, add="+")
        self.refresh()
        self.after(150, self._drain)
        self.after(2500, self._tick)

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5f7")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f3f5f7", foreground="#263238", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"), foreground="#162a36")
        style.configure("Section.TLabel", background="#ffffff", font=("Segoe UI", 11, "bold"), foreground="#162a36")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#5e6c73", wraplength=720)
        style.configure("Action.TButton", padding=(12, 8), font=("Segoe UI", 10),
                         background="#28748a", foreground="#ffffff", borderwidth=0)
        style.map("Action.TButton", background=[("active", "#3193a7"), ("disabled", "#48545d")])
        style.configure("TRadiobutton", background="#ffffff", font=("Segoe UI", 10))

    def _apply_theme(self):
        if self.dark:
            bg, panel, text, muted, field = "#111820", "#1b2630", "#afbec8", "#889da9", "#0d141a"
            accent, hover, button_text = "#245b70", "#2c7188", "#bbcad2"
            indicator_border, indicator_dot = "#4b6371", "#77a4b5"
            self.theme_button.configure(text="日间模式")
        else:
            bg, panel, text, muted, field = "#f3f5f7", "#ffffff", "#263238", "#5e6c73", "#fbfcfd"
            accent, hover, button_text = "#28657a", "#347c91", "#f3f6f7"
            indicator_border, indicator_dot = "#81959f", "#28657a"
            self.theme_button.configure(text="夜间模式")
        self.configure(background=bg)
        style = ttk.Style(self)
        style.configure("TFrame", background=bg)
        style.configure("Panel.TFrame", background=panel)
        style.configure("TLabel", background=bg, foreground=text)
        style.configure("Title.TLabel", background=bg, foreground=text)
        style.configure("Section.TLabel", background=panel, foreground=text)
        style.configure("Muted.TLabel", background=panel, foreground=muted)
        style.configure("Hint.TLabel", background=bg, foreground=muted, font=("Segoe UI", 9))
        style.configure("TRadiobutton", background=panel, foreground=text,
                        indicatorbackground=panel, indicatorforeground=indicator_dot,
                        upperbordercolor=indicator_border, lowerbordercolor=indicator_border)
        style.map("TRadiobutton", background=[("active", panel)], foreground=[("active", text)])
        style.map("TRadiobutton", indicatorbackground=[("selected", panel), ("active", panel)])
        style.configure("Action.TButton", background=accent, foreground=button_text)
        style.map("Action.TButton", background=[("active", hover), ("disabled", "#354650")],
                  foreground=[("disabled", muted)])
        slider, slider_hover = ("#415563", "#587382") if self.dark else ("#b2bfc6", "#8e9ea8")
        style.configure("Panel.Vertical.TScrollbar", background=slider, troughcolor=field,
                        arrowcolor=muted, bordercolor=field, lightcolor=slider, darkcolor=slider,
                        relief="flat", borderwidth=0)
        style.map("Panel.Vertical.TScrollbar", background=[("active", slider_hover), ("pressed", accent)],
                  arrowcolor=[("active", text)], lightcolor=[("active", slider_hover)],
                  darkcolor=[("active", slider_hover)])
        style.configure("Panel.TSeparator", background=slider)
        if hasattr(self, "output"):
            self.output.configure(background=field, foreground=text, insertbackground=text,
                                  selectbackground=accent, selectforeground=button_text)
        if hasattr(self, "tools"):
            self.tools.apply_theme(panel)
        self._theme = (bg, panel, text, muted, field)
        self.after_idle(self._apply_titlebar)

    def _apply_titlebar(self):
        bg, _panel, text, _muted, _field = self._theme
        self.titlebar_dark_applied = apply_titlebar(self, self.dark, bg, text)

    def _on_map(self, event):
        if event.widget is self:
            self.after_idle(self._apply_titlebar)

    def _build(self):
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Codex 压缩缓存修复", style="Title.TLabel").pack(anchor="w")
        heading = ttk.Frame(outer)
        heading.pack(fill="x", pady=(3, 14))
        ttk.Label(heading, text="代理状态、供应商同步与启动设置").pack(side="left")
        self.theme_button = self._button(heading, "日间模式", self.toggle_theme)
        self.theme_button.pack(side="right")

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
        self.tools = ScrollableTools(left)
        self.tools.pack(fill="both", expand=True)
        tool_content = self.tools.content
        for label, command in (("查看压缩记录", self.show_records),
                               ("本地自检", self.self_test),
                               ("开启登录后自启", lambda: self.startup("enable")),
                               ("关闭登录后自启", lambda: self.startup("disable")),
                               ("查看自启状态", lambda: self.startup("status"))):
            self._button(tool_content, label, command).pack(fill="x", pady=3)
        ttk.Separator(tool_content, style="Panel.TSeparator").pack(fill="x", pady=9)
        self._button(tool_content, "合成缓存实验", lambda: self._paid_experiment("experiment.py", "约 6 次模型请求"))\
            .pack(fill="x", pady=3)
        self._button(tool_content, "客户端运行实验", lambda: self._paid_experiment("runtime_smoke.py", "会启动 Codex 客户端并调用模型"))\
            .pack(fill="x", pady=3)
        self._button(tool_content, "重启恢复实验", lambda: self._paid_experiment("restart_smoke.py", "约 3 次模型请求"))\
            .pack(fill="x", pady=3)
        for widget in tool_content.winfo_children():
            if isinstance(widget, ttk.Button):
                widget.bind("<FocusIn>", lambda _event, button=widget: self.tools.keep_visible(button))

        right = ttk.Frame(lower, style="Panel.TFrame", padding=12)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="最近操作", style="Section.TLabel").pack(anchor="w", pady=(0, 7))
        log_area = ttk.Frame(right, style="Panel.TFrame")
        log_area.pack(fill="both", expand=True)
        self.output = tk.Text(log_area, height=8, width=40, wrap="word", relief="flat",
                              highlightthickness=0, borderwidth=0, background="#fbfcfd",
                              foreground="#263238", font=("Consolas", 9), padx=9, pady=8)
        self.output_scroll = ttk.Scrollbar(log_area, orient="vertical", command=self.output.yview,
                                           style="Panel.Vertical.TScrollbar")
        self.output.configure(yscrollcommand=self.output_scroll.set)
        self.output_scroll.pack(side="right", fill="y")
        self.output.pack(side="left", fill="both", expand=True)
        self.output.configure(state="disabled")
        ttk.Label(outer, text="关闭窗口后，已启动的修复代理继续运行。停用时请点“停止代理”。",
                  style="Hint.TLabel", padding=(0, 8, 0, 0)).pack(anchor="w")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _button(self, parent, text, command):
        return ttk.Button(parent, text=text, command=command, style="Action.TButton")

    def toggle_theme(self):
        self.dark = not self.dark
        self._apply_theme()
        try:
            values = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
            values["ui_theme"] = "dark" if self.dark else "light"
            temporary = SETTINGS.with_name(SETTINGS.name+".new")
            temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
            temporary.replace(SETTINGS)
        except (OSError, ValueError) as error:
            self._record("主题已切换，但未能保存偏好："+str(error))

    @staticmethod
    def _saved_theme():
        return _saved_settings.get("ui_theme", "dark")

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
                result = subprocess.run([str(PYTHON), *map(str, args)], cwd=APP_DIR, capture_output=True,
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
            subprocess.Popen([str(executable), str(APP_DIR/"control.py"), "start"], cwd=APP_DIR,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=DETACHED)
            self._record("已发出启动命令，正在等待代理健康检查。")
            self.after(900, self.refresh)
        except OSError as error:
            self._record("启动失败："+str(error))

    def start_proxy(self):
        self._run([APP_DIR/"control.py", "status"], lambda code, _text: self._start_background())

    def restore_route(self):
        if messagebox.askyesno("恢复直连", "把当前 Codex 供应商地址恢复为直连？"):
            self._run([APP_DIR/"control.py", "restore"], lambda *_: self.refresh())

    def stop_proxy(self):
        if messagebox.askyesno("停止代理", "恢复当前供应商直连并停止代理？"):
            self._run([APP_DIR/"control.py", "stop"], lambda *_: self.refresh())

    def sync_provider(self):
        self._run([APP_DIR/"control.py", "sync-provider"], lambda *_: self.refresh())

    def set_mode(self, value):
        self._run([APP_DIR/"control.py", "set-sync-mode", "--mode", value])

    def show_records(self):
        self._run([APP_DIR/"control.py", "records"])

    def startup(self, action):
        self._run([APP_DIR/"autostart.py", action])

    def _paid_experiment(self, script, details):
        if messagebox.askyesno("实验会产生费用", f"{details}。确认现在运行 {script} 吗？"):
            self._run([APP_DIR/script], timeout=1800)

    def self_test(self):
        test_dir = APP_DIR/"tests" if (APP_DIR/"tests").is_dir() else APP_DIR
        self._run(["-m", "unittest", "discover", "-v", "-s", test_dir, "-t", APP_DIR])

    def refresh(self):
        self._run([APP_DIR/"control.py", "status"], self._show_status)

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
