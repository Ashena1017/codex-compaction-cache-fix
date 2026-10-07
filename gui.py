"""Desktop control panel for the Codex compaction proxy."""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tomllib
from pathlib import Path
from tkinter import font as tkfont, messagebox, ttk

import requests
from layout import APP_DIR, INSTALL_DIR, SETTINGS_FILE
from gui_widgets import ScrollablePage
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
MODE_NAMES = {"event": "切换后自动同步（推荐）", "poll": "定时检查", "manual": "手动同步"}
PAGE_TITLES = {
    "home": ("概览", "查看代理与 Codex 的连接状态，管理日常启停。"),
    "sync": ("供应商同步", "选择切换供应商后，怎样更新修复代理。"),
    "startup": ("登录自启", "让代理在登录 Windows 后自动运行。"),
    "diagnostics": ("诊断与实验", "本地检查与缓存实验各自独立，按需要运行。"),
    "logs": ("操作记录", "查看操作结果、状态详情和最近的压缩记录。"),
}


class ControlPanel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Codex 压缩缓存修复")
        self.geometry("1080x760")
        self.minsize(900, 620)
        self.results = queue.Queue()
        self.busy = False
        self.monitor_busy = False
        self.dark = _saved_settings.get("ui_theme", "dark") != "light"
        self.action_widgets = []
        self.current_page = "home"
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
        font = ("Microsoft YaHei UI", 11)
        style.configure("TLabel", font=font)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 21, "bold"))
        style.configure("Brand.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Value.TLabel", font=("Microsoft YaHei UI", 14, "bold"))
        for name in ("Good.TLabel", "Warning.TLabel"):
            style.configure(name, font=("Microsoft YaHei UI", 14, "bold"))
        for name in ("Primary.TButton", "Secondary.TButton", "Nav.TButton", "ActiveNav.TButton"):
            style.configure(name, font=font, padding=(14, 10), borderwidth=0, focusthickness=1, width=0)
        for name in ("Nav.TButton", "ActiveNav.TButton"):
            style.configure(name, anchor="w", padding=(18, 13))
        style.configure("TRadiobutton", font=("Microsoft YaHei UI", 12, "bold"), padding=(0, 3))

    def _apply_theme(self):
        if self.dark:
            bg, panel, text, muted, field = "#181818", "#232323", "#d4d4d4", "#ababab", "#1b1b1b"
            accent, hover, button_text = "#d4d4d4", "#e2e2e2", "#202020"
            secondary, secondary_hover, border = "#333333", "#404040", "#454545"
            nav_active, good, warning = "#2e2e2e", "#86cfa4", "#dcb477"
        else:
            bg, panel, text, muted, field = "#f2f2f2", "#ffffff", "#292929", "#606060", "#f7f7f7"
            accent, hover, button_text = "#303030", "#454545", "#f5f5f5"
            secondary, secondary_hover, border = "#e7e7e7", "#d8d8d8", "#bdbdbd"
            nav_active, good, warning = "#dddddd", "#24694e", "#87561a"
        self._theme = (bg, panel, text, muted, field)
        self.configure(background=bg)
        style = ttk.Style(self)
        for name, color in (("TFrame", bg), ("Panel.TFrame", panel), ("Sidebar.TFrame", field)):
            style.configure(name, background=color)
        for name, background, foreground in (
            ("TLabel", bg, text), ("Title.TLabel", bg, text), ("Hint.TLabel", bg, muted),
            ("Brand.TLabel", field, text), ("SideHint.TLabel", field, muted),
            ("Section.TLabel", panel, text), ("Value.TLabel", panel, text),
            ("Body.TLabel", panel, text), ("Muted.TLabel", panel, muted),
            ("Good.TLabel", panel, good), ("Warning.TLabel", panel, warning),
        ):
            style.configure(name, background=background, foreground=foreground)
        for name, normal, active, fg in (
            ("Primary.TButton", accent, hover, button_text),
            ("Secondary.TButton", secondary, secondary_hover, text),
            ("Nav.TButton", field, secondary, muted),
            ("ActiveNav.TButton", nav_active, nav_active, text),
        ):
            style.configure(name, background=normal, foreground=fg, bordercolor=normal,
                            lightcolor=normal, darkcolor=normal, focuscolor=muted)
            style.map(name, background=[("disabled", secondary), ("active", active)],
                      foreground=[("disabled", muted), ("active", fg)])
        style.configure("TRadiobutton", background=panel, foreground=text, indicatorbackground=panel,
                        indicatorforeground=accent, upperbordercolor=border, lowerbordercolor=border)
        style.map("TRadiobutton", background=[("active", panel)], foreground=[("disabled", muted), ("active", text)],
                  indicatorbackground=[("selected", panel), ("active", panel)])
        style.configure("Panel.Vertical.TScrollbar", background=border, troughcolor=bg,
                        arrowcolor=muted, bordercolor=bg, lightcolor=border, darkcolor=border,
                        relief="flat", borderwidth=0)
        style.map("Panel.Vertical.TScrollbar", background=[("active", secondary_hover), ("pressed", accent)],
                  lightcolor=[("active", secondary_hover)], darkcolor=[("active", secondary_hover)],
                  arrowcolor=[("active", text)])
        for page in self.pages.values():
            if isinstance(page, ScrollablePage):
                page.apply_theme(bg)
        self.output.configure(background=field, foreground=text, insertbackground=text,
                              selectbackground=accent, selectforeground=button_text)
        self.theme_button.configure(text="切换日间模式" if self.dark else "切换夜间模式")
        self.after_idle(self._apply_titlebar)

    def _apply_titlebar(self):
        bg, _panel, text, _muted, _field = self._theme
        self.titlebar_dark_applied = apply_titlebar(self, self.dark, bg, text)

    def _on_map(self, event):
        if event.widget is self:
            self.after_idle(self._apply_titlebar)

    def _build(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        side_width = max(196, tkfont.Font(font=("Microsoft YaHei UI", 11)).measure("切换日间模式")+70)
        side = ttk.Frame(self, style="Sidebar.TFrame", padding=(16, 25, 16, 18), width=side_width)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        side.columnconfigure(0, weight=1)
        side.rowconfigure(7, weight=1)
        ttk.Label(side, text="Codex Fix", style="Brand.TLabel").grid(row=0, sticky="w")
        ttk.Label(side, text="压缩缓存修复", style="SideHint.TLabel").grid(row=1, sticky="w", pady=(4, 24))
        self.nav_buttons = {}
        for row, (key, (title, _)) in enumerate(PAGE_TITLES.items(), 2):
            button = ttk.Button(side, text=title, style="Nav.TButton", command=lambda k=key: self.show_page(k))
            button.grid(row=row, sticky="ew", pady=3)
            self.nav_buttons[key] = button
        self.theme_button = ttk.Button(side, command=self.toggle_theme, style="Nav.TButton")
        self.theme_button.grid(row=8, sticky="ew", pady=(12, 10))
        ttk.Label(side, text="关闭面板后\n代理继续运行", style="SideHint.TLabel", justify="left").grid(row=9, sticky="w")
        main = ttk.Frame(self, padding=(28, 22, 16, 16))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        self.page_title = tk.StringVar()
        self.page_hint = tk.StringVar()
        ttk.Label(main, textvariable=self.page_title, style="Title.TLabel").grid(row=0, sticky="w")
        hint = ttk.Label(main, textvariable=self.page_hint, style="Hint.TLabel")
        hint.grid(row=1, sticky="ew", pady=(5, 18))
        self.page_host = ttk.Frame(main)
        self.page_host.grid(row=2, sticky="nsew")
        self.page_host.rowconfigure(0, weight=1)
        self.page_host.columnconfigure(0, weight=1)
        self.pages = {}
        for key in PAGE_TITLES:
            page = ttk.Frame(self.page_host) if key == "logs" else ScrollablePage(self.page_host)
            self.pages[key] = page
        self.notice_var = tk.StringVar(value="就绪")
        notice = ttk.Label(main, textvariable=self.notice_var, style="Hint.TLabel")
        notice.grid(row=3, sticky="ew", pady=(12, 0))
        main.bind("<Configure>", lambda e: (hint.configure(wraplength=max(300, e.width-55)),
                                           notice.configure(wraplength=max(300, e.width-55))))
        self._build_home(self.pages["home"].content)
        self._build_sync(self.pages["sync"].content)
        self._build_startup(self.pages["startup"].content)
        self._build_diagnostics(self.pages["diagnostics"].content)
        self._build_logs(self.pages["logs"])
        self.show_page("home")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _card(self, parent):
        card = ttk.Frame(parent, style="Panel.TFrame", padding=20)
        card.pack(fill="x", pady=(0, 14))
        return card

    def _label(self, parent, text=None, variable=None, style="Muted.TLabel", pady=0):
        label = ttk.Label(parent, text=text, textvariable=variable, style=style, justify="left", wraplength=600)
        label.pack(fill="x", pady=pady)
        parent.bind("<Configure>", lambda e, w=label: w.configure(wraplength=max(180, e.width-40)), add="+")
        return label

    def _button(self, parent, text, command, primary=False):
        button = ttk.Button(parent, text=text, command=command,
                            style="Primary.TButton" if primary else "Secondary.TButton")
        self.action_widgets.append(button)
        button.bind("<FocusIn>", lambda _e, w=button: self._focus_visible(w))
        return button

    def _focus_visible(self, widget):
        page = self.pages[self.current_page]
        if isinstance(page, ScrollablePage):
            page.keep_visible(widget)

    def _build_home(self, parent):
        self.status_var = tk.StringVar(value="正在检查连接状态…")
        self.proxy_var = tk.StringVar(value="检查中")
        self.route_var = tk.StringVar(value="检查中")
        card = self._card(parent)
        self._label(card, "运行状态", style="Section.TLabel")
        states = ttk.Frame(card, style="Panel.TFrame")
        states.pack(fill="x", pady=(14, 10))
        states.columnconfigure((0, 1), weight=1, uniform="state")
        for column, (title, variable) in enumerate((("后台代理", self.proxy_var), ("Codex 配置", self.route_var))):
            cell = ttk.Frame(states, style="Panel.TFrame")
            cell.grid(row=0, column=column, sticky="ew", padx=(0, 16))
            self._label(cell, title)
            label = self._label(cell, variable=variable, style="Value.TLabel", pady=(5, 0))
            if column == 0:
                self.proxy_label = label
            else:
                self.route_label = label
        self._label(card, variable=self.status_var, style="Body.TLabel", pady=(0, 12))
        actions = ttk.Frame(card, style="Panel.TFrame")
        actions.pack(fill="x")
        for column, (text, command, primary) in enumerate((
            ("启动修复", self.start_proxy, True), ("恢复直连", self.restore_route, False),
            ("停止代理", self.stop_proxy, False), ("刷新状态", self.refresh, False))):
            actions.columnconfigure(column, weight=1)
            self._button(actions, text, command, primary).grid(row=0, column=column, sticky="ew", padx=(0, 8))
        card = self._card(parent)
        self._label(card, "当前连接", style="Section.TLabel", pady=(0, 10))
        self.details = {}
        for key, title in (("model", "模型"), ("provider", "供应商标识"), ("upstream", "上游地址"),
                           ("mode", "同步方式"), ("snapshots", "工具快照")):
            row = ttk.Frame(card, style="Panel.TFrame")
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=title, style="Muted.TLabel", width=12).pack(side="left", anchor="n")
            var = tk.StringVar(value="—")
            label = ttk.Label(row, textvariable=var, style="Body.TLabel", wraplength=440, justify="left")
            label.pack(side="left", fill="x", expand=True)
            row.bind("<Configure>", lambda e, w=label: w.configure(wraplength=max(180, e.width-130)))
            self.details[key] = var
        quick = ttk.Frame(parent)
        quick.pack(fill="x", pady=(0, 6))
        self._button(quick, "查看压缩记录", self.show_records).pack(side="left", padx=(0, 10))
        self._button(quick, "同步当前供应商", self.sync_provider).pack(side="left")

    def _build_sync(self, parent):
        self.mode = tk.StringVar(value=self._saved_mode())
        for value, title, detail in (
            ("event", "切换后自动同步（推荐）", "CCSwitch 写入配置后，由 Windows 文件通知触发同步。无需修改 CCSwitch 安装包，更新 CCSwitch 后也能继续使用。"),
            ("poll", "定时检查", "每秒检查配置文件。切换供应商后，代理自动跟随新地址。"),
            ("manual", "手动同步", "不自动监测。切换供应商后，在面板里点击“同步当前供应商”。"),
        ):
            card = self._card(parent)
            radio = ttk.Radiobutton(card, text=title, value=value, variable=self.mode,
                                    command=lambda v=value: self.set_mode(v))
            radio.pack(anchor="w")
            radio.bind("<FocusIn>", lambda _e, w=radio: self._focus_visible(w))
            self.action_widgets.append(radio)
            self._label(card, detail, pady=(9, 0))
        card = self._card(parent)
        self._button(card, "同步当前供应商", self.sync_provider, True).pack(anchor="w")
        self._label(card, "切换后通常需要重启 Codex，让它加载新配置。密钥不会显示在面板或同步日志中。", pady=(12, 0))

    def _build_startup(self, parent):
        card = self._card(parent)
        self.startup_var = tk.StringVar(value="点击“查看状态”读取当前设置")
        self._label(card, "登录后自动启动", style="Section.TLabel")
        self._label(card, variable=self.startup_var, style="Value.TLabel", pady=(14, 12))
        self._label(card, "启用后，登录当前 Windows 账号约 15 秒后启动修复代理，不弹出控制面板。")
        actions = ttk.Frame(card, style="Panel.TFrame")
        actions.pack(fill="x", pady=(20, 0))
        for text, action in (("开启自启", "enable"), ("关闭自启", "disable"), ("查看状态", "status")):
            self._button(actions, text, lambda a=action: self.startup(a), action == "enable").pack(side="left", padx=(0, 10))
        card = self._card(parent)
        self._label(card, "使用时留意", style="Section.TLabel")
        self._label(card, "关闭自启只影响下次登录，当前代理会继续运行。\n\n移动工具目录或更换 Python 时，先关闭自启，再从新位置重新开启。", pady=(12, 0))

    def _build_diagnostics(self, parent):
        for title, detail, button_text, command, primary in (
            ("本地自检", "检查代理、供应商同步和快照相关代码。不调用模型，不产生 API 费用。", "运行自检", self.self_test, True),
            ("合成缓存实验", "构造测试会话，比较普通请求与压缩请求的缓存情况。约 6 次模型请求，会产生费用。", "运行实验", lambda: self._paid_experiment("experiment.py", "约 6 次模型请求"), False),
            ("客户端运行实验", "启动 Codex 客户端，验证真实客户端的请求流程。会调用模型并产生费用。", "运行实验", lambda: self._paid_experiment("runtime_smoke.py", "会启动 Codex 客户端并调用模型"), False),
            ("重启恢复实验", "验证代理重启后能否恢复工具快照，再完成压缩。约 3 次模型请求，会产生费用。", "运行实验", lambda: self._paid_experiment("restart_smoke.py", "约 3 次模型请求"), False),
        ):
            card = self._card(parent)
            card.columnconfigure(0, weight=1)
            copy = ttk.Frame(card, style="Panel.TFrame")
            copy.grid(row=0, column=0, sticky="ew", padx=(0, 20))
            self._label(copy, title, style="Section.TLabel")
            self._label(copy, detail, pady=(8, 0))
            self._button(card, button_text, command, primary).grid(row=0, column=1)

    def _build_logs(self, page):
        toolbar = ttk.Frame(page)
        toolbar.pack(fill="x", pady=(0, 12))
        self._button(toolbar, "读取压缩记录", self.show_records).pack(side="left", padx=(0, 10))
        self._button(toolbar, "读取状态详情", self.refresh).pack(side="left", padx=(0, 10))
        ttk.Button(toolbar, text="清空显示", command=self.clear_output, style="Secondary.TButton").pack(side="right")
        area = ttk.Frame(page, style="Panel.TFrame", padding=12)
        area.pack(fill="both", expand=True)
        self.output = tk.Text(area, width=1, height=1, wrap="word", relief="flat", highlightthickness=0,
                              borderwidth=0, font=("Microsoft YaHei UI", 11), padx=10, pady=8, spacing1=3, spacing3=5)
        self.output_scroll = ttk.Scrollbar(area, orient="vertical", command=self.output.yview,
                                           style="Panel.Vertical.TScrollbar")
        self.output.configure(yscrollcommand=self.output_scroll.set, state="disabled")
        self.output_scroll.pack(side="right", fill="y")
        self.output.pack(side="left", fill="both", expand=True)

    def show_page(self, key):
        self.current_page = key
        for name, page in self.pages.items():
            if name != key:
                page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        self.pages[key].tkraise()
        self.page_title.set(PAGE_TITLES[key][0])
        self.page_hint.set(PAGE_TITLES[key][1])
        for name, button in self.nav_buttons.items():
            button.configure(style="ActiveNav.TButton" if name == key else "Nav.TButton")

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

    def _saved_mode(self):
        try:
            value = json.loads(SETTINGS.read_text(encoding="utf-8-sig")).get("provider_sync_mode", "event")
            return value if value in MODE_NAMES else "event"
        except (OSError, ValueError):
            return "event"

    def clear_output(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def _record(self, text):
        self.output.configure(state="normal")
        self.output.insert("end", text.rstrip()+"\n\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def _set_busy(self, value):
        self.busy = value
        for widget in self.action_widgets:
            widget.state(["disabled"] if value else ["!disabled"])

    def _run(self, args, callback=None, timeout=60):
        if self.busy:
            self.notice_var.set("上一项操作还在运行，完成后再试。详细输出在“操作记录”。")
            return
        self._set_busy(True)
        self.notice_var.set("正在执行… 详细输出在“操作记录”。")
        def worker():
            try:
                result = subprocess.run([str(PYTHON), *map(str, args)], cwd=APP_DIR, capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                        creationflags=CREATE_NO_WINDOW)
                self.results.put(("command", result.returncode, (result.stdout+result.stderr).strip() or "完成。", callback))
            except (OSError, subprocess.SubprocessError) as error:
                self.results.put(("command", 1, "操作失败："+str(error), callback))
        threading.Thread(target=worker, daemon=True).start()

    def _drain(self):
        try:
            while True:
                kind, code, message, callback = self.results.get_nowait()
                if kind == "monitor":
                    self.monitor_busy = False
                    self._display_state(message)
                    continue
                self._set_busy(False)
                self._record(message)
                self.notice_var.set("操作完成。详细输出在“操作记录”。" if code == 0 else "操作未完成，请在“操作记录”查看原因。")
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
            self.notice_var.set("已发出启动命令，正在等待状态更新。")
            self.after(900, self.refresh)
        except OSError as error:
            self._record("启动失败："+str(error))

    def start_proxy(self):
        self._run([APP_DIR/"control.py", "status"], lambda code, _text: self._start_background() if code == 0 else None)

    def restore_route(self):
        if messagebox.askyesno("恢复直连", "把当前 Codex 供应商地址恢复为直连？"):
            self._run([APP_DIR/"control.py", "restore"], lambda *_: self.refresh())

    def stop_proxy(self):
        if messagebox.askyesno("停止代理", "恢复当前供应商直连并停止代理？"):
            self._run([APP_DIR/"control.py", "stop"], lambda *_: self.refresh())

    def sync_provider(self):
        self._run([APP_DIR/"control.py", "sync-provider"], lambda *_: self.refresh())

    def set_mode(self, value):
        self._run([APP_DIR/"control.py", "set-sync-mode", "--mode", value],
                  lambda *_: self.mode.set(self._saved_mode()))

    def show_records(self):
        self.show_page("logs")
        self._run([APP_DIR/"control.py", "records"])

    def startup(self, action):
        self._run([APP_DIR/"autostart.py", action], self._show_startup)

    def _show_startup(self, code, output):
        line = next((line for line in output.splitlines() if line.startswith("开机自启：")), None)
        self.startup_var.set(line.split("：", 1)[1] if line else "查询失败，请查看操作记录")

    def _paid_experiment(self, script, details):
        if messagebox.askyesno("实验会产生费用", f"{details}。确认现在运行 {script} 吗？"):
            self.show_page("logs")
            self._run([APP_DIR/script], timeout=1800)

    def self_test(self):
        self.show_page("logs")
        test_dir = APP_DIR/"tests" if (APP_DIR/"tests").is_dir() else APP_DIR
        self._run(["-m", "unittest", "discover", "-v", "-s", test_dir, "-t", APP_DIR])

    def refresh(self):
        self._run([APP_DIR/"control.py", "status"], self._show_status)

    def _show_status(self, code, output):
        fields = dict(line.split("：", 1) for line in output.splitlines() if "：" in line)
        state = {"running": fields.get("代理", "").startswith("正在运行"),
                 "local": fields.get("配置") == "修复已启用" if "配置" in fields else None,
                 "model": fields.get("模型", "—"),
                 "provider": fields.get("当前使用的供应商", "—"), "mode": fields.get("供应商同步", "—"),
                 "snapshots": fields.get("工具快照", "—"), "error": fields.get("快照文件异常", "")}
        try:
            settings = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
            state["upstream"] = settings.get("upstream_base_url", "—")
        except (OSError, ValueError):
            state["upstream"] = "—"
        if code:
            state["status_error"] = True
        self._display_state(state)

    def _display_state(self, state):
        running, local = state.get("running", False), state.get("local")
        self.proxy_var.set("运行中" if running else "未运行")
        self.proxy_label.configure(style="Good.TLabel" if running else "Warning.TLabel")
        self.route_var.set("检查失败" if local is None else "已连接修复代理" if local else "供应商直连")
        self.route_label.configure(style="Good.TLabel" if local and running else "Warning.TLabel")
        if local and running:
            summary = "修复已就绪。新加载此配置的聊天会经过代理。"
        elif local:
            summary = "Codex 已配置本机地址，但代理未运行。请启动修复或恢复直连。"
        elif local is False and running:
            summary = "代理正在运行，Codex 配置仍是直连。点击“启动修复”可接入。"
        elif local is False:
            summary = "Codex 当前直连供应商。需要修复时，点击“启动修复”。"
        else:
            summary = "无法读取 Codex 配置，请刷新状态并查看操作记录。"
        if state.get("status_error"):
            summary = "状态检查未完成，请查看操作记录。"
        if state.get("error"):
            summary += " 工具快照有异常，请在操作记录中查看状态详情。"
        self.status_var.set(summary)
        for key, var in self.details.items():
            var.set(str(state.get(key, "—")))

    def _tick(self):
        # Observe status off Tk's thread. The independent proxy handles provider sync.
        if not self.monitor_busy and not self.busy:
            self.monitor_busy = True
            def worker():
                state = {"local": None, "running": False}
                try:
                    settings = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
                    state["upstream"] = settings.get("upstream_base_url", "—")
                    state["mode"] = MODE_NAMES.get(settings.get("provider_sync_mode"), "—")
                    try:
                        config = tomllib.loads(Path(settings["config_path"]).read_text(encoding="utf-8-sig"))
                        provider = config.get("model_provider", settings.get("provider_id", ""))
                        url = config.get("model_providers", {}).get(provider, {}).get("base_url", "")
                        state.update(model=config.get("model", "—"), provider=provider,
                                     local=url.rstrip("/") == settings["local_base_url"].rstrip("/"))
                    except (OSError, ValueError, KeyError, TypeError, AttributeError):
                        pass
                    response = requests.get(settings["health_url"], timeout=0.7)
                    response.raise_for_status()
                    data = response.json()
                    state["running"] = bool(data.get("ok"))
                    snapshots = data.get("snapshots") or {}
                    state["snapshots"] = f"可用 {snapshots.get('available', 0)} 个 · 保留 24 小时"
                    state["error"] = snapshots.get("error", "")
                except (OSError, ValueError, KeyError, TypeError, AttributeError, requests.RequestException):
                    pass
                self.results.put(("monitor", 0, state, None))
            threading.Thread(target=worker, daemon=True).start()
        self.after(3000, self._tick)


if __name__ == "__main__":
    ControlPanel().mainloop()
