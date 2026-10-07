"""Follow the active Codex provider while keeping its client URL on loopback."""
from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import tomllib
from pathlib import Path
from urllib.parse import urlsplit


def validate_upstream(value, local_url):
    parsed = urlsplit(value)
    local = urlsplit(local_url)
    if (not isinstance(value, str) or parsed.scheme not in ("http", "https")
            or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment or value.rstrip("/") == local_url.rstrip("/")):
        raise RuntimeError("当前供应商地址无效或指向本机代理，未转发请求。")
    return value.rstrip("/")


def provider_section(text, provider_id):
    header = re.compile(r'(?m)^\[model_providers\.(?:"((?:[^"\\]|\\.)*)"|([^]\r\n]+))\]\s*$')
    matches = list(header.finditer(text))
    for index, match in enumerate(matches):
        name = json.loads('"'+match.group(1)+'"') if match.group(1) is not None else match.group(2).strip()
        if name == provider_id:
            start = match.end()
            end = matches[index+1].start() if index+1 < len(matches) else len(text)
            return match, start, end
    raise RuntimeError("当前启用的供应商没有标准的 [model_providers.*] 配置，已停止同步。")


class ProviderRouteManager:
    def __init__(self, settings_path):
        self.settings_path = Path(settings_path)
        self.lock = threading.RLock()
        self._signature = None
        self._upstream = None

    def changed(self):
        path = Path(self._settings()["config_path"])
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        with self.lock:
            changed = signature != self._signature
            self._signature = signature
            return changed

    def _settings(self):
        return json.loads(self.settings_path.read_text(encoding="utf-8-sig"))

    def _save_settings(self, settings):
        temporary = self.settings_path.with_name(self.settings_path.name+".new")
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        temporary.replace(self.settings_path)

    def _current(self, settings):
        path = Path(settings["config_path"])
        text = path.read_text(encoding="utf-8-sig")
        config = tomllib.loads(text)
        provider_id = config.get("model_provider")
        providers = config.get("model_providers", {})
        provider = providers.get(provider_id) if isinstance(providers, dict) else None
        if not isinstance(provider, dict) or not isinstance(provider.get("base_url"), str):
            raise RuntimeError("当前启用的供应商没有可识别的 base_url，已停止同步。")
        return path, text, provider_id, provider["base_url"]

    def _authorization_hash(self, settings, provider_id):
        config = tomllib.loads(Path(settings["config_path"]).read_text(encoding="utf-8-sig"))
        provider = config["model_providers"][provider_id]
        token = provider.get("experimental_bearer_token")
        if not isinstance(token, str) or not token:
            env_name = provider.get("env_key")
            token = os.environ.get(env_name, "") if isinstance(env_name, str) else ""
        if not token:
            return None
        return hashlib.sha256(("Bearer "+token).encode()).hexdigest()

    def discover(self):
        with self.lock:
            settings = self._settings()
            _, _, provider_id, current = self._current(settings)
            auth_hash = self._authorization_hash(settings, provider_id)
            if current.rstrip("/") == settings["local_base_url"].rstrip("/"):
                if settings.get("provider_id") != provider_id:
                    raise RuntimeError("本机代理地址对应的供应商与已保存上游不一致，已停止转发。")
                upstream = validate_upstream(settings["upstream_base_url"], settings["local_base_url"])
                if settings.get("authorization_sha256") != auth_hash:
                    settings["authorization_sha256"] = auth_hash
                    self._save_settings(settings)
                return upstream
            upstream = validate_upstream(current, settings["local_base_url"])
            if (settings.get("provider_id") != provider_id
                    or settings.get("upstream_base_url", "").rstrip("/") != upstream
                    or settings.get("authorization_sha256") != auth_hash):
                settings["provider_id"] = provider_id
                settings["upstream_base_url"] = upstream
                settings["authorization_sha256"] = auth_hash
                self._save_settings(settings)
            return upstream

    def expected_authorization(self):
        with self.lock:
            return self._settings().get("authorization_sha256")

    def sync(self):
        with self.lock:
            upstream = self.discover()
            settings = self._settings()
            path, text, provider_id, current = self._current(settings)
            if current.rstrip("/") == settings["local_base_url"].rstrip("/"):
                self._upstream = upstream
                return upstream
            match, start, end = provider_section(text, provider_id)
            section = text[start:end]
            updated, count = re.subn(r'(?m)^base_url\s*=\s*[^\r\n]+',
                                     "base_url = "+json.dumps(settings["local_base_url"]), section)
            if count != 1:
                raise RuntimeError("当前供应商的 base_url 格式无法安全修改，已停止同步。")
            result = text[:start]+updated+text[end:]
            parsed = tomllib.loads(result)
            if parsed["model_providers"][provider_id]["base_url"] != settings["local_base_url"]:
                raise RuntimeError("路由校验失败，Codex 配置未保存。")
            temporary = path.with_name(path.name+".compact-fix-new")
            temporary.write_text(result, encoding="utf-8")
            temporary.replace(path)
            stat = path.stat()
            self._signature = (stat.st_mtime_ns, stat.st_size)
            self._upstream = upstream
            return upstream

    def sync_if_changed(self):
        if self.changed():
            try:
                return self.sync()
            except (OSError, ValueError, RuntimeError):
                with self.lock:
                    self._signature = None
                raise
        return self._upstream

    def restore(self):
        with self.lock:
            upstream = self.discover()
            settings = self._settings()
            path, text, provider_id, current = self._current(settings)
            if current.rstrip("/") != settings["local_base_url"].rstrip("/"):
                return False
            match, start, end = provider_section(text, provider_id)
            section = text[start:end]
            updated, count = re.subn(r'(?m)^base_url\s*=\s*[^\r\n]+',
                                     "base_url = "+json.dumps(upstream), section)
            if count != 1:
                raise RuntimeError("当前供应商的 base_url 格式无法安全恢复。")
            parsed = tomllib.loads(text[:start]+updated+text[end:])
            if parsed["model_providers"][provider_id]["base_url"].rstrip("/") != upstream:
                raise RuntimeError("恢复路由校验失败，配置未保存。")
            temporary = path.with_name(path.name+".compact-fix-new")
            temporary.write_text(text[:start]+updated+text[end:], encoding="utf-8")
            temporary.replace(path)
            return True

