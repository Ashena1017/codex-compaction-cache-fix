"""Create local settings without changing Codex's configuration or copying keys."""
import argparse
import json
import os
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent

def build_settings(config_path, upstream=None, provider_id=None, port=28615):
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    config_path = Path(config_path).expanduser().resolve()
    config = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    provider_id = provider_id or config.get("model_provider")
    provider = config.get("model_providers", {}).get(provider_id)
    if not isinstance(provider, dict) or not provider.get("base_url"):
        raise ValueError("A custom Responses provider with base_url is required")
    current = provider["base_url"]
    upstream = (upstream or current).rstrip("/")
    parsed = urlsplit(upstream)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use an HTTP(S) upstream URL without credentials, query or fragment")
    local = f"http://127.0.0.1:{port}/v1"
    if upstream == local:
        raise ValueError("Already routed through this proxy: provide the original --upstream")
    if current not in (upstream, local):
        raise ValueError("Upstream must match the current provider URL (or the provider must already use this proxy)")
    return {"upstream_base_url": upstream, "local_base_url": local,
            "provider_id": provider_id, "provider_sync_mode": "event", "port": port,
            "health_url": f"http://127.0.0.1:{port}/health",
            "config_path": str(config_path), "events_path": str(ROOT/"events.jsonl"),
            "snapshot_path": str(ROOT/"prefix-snapshots.dpapi")}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))/"config.toml")
    parser.add_argument("--upstream")
    parser.add_argument("--provider")
    parser.add_argument("--port", type=int, default=28615)
    args = parser.parse_args()
    destination = ROOT/"settings.json"
    if destination.exists():
        parser.exit(1, "settings.json already exists. Edit it, or move it aside before configuring again.\n")
    try:
        values = build_settings(args.config, args.upstream, args.provider, args.port)
    except (OSError, ValueError, TypeError):
        # TOML diagnostics can quote credential-bearing source lines.
        parser.exit(1, "Configuration failed. Check config path, custom provider, upstream URL and port.\n")
    destination.write_text(json.dumps(values, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("Created settings.json. Codex config is unchanged. Run: python control.py start")

if __name__ == "__main__":
    main()
