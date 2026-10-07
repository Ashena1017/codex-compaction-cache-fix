"""Enable the proxy against the currently selected Codex provider."""
from __future__ import annotations

import sys
from pathlib import Path

from layout import APP_DIR, SETTINGS_FILE

sys.path.insert(0, str(APP_DIR))

from route_sync import ProviderRouteManager  # noqa: E402
import control  # noqa: E402


def main():
    ProviderRouteManager(SETTINGS_FILE).discover()
    print("已读取当前 Codex 供应商。")
    print("正在启用代理；以后切换供应商会自动更新上游。")
    control.start()
    print("供应商自动跟随已启用。CCSwitch 更新后，再运行本脚本一次。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print("没有完成：" + str(error))
        raise SystemExit(1)
