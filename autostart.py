"""Manage a per-user logon task for this installation; never starts it immediately."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from layout import INSTALL_DIR

ROOT = INSTALL_DIR

# Installation-specific names avoid changing another copy's startup task.
def task_name(root=ROOT):
    identity = os.path.normcase(str(Path(root).resolve()))
    return "CodexCompactFix-"+hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder = $service.GetFolder('\')
    $existing = $null
    try { $existing = $folder.GetTask($env:COMPACT_FIX_TASK_NAME) }
    catch { if ($_.Exception.HResult -ne -2147024894) { throw } }
    if ($null -ne $existing) {
        $actions = $existing.Definition.Actions
        if ($actions.Count -ne 1 -or $actions.Item(1).Type -ne 0 -or
            $actions.Item(1).Path -ne $env:COMPACT_FIX_PYTHON -or
            $actions.Item(1).Arguments -ne $env:COMPACT_FIX_ARGUMENTS -or
            $actions.Item(1).WorkingDirectory -ne $env:COMPACT_FIX_ROOT) {
            throw 'Task identity mismatch'
        }
    }
    if ($env:COMPACT_FIX_ACTION -eq 'enable') {
        $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $task = $service.NewTask(0)
        $task.RegistrationInfo.Description = 'Codex compaction cache fix: start this installation after user logon.'
        $task.Principal.UserId = $user
        $task.Principal.LogonType = 3
        $task.Principal.RunLevel = 0
        $task.Settings.Enabled = $true
        $task.Settings.StartWhenAvailable = $true
        $task.Settings.DisallowStartIfOnBatteries = $false
        $task.Settings.StopIfGoingOnBatteries = $false
        $task.Settings.ExecutionTimeLimit = 'PT0S'
        $task.Settings.MultipleInstances = 2
        $trigger = $task.Triggers.Create(9)
        $trigger.UserId = $user
        $trigger.Delay = 'PT15S'
        $action = $task.Actions.Create(0)
        $action.Path = $env:COMPACT_FIX_PYTHON
        $action.Arguments = $env:COMPACT_FIX_ARGUMENTS
        $action.WorkingDirectory = $env:COMPACT_FIX_ROOT
        $registered = $folder.RegisterTaskDefinition($env:COMPACT_FIX_TASK_NAME, $task, 6, $user, $null, 3)
        if (-not $registered.Enabled) { throw 'Task registration disabled' }
        $enabled = $true
    } elseif ($env:COMPACT_FIX_ACTION -eq 'disable') {
        if ($null -ne $existing) { $folder.DeleteTask($env:COMPACT_FIX_TASK_NAME, 0) }
        $enabled = $false
    } else {
        $enabled = $null -ne $existing -and $existing.Enabled
    }
    @{ok=$true; enabled=[bool]$enabled; task=$env:COMPACT_FIX_TASK_NAME} | ConvertTo-Json -Compress
} catch {
    @{ok=$false; error='task-operation-failed'; code=$_.Exception.HResult} | ConvertTo-Json -Compress
    exit 1
}
'''


def manage(action, root=ROOT):
    if os.name != "nt":
        raise RuntimeError("此功能需要 Windows 任务计划程序。")
    if action not in ("enable", "disable", "status"):
        raise ValueError("未知操作。")
    root = Path(root).resolve()
    app_dir = root/"app" if (root/"app").is_dir() else root
    data_dir = root/"data" if (root/"data").is_dir() else root
    settings_file = data_dir/"settings.json"
    control_file = app_dir/"control.py"
    if action == "enable" and not (control_file.is_file() and settings_file.is_file()):
        raise RuntimeError("安装文件不完整：缺少 control.py 或 settings.json。")
    settings = json.loads(settings_file.read_text(encoding="utf-8-sig")) if settings_file.is_file() else {}
    configured_python = Path(settings.get("python_executable") or sys.executable)
    background_python = configured_python.with_name("pythonw.exe")
    if not background_python.is_file():
        raise RuntimeError("当前 Python 安装缺少 pythonw.exe，无法创建无窗口的自启任务。")
    env = dict(os.environ, COMPACT_FIX_ACTION=action, COMPACT_FIX_ROOT=str(root),
               COMPACT_FIX_TASK_NAME=task_name(root), COMPACT_FIX_PYTHON=str(background_python),
               COMPACT_FIX_ARGUMENTS=subprocess.list2cmdline([str(control_file), "start"]))
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        value = json.loads(result.stdout.strip())
    except ValueError:
        raise RuntimeError("任务计划程序没有返回有效结果。") from None
    if result.returncode or not value.get("ok"):
        raise RuntimeError("任务计划操作失败（代码 "+str(value.get("code", "未知"))+"）。请检查当前用户的任务计划权限或同名任务是否被改动。")
    return value


def run_action(action):
    value = manage(action)
    print("开机自启："+("已开启" if value["enabled"] else "已关闭"))
    print("任务名称："+value["task"])
    if action == "enable":
        print("下次登录当前 Windows 账号后，等待 15 秒启动。现在不会另开代理。")
        print("代理由任务计划程序运行，不会主动弹出启动窗口；可用“检查状态”确认。")
        print("移动目录或更换 Python 前，先关闭自启，再从新位置开启。")
    elif action == "disable":
        print("只取消以后登录时的自动启动，当前代理继续运行。")
    return 0


def menu():
    run_action("status")
    print("\n1. 开启登录后自启\n2. 关闭登录后自启\n3. 只查看状态\n4. 退出")
    actions = {"1": "enable", "2": "disable", "3": "status"}
    while True:
        selected = input("请选择 [1-4]：").strip()
        if selected == "4":
            return 0
        if selected in actions:
            return run_action(actions[selected])
        print("请输入 1、2、3 或 4。")


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="menu", choices=("enable", "disable", "status", "menu"))
    args = parser.parse_args()
    try:
        return menu() if args.action == "menu" else run_action(args.action)
    except EOFError:
        return 0
    except KeyboardInterrupt:
        print("\n已退出。")
        return 130
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print("没有完成："+str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
