import json
import os
import shutil
import tomllib
from pathlib import Path

def read_config():
    settings_path = Path(__file__).with_name("settings.json")
    settings = json.loads(settings_path.read_text(encoding="utf-8-sig")) if settings_path.exists() else {}
    default = Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))/"config.toml"
    path = Path(settings.get("config_path", default))
    return tomllib.loads(path.read_text(encoding="utf-8-sig"))

def codex_binary(config):
    legacy = config.get("mcp_servers", {}).get("node_repl", {}).get("env", {}).get("CODEX_CLI_PATH")
    value = os.environ.get("CODEX_CLI_PATH") or legacy or shutil.which("codex")
    if not value:
        raise RuntimeError("Set CODEX_CLI_PATH to the Codex executable with app-server support")
    return Path(value)
