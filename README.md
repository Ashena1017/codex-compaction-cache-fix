> **本项目的修复代码由 AI（OpenAI Codex）实现，并由用户在实际使用中验证。** AI 编写的部分包括代理、重启快照、自启管理和测试。它是社区提供的临时修复，非 OpenAI 官方补丁。
>
> **AI-authored fix:** implemented with OpenAI Codex and verified by the user in real use. This is an unofficial community workaround.

# Codex Compaction Cache Fix

修复 Codex **本地压缩请求缺少工具前缀，导致 prompt cache 命中率接近 0%** 的问题。不需要替换 Codex 二进制：在本机运行一个 HTTP Responses 代理，恢复压缩请求中缺失的工具定义和 `parallel_tool_calls`。

针对 Codex 0.160.1、一个第三方 Responses 网关完成的两组 A/B 实验中，原始压缩均为 **0%**，修补后约 **99.46%**。Desktop 手动和自动压缩约 **99.67%**；重启代理后直接压缩为 **12,993 / 13,035 tokens，99.68%**。记录见 [evidence](evidence/README.md)。这些数字是特定环境下的实测结果，不是对所有网关的保证。

## 适用范围

- Windows，Python 3.11 或更新版本。
- Codex 配置使用自定义模型供应商，并有 `[model_providers.<名称>]` 和 `base_url`。
- HTTP Responses / Responses-lite 的本地压缩；请求带有明确的 compaction 元数据。
- 默认监听 `127.0.0.1:28615`，自动读取当前 Codex 供应商并转发到对应上游。切换供应商后无需手动改代理地址。
- 压缩记录标注实际上游域名；设置项和历史变化会记录诊断信息，但不再单独阻止恢复工具前缀。快照仍按账号、会话和模型隔离，恢复时沿用普通请求的工具格式。

当前没有验证官方 ChatGPT 登录链路，也不支持 WebSocket、压缩请求体或 Responses V2 远端压缩。内存代理代码可在其他系统运行，但持久快照和自启管理使用 Windows API；本仓库按 Windows 工具交付。

## 安装

### 下载 Windows 发行包

从 [Releases](https://github.com/Ashena1017/codex-compaction-cache-fix/releases/latest) 下载 Windows ZIP，完整解压后先运行 `setup.cmd`，再双击 `CodexCompactionFix.exe`。需要预先安装 Python 3.11 或更新版本（包含 Tcl/Tk 和 pip），首次安装需要联网；EXE 是启动器，未内置完整 Python。发行包按 `app`、`data`、`shortcuts`、`docs` 分类，不含任何个人配置、API Key、日志或会话快照。详细安装和升级步骤见 [发行包安装说明](docs/INSTALL.md)。

### 从源码安装

在 PowerShell 中执行：

```powershell
git clone https://github.com/Ashena1017/codex-compaction-cache-fix.git
cd codex-compaction-cache-fix
python -m pip install -r requirements.txt
python configure.py
```

`configure.py` 读取当前 Codex 配置，生成本地 `settings.json`，**不会修改 Codex 配置，也不会复制密钥**。默认读取 `%USERPROFILE%\.codex\config.toml`；设置了 `CODEX_HOME` 时使用该目录。

需要指定配置路径或端口时：

```powershell
python configure.py --config "D:\Codex\config.toml" --port 28616
```

如果配置已经指向本机代理，需要提供原网关地址：

```powershell
python configure.py --upstream "https://your-provider.example/v1"
```

已有 `settings.json` 时向导拒绝覆盖。可以手工编辑，或先移走再重新配置。也可以参考 [settings.example.json](settings.example.json)，但其中的占位路径和地址必须换成自己的值。不要把密钥写进这个文件。

## 使用

1. 双击 `01-启动修复.cmd`，或者执行 `python control.py start`。健康检查通过后，工具把当前供应商的 `base_url` 切换到本机代理。
2. 保留启动窗口，可以最小化。重新打开聊天或重启 Codex，让它加载配置。
3. 先让这个聊天的一次普通请求经过代理，建立工具快照；之后压缩时才有对应的前缀可恢复。
4. 双击 `05-查看压缩记录.cmd`，查看压缩请求的缓存数据。

CC Switch 切换供应商时会更新 Codex 配置。控制面板提供三种方式：定时检查；Windows 文件变更通知在 CC Switch 写入配置后触发；或由用户切换后手动点击同步。文件通知不修改 CC Switch 安装包，CC Switch 更新也不会覆盖联动。切换后通常需要重启 Codex 让客户端加载新配置。CC Switch 更新后运行一次 `08-CCSwitch更新后启用.cmd`，确认当前配置已经接入代理。

代理会从当前 Codex provider 的 `experimental_bearer_token` 或 `env_key` 对应环境变量计算 SHA-256 指纹，并在转发前校验 Authorization。`settings.json` 只保存指纹，不保存或记录密钥。旧 Codex 会话仍使用旧密钥时会被拒绝，避免把旧供应商凭据发往新上游。没有可验证密钥的 provider 会拒绝转发；请先确认它以支持的方式配置了 bearer token。

| 文件 | 用途 |
|---|---|
| `01-启动修复.cmd` | 启动代理并启用路由；重复启动会复用同目录的已有服务 |
| `02-检查状态.cmd` | 查看代理版本、路由和快照恢复情况 |
| `03-恢复直连.cmd` | 恢复当前供应商直连，保留代理运行 |
| `04-停止代理.cmd` | 恢复当前供应商直连并停止本目录的代理 |
| `05-查看压缩记录.cmd` | 查看最近十次压缩是否修补及命中率 |
| `06-本地自检.cmd` | 本地测试，不调用模型 |
| `07-开机自启.cmd` | 开启、关闭或查看当前安装目录的登录自启 |
| `08-CCSwitch更新后启用.cmd` | CC Switch 更新后检查当前供应商并启用自动跟随 |
| `09-打开控制面板.cmd` | 集中管理代理、自启和供应商同步方式 |

也可以运行 `python gui.py` 打开桌面控制面板，从单一窗口管理代理状态、启动/停止、恢复直连、供应商同步方式、自启、压缩记录、本地自检和实验。实验会实际调用模型并产生费用，运行前需要确认。

界面默认采用深灰背景、灰白文字和中性灰按钮。左侧导航分为概览、供应商同步、登录自启、诊断与实验、操作记录；左下角可切换日间模式并保存偏好。首页分别显示代理是否运行和 Codex 是否接入代理，完整输出放在操作记录页。关闭面板后，已经启动的代理继续运行。整理后的本机安装目录使用 `app`（程序）、`app/tests`（测试）、`data`（配置和运行记录）、`shortcuts`（备用 CMD 入口）、`docs`（说明）。`CodexCompactionFix.exe` 为套壳入口，仍需要 `data/settings.json` 中 `python_executable` 指定的 Python 环境和旁边的程序文件，并非独立便携版。启动器源码见 `shell_launcher.py`；构建时需安装 PyInstaller。

双击脚本使用 PATH 中的 `python`。安装依赖、配置和运行脚本时应使用同一个 Python 环境。

### 开机自启

双击 `07-开机自启.cmd`，选 `1` 开启，选 `2` 关闭，选 `3` 查看状态。也可执行：

```powershell
python autostart.py enable
python autostart.py status
python autostart.py disable
```

开启后，在任务计划程序注册一个当前 Windows 用户的登录任务，登录后等待 15 秒，以同一 Python 安装中的 `pythonw.exe` 在后台运行 `control.py start`。不要求保存 Windows 密码，不提升权限，也不立即启动第二个代理。任务名称含安装目录的哈希，避免覆盖另一份安装的任务。

关闭自启会删除这个任务，**不会停止当前代理或恢复直连**。要现在停用，请运行 `04-停止代理.cmd`。移动目录、更换 Python 前，应在旧位置关闭自启，再在新位置开启。

这是用户登录后的启动，不是开机前运行的 Windows 服务。启用自启后，登录时先等代理就绪再用 Codex；用 `02-检查状态.cmd` 确认。该工具只管理自己创建的任务，你以前手工设置的启动项需自行处理。

### 退出和升级

前台运行时按 Ctrl+C 会恢复直连。直接关闭控制台可能来不及恢复，可再运行 `03-恢复直连.cmd`。

Codex 更新通常不会覆盖这个独立目录，但协议变化可能使修补失效。更新本工具后保留本地 `settings.json` 和快照，停止代理再重新启动，加载新代码。检查状态和一次真实压缩记录，确认新版本仍适用。

## 修复原理

普通请求的工具定义参与缓存前缀。本地压缩会去掉 `tools`，并改变 `parallel_tool_calls`，使压缩与普通请求的模型可见前缀不同。

代理在普通请求经过时记住工具定义、并行设置、历史 item 哈希和前缀设置。只有明确的本地压缩，且账号、聊天和模型匹配时，才补回工具定义；历史和设置差异会写入诊断日志，但不会阻止恢复，因为压缩请求可能重写或裁剪历史，而工具定义位于请求前部。Responses-lite 会恢复开头的 `additional_tools` 条目，包括原 ID；如果整个条目被省略，就插回输入开头，保留所有历史消息和现有的顶层原生工具。已有 `additional_tools` 工具定义的请求不重复补；标准 Responses 只补回缺失的顶层工具。

1.3.2 修正了 Responses-lite 压缩省略整个工具条目时被误判为 `prefix-mode-changed` 的问题。日志的 `prefix_action: inserted` 表示补回整个条目；`replaced` 表示补回空工具列表。验证包含本地回归测试和模拟 HTTP/SSE 转发，测试通过不等于已验证上游实际缓存命中率。

补回工具后，压缩的 SSE 响应先缓冲，确认完成、包含非空 assistant 摘要、没有工具调用或其他异常输出后再交给 Codex。校验失败返回 502，避免把异常输出当成摘要。普通回复保持流式转发。

**压缩后的首个普通请求仍可能 0% 命中。** 此时历史已经变成摘要；要看本次修复是否有效，应查看压缩请求自己的 usage。

## 快照和日志

- 工具快照用当前 Windows 用户的 DPAPI 加密，保存到 `prefix-snapshots.dpapi`。不保存原始密钥或聊天正文；保存工具定义、历史哈希和匹配信息。工具定义本身可能含本机或插件描述，因此快照也不应公开。
- 快照有效期 24 小时，最多 256 份。启动只恢复有效项；后续有效请求清理过期项并保存。没有定时清理线程，也不会随会话归档立即删除。
- 本机快照的 24 小时有效期与上游 prompt cache 的保存时间不同。恢复快照不能恢复已经过期的上游缓存。
- `events.jsonl` 记录 token 统计、模型、修补原因和哈希会话标签，不记录请求密钥或聊天正文。
- `settings.json`、快照、运行日志和新实验结果均已列入 `.gitignore`。

日志里的 `patched: true` 说明补回了工具前缀，不等于上游一定命中缓存。`no-snapshot` 表示没有有效快照；`tool-prefix-restored-history-changed` 表示恢复时发现压缩历史与快照不同，`shared_items`、`snapshot_items`、`current_items` 可用于诊断。

## 验证与实验

本地测试不调用模型：

```powershell
python -m unittest -v
```

覆盖前缀恢复、账号和聊天隔离、HTTP/SSE 转发、摘要校验、DPAPI 重启恢复、失效快照、供应商切换与新上游转发、Windows 文件通知、配置修改和自启管理。GitHub Actions 在 Windows Python 3.11 / 3.14 上运行本地测试。

以下脚本**会调用你的模型供应商，产生费用**。日常使用无需执行：

```powershell
# 合成历史的两组 A/B，共 6 次请求
python experiment.py

# Desktop/CLI app-server 的手动与自动压缩、继续回复
$env:CODEX_CLI_PATH = "C:\path\to\codex.exe"
python runtime_smoke.py

# 普通请求 → 重建代理 → 直接压缩 → 继续回复
python restart_smoke.py
```

实验读取当前配置中的 `experimental_bearer_token` 或 `env_key` 指定的环境变量。`CODEX_CLI_PATH` 要指向支持 `app-server --stdio` 的真实可执行文件。真实客户端实验使用临时合成会话和独立端口；不会重启日常代理。`runtime_smoke.py --installed` 则用于验证已经配置好的实际代理路由。

## 已有讨论与修复

- [openai/codex #37305](https://github.com/openai/codex/issues/37305)：报告本地压缩缺少工具定义和并行设置，导致缓存前缀失配。
- [Dirard/codex 源码修复](https://github.com/Dirard/codex/commit/46e037ba61df6e3b3c60054b10239111c57dc4eb)：直接在 Codex 中保留本地压缩工具前缀。
- [oh-my-pi #10789](https://github.com/can1357/oh-my-pi/pull/10789)：另一个客户端中与压缩序列化有关的缓存修复。

这里采用代理方案，方便继续使用原来的 Desktop 安装。以上链接用于说明相关问题；本项目没有复制这些仓库的代码。

MIT License.
