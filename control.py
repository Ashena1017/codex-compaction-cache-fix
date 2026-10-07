"""User-facing start, status, restore, stop and usage commands for this folder."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import requests

from proxy import VERSION, make_server
from route_sync import ProviderRouteManager
from config_watch import WindowsConfigWatcher

ROOT = Path(__file__).resolve().parent


def settings():
    return json.loads((ROOT/"settings.json").read_text(encoding="utf-8-sig"))


SYNC_MODES = {"poll", "event", "manual"}


def write_settings(values):
    path = ROOT/"settings.json"
    temporary = path.with_name(path.name+".new")
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    temporary.replace(path)


def set_sync_mode(mode):
    if mode not in SYNC_MODES:
        raise ValueError("同步方式只能是 poll、event 或 manual。")
    if mode == "event" and os.name != "nt":
        raise RuntimeError("CCSwitch 文件通知模式仅支持 Windows。")
    values = settings()
    values["provider_sync_mode"] = mode
    write_settings(values)
    print("供应商同步方式已设为："+{"poll": "定时检查", "event": "CCSwitch 切换后自动同步", "manual": "手动同步"}[mode])


def sync_provider():
    if not health():
        raise RuntimeError("代理未运行。请先启动修复，再同步供应商。")
    ProviderRouteManager(ROOT/"settings.json").sync()
    print("已读取当前 Codex 供应商，并更新代理上游。地址和密钥不会显示。")


def change_provider_url(text, provider, expected, replacement):
    pattern = r'(?ms)(^\[model_providers\.'+re.escape(provider)+r'\]\s*\n)(.*?)(?=^\[|\Z)'
    matched = re.search(pattern, text)
    if not matched:
        raise RuntimeError("没有找到对应的模型供应商配置。")
    def update(match):
        if tomllib.loads(match.group(0))["base_url"] != expected:
            raise RuntimeError("网关地址已经改过，工具没有覆盖它。请先检查配置。")
        return 'base_url = '+json.dumps(replacement)
    section, count = re.subn(r'(?m)^base_url\s*=\s*[^\r\n]+', update, matched.group(2))
    if count != 1:
        raise RuntimeError("配置中的 base_url 数量异常，工具没有改动配置。")
    return text[:matched.start(2)]+section+text[matched.end(2):]


def configured():
    s = settings()
    path = Path(s["config_path"])
    text = path.read_text(encoding="utf-8-sig")
    config = tomllib.loads(text)
    return s, path, text, config


def set_route(enable):
    s, path, text, config = configured()
    current = config["model_providers"][s["provider_id"]]["base_url"]
    wanted = s["local_base_url"] if enable else s["upstream_base_url"]
    if current == wanted:
        return False
    expected = s["upstream_base_url"] if enable else s["local_base_url"]
    updated = change_provider_url(text, s["provider_id"], expected, wanted)
    parsed = tomllib.loads(updated)
    config["model_providers"][s["provider_id"]]["base_url"] = wanted
    if parsed != config:
        raise RuntimeError("发现额外配置变化，工具没有保存。")
    temporary = path.with_name(path.name+".compact-fix-new")
    temporary.write_text(updated, encoding="utf-8")
    temporary.replace(path)
    return True


def health():
    try:
        response = requests.get(settings()["health_url"], timeout=2)
        response.raise_for_status()
        result = response.json()
        return result if result.get("ok") and str(result.get("version", "")).startswith("1.") else None
    except (requests.RequestException, ValueError):
        return None


def same_directory(value):
    return value and os.path.normcase(os.path.abspath(value)) == os.path.normcase(str(ROOT))


def start():
    existing = health()
    if existing:
        if not same_directory(existing.get("directory")):
            raise RuntimeError("端口被旧位置的代理占用。请先停止旧代理，再启动这个目录的版本。")
        if existing.get("version", VERSION) != VERSION:
            raise RuntimeError("正在运行旧版代理。先运行“停止代理”，再运行“启动修复”，才能加载新代码。")
        manager = ProviderRouteManager(ROOT/"settings.json")
        manager.sync()
        print("代理已经在运行，修复已启用，不会再开第二个代理。")
        return
    s = settings()
    manager = ProviderRouteManager(ROOT/"settings.json")
    upstream = manager.discover()
    server = make_server(upstream, s["port"], s["events_path"], s.get("snapshot_path"))
    server.state.set_upstream(upstream, manager.expected_authorization())
    def synchronize():
        current = settings()
        if current.get("provider_sync_mode", "event") == "manual":
            upstream = current["upstream_base_url"]
        else:
            upstream = manager.sync_if_changed()
        if upstream:
            server.state.set_upstream(upstream, manager.expected_authorization())
    server.state.sync_callback = synchronize
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    watcher = None
    thread.start()
    try:
        if not health():
            raise RuntimeError("代理启动后没有通过健康检查，Codex 配置未切换。")
        server.state.set_upstream(manager.sync(), manager.expected_authorization())
        def on_config_changed():
            try:
                server.state.set_upstream(manager.sync(), manager.expected_authorization())
                print("检测到 Codex 供应商变化，代理上游已更新。", flush=True)
            except (OSError, ValueError, RuntimeError) as error:
                print("供应商配置尚未同步："+str(error), flush=True)

        print("代理已启动，Codex 修复已启用。", flush=True)
        print("地址："+s["local_base_url"], flush=True)
        print("上游："+upstream, flush=True)
        print("供应商切换：自动跟随当前 Codex 配置。", flush=True)
        print("这个窗口需要保持打开，可以最小化。", flush=True)
        print("按 Ctrl+C 退出时会恢复当前供应商直连；也可以用“停止代理”脚本。", flush=True)
        while thread.is_alive():
            thread.join(timeout=1)
            mode = settings().get("provider_sync_mode", "event")
            if mode == "event" and watcher is None:
                watcher = WindowsConfigWatcher(settings()["config_path"], on_config_changed)
                watcher.start()
            elif mode != "event" and watcher is not None:
                watcher.stop()
                watcher = None
            if mode == "poll":
                try:
                    upstream = manager.sync_if_changed()
                    if upstream:
                        server.state.set_upstream(upstream, manager.expected_authorization())
                except (OSError, ValueError, RuntimeError) as error:
                    print("供应商配置尚未同步："+str(error), flush=True)
            elif mode == "manual":
                server.state.set_upstream(settings()["upstream_base_url"], manager.expected_authorization())
    except KeyboardInterrupt:
        if "watcher" in locals() and watcher is not None:
            watcher.stop()
        manager.restore()
        print("已恢复直连，正在停止代理。", flush=True)
    finally:
        if watcher is not None:
            watcher.stop()
        server.shutdown()
        server.server_close()


def restore():
    manager = ProviderRouteManager(ROOT/"settings.json")
    changed = manager.restore()
    print("已恢复当前供应商直连。模型、密钥和其他配置没有改动。" if changed else "当前配置已经是直连。")
    print("重新打开聊天或重启 Codex，让它加载这个配置。")


def status():
    s, _, _, config = configured()
    service = health()
    active_provider = config.get("model_provider", s["provider_id"])
    active_config = config.get("model_providers", {}).get(active_provider, {})
    current = active_config.get("base_url") or "未知"
    print("模型："+config.get("model", "未知"))
    print("当前使用的供应商："+active_provider)
    print("配置地址："+current)
    is_local = isinstance(current, str) and current.rstrip("/") == s["local_base_url"].rstrip("/")
    print("配置："+("修复已启用" if is_local else "供应商直连"))
    mode = s.get("provider_sync_mode", "event")
    print("供应商同步："+{"poll": "定时检查", "event": "CCSwitch 切换后自动同步", "manual": "手动同步"}.get(mode, "定时检查"))
    if service:
        print("代理：正在运行，进程 "+str(service.get("pid", "未知")))
        print("版本："+str(service.get("version", "未知")))
        print("供应商切换：自动跟随当前 Codex 配置")
        print("运行目录："+service.get("directory", "旧版本未报告目录"))
        snapshots = service.get("snapshots")
        if snapshots:
            print(f"工具快照：当前可用 {snapshots['available']} 个，本次启动从磁盘恢复 {snapshots['restored_at_start']} 个")
            print("快照保存："+("Windows 用户加密，保留 24 小时" if snapshots["persistent"] else "仅内存"))
            if snapshots.get("error"):
                print("快照文件异常："+snapshots["error"]+"；先完成一次普通回复，再检查。")
        else:
            print("工具快照：旧版本仅存内存，重启后需要重新建立。")
    else:
        print("代理：没有运行")
        if current == s["local_base_url"]:
            print("现在 Codex 会连接不到本机代理。请启动修复，或者恢复直连。")
    print("自启状态请运行“07-开机自启”；你另外设置的后台启动不在这里检测。")
    print("配置只说明新加载配置的聊天会走哪里，已经打开的聊天可能仍使用旧地址。")


def records():
    path = Path(settings()["events_path"])
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("local_compaction"):
                rows.append(row)
    if not rows:
        print("还没有压缩记录。先在加载新配置的聊天中完成一次普通回复，再执行压缩。")
        return
    print("最近的压缩记录（北京时间）：")
    for row in rows[-10:]:
        stamp = row.get("timestamp", "")
        try:
            stamp = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%m-%d %H:%M:%S")
        except ValueError:
            pass
        usage = row.get("usage") or {}
        inputs = usage.get("input_tokens", 0)
        cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
        hit = f"{cached/inputs:.2%}" if inputs else "未报告"
        print(f"{stamp}  修补：{'是' if row.get('patched') else '否'}  输入：{inputs:,}  命中：{cached:,}  命中率：{hit}")
        if row.get("summary_error"):
            print("  摘要被拦截："+row["summary_error"])
        if not row.get("patched"):
            print("  原因："+row.get("reason", "未知"))
            if row.get("reason") == "no-snapshot":
                print("  没有匹配快照：先让这个聊天的一次普通请求经过代理。1.1 版会加密保存，供下次重启使用。")


def stop():
    service = health()
    ProviderRouteManager(ROOT/"settings.json").restore()
    if not service:
        print("已恢复直连，代理原本就没有运行。")
        return
    if not same_directory(service.get("directory")):
        raise RuntimeError("已恢复直连，但端口上的代理不属于这个目录，没有停止它。")
    pid = int(service["pid"])
    # Confirm process ownership before terminating the exact helper PID.
    command = (f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | "
               "Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, encoding="utf-8", timeout=10,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        process = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("已恢复直连，但无法确认代理进程，没有停止它。") from None
    allowed_python = {os.path.normcase(sys.executable),
                      os.path.normcase(str(Path(sys.executable).with_name("python.exe"))),
                      os.path.normcase(str(Path(sys.executable).with_name("pythonw.exe")))}
    same_python = os.path.normcase(process.get("ExecutablePath", "")) in allowed_python
    owned = any(str(ROOT/name).lower() in process.get("CommandLine", "").lower() for name in ("control.py", "proxy.py"))
    if not same_python or not owned:
        raise RuntimeError("已恢复直连，但进程信息不匹配，没有停止它。")
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not health():
            break
        time.sleep(0.1)
    print("已恢复直连，并停止本目录的代理。")


def main():
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
        elif hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "status", "restore", "records", "stop", "sync-provider", "set-sync-mode"))
    parser.add_argument("--mode", choices=tuple(SYNC_MODES))
    args = parser.parse_args()
    try:
        if args.action == "set-sync-mode":
            if not args.mode:
                raise ValueError("set-sync-mode 需要 --mode。")
            set_sync_mode(args.mode)
        else:
            {"start": start, "status": status, "restore": restore, "records": records,
             "stop": stop, "sync-provider": sync_provider}[args.action]()
    except (OSError, ValueError, RuntimeError, requests.RequestException) as error:
        # These messages contain no request headers or credential values.
        print("没有完成："+str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
