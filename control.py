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

ROOT = Path(__file__).resolve().parent


def settings():
    return json.loads((ROOT/"settings.json").read_text(encoding="utf-8-sig"))


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
        set_route(True)
        print("代理已经在运行，修复已启用，不会再开第二个代理。")
        return
    s = settings()
    server = make_server(s["upstream_base_url"], s["port"], s["events_path"], s.get("snapshot_path"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not health():
            raise RuntimeError("代理启动后没有通过健康检查，Codex 配置未切换。")
        set_route(True)
        print("代理已启动，Codex 修复已启用。", flush=True)
        print("地址："+s["local_base_url"], flush=True)
        print("上游："+s["upstream_base_url"], flush=True)
        print("这个窗口需要保持打开，可以最小化。", flush=True)
        print("按 Ctrl+C 退出时会恢复直连；也可以用“停止代理”脚本。", flush=True)
        thread.join()
    except KeyboardInterrupt:
        set_route(False)
        print("已恢复直连，正在停止代理。", flush=True)
    finally:
        server.shutdown()
        server.server_close()


def restore():
    set_route(False)
    print("已恢复原网关直连。模型、密钥和其他配置没有改动。")
    print("重新打开聊天或重启 Codex，让它加载这个配置。")


def status():
    s, _, _, config = configured()
    service = health()
    current = config["model_providers"][s["provider_id"]]["base_url"]
    print("模型："+config.get("model", "未知"))
    print("当前使用的供应商："+config.get("model_provider", "未知"))
    print("配置地址："+current)
    print("配置："+("修复已启用" if current == s["local_base_url"] else "原网关直连" if current == s["upstream_base_url"] else "地址已另行修改"))
    if service:
        print("代理：正在运行，进程 "+str(service.get("pid", "未知")))
        print("版本："+str(service.get("version", "未知")))
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
    set_route(False)
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
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "status", "restore", "records", "stop"))
    args = parser.parse_args()
    try:
        {"start": start, "status": status, "restore": restore, "records": records, "stop": stop}[args.action]()
    except (OSError, ValueError, RuntimeError, requests.RequestException) as error:
        # These messages contain no request headers or credential values.
        print("没有完成："+str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
