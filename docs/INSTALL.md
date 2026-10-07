# Windows Release 安装说明

本项目的修复代码由 AI（OpenAI Codex）实现，用户已在真实会话中验证缓存命中。本工具是非官方修复。

## 第一次安装

1. 安装 Windows 版 Python 3.11 或更新版本。推荐从 https://www.python.org/downloads/windows/ 安装，保留 Tcl/Tk 和 pip；没有 `py` 启动器时需将 Python 加入 PATH。
2. 把 ZIP 完整解压到固定目录，例如 `D:\Tools\CodexCompactionFix`。不要直接在压缩包里运行。
3. 先确认 Codex 已配置一个自定义 Responses 供应商，地址为真实网关地址。
4. 双击 `setup.cmd`。它会创建 `runtime` 虚拟环境，从 PyPI 安装 requests 及其依赖，并生成 `data/settings.json`。首次安装需要联网。这个步骤不修改 Codex 配置，也不复制 API Key。
5. 双击 `CodexCompactionFix.exe`，在概览中点击“启动修复”。启动并通过健康检查后，才将 Codex 地址切到本机代理。
6. 重启 Codex，先进行一次普通对话，再测试压缩。在“诊断与实验”中查看压缩记录。

EXE 是窗口启动器；此版本**没有内置完整 Python**。安装后仍依赖最初安装的 Python，请保留它。整个程序目录也要保留。关闭控制面板后代理继续运行。

自定义配置位置或原上游地址时，在解压目录打开终端，例如：

```powershell
py -3 app/release_setup.py --config "D:\Codex\config.toml" --upstream "https://your-provider.example/v1"
```

如果当前 Codex 已经使用另一份工具的本机代理，建议先在旧面板“停止代理”，恢复直连后再安装。安装脚本不会猜测原网关。默认端口为 28615，不要同时启动两份代理。

## 已经使用旧版

只调整界面时，可以保留旧位置，更新 `app` 中同名程序文件。更新前关闭面板；更新代理代码前在面板停止代理，更新完成后启动。

这个发行版从干净源码组装，没有任何个人配置。不要用发行包覆盖 `data`。`setup.cmd` 遇到已有设置会拒绝覆盖。要迁移到新目录，先在旧面板关闭登录自启并停止代理，再在新目录安装；新面板中重新开启自启。旧目录快照可在同一 Windows 用户下保留，但不能公开上传。

## 文件位置

- `CodexCompactionFix.exe`：打开控制面板。
- `setup.cmd`：首次安装。
- `app`：程序和本地测试。
- `data`：安装后生成的设置、日志和加密快照；不要分享。
- `runtime`：安装后生成的 Python 虚拟环境。
- `shortcuts`：备用命令入口。
- `docs`：说明、修复原理和公开实验记录。

供应商同步有定时检查、Windows 文件通知和手动同步三种方式。文件通知不修改 CC Switch。登录自启可在面板启用或关闭。实验会调用模型并产生费用，日常使用无需运行。

## 本地检查

安装后双击 `shortcuts/06-本地自检.cmd`，或者在解压目录执行：

```powershell
runtime/Scripts/python.exe -m unittest discover -v -s app/tests -t app
```

这些测试不调用模型。下载页的 `SHA256SUMS.txt` 可核对 ZIP 完整性：

```powershell
Get-FileHash .\CodexCompactionFix-1.2.0-windows.zip -Algorithm SHA256
```
