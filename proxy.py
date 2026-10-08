"""Loopback Responses relay that preserves the tool prefix for local compaction.

Credentials and conversation text are never written to logs. Compaction output is
withheld until completion, nonempty assistant text, and no tool calls are verified.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import threading
import time
from collections import Counter, OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import requests
from snapshot_store import SnapshotStore
from layout import INSTALL_DIR

VERSION = "1.3.1"
UNCHANGED = object()
MAX_BODY = 64 * 1024 * 1024
MAX_SUMMARY_STREAM = 16 * 1024 * 1024
HOP_HEADERS = {"host", "connection", "keep-alive", "transfer-encoding",
               "content-length", "proxy-authorization", "proxy-authenticate",
               "te", "trailer", "upgrade", "content-encoding"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def metadata(body, headers):
    lowered = {k.lower(): v for k, v in headers.items()}
    client = body.get("client_metadata") or {}
    raw = client.get("x-codex-turn-metadata") or lowered.get("x-codex-turn-metadata")
    try:
        meta = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (ValueError, TypeError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    thread = meta.get("thread_id") or client.get("thread_id") or lowered.get("thread_id")
    return meta, thread


def local_compaction(meta):
    compaction = meta.get("compaction") or {}
    return (meta.get("request_kind") == "compaction"
            and isinstance(compaction, dict)
            and compaction.get("implementation") == "responses")


def visible_hash(item):
    if not isinstance(item, dict):
        return digest(item)
    # IDs/status and client bookkeeping are not model-visible message content.
    value = {k: v for k, v in item.items() if k not in
             ("id", "status", "metadata", "internal_chat_message_metadata_passthrough")}
    return digest(value)


def tool_prefix(body):
    items = body.get("input")
    if isinstance(items, list) and items and isinstance(items[0], dict) and items[0].get("type") == "additional_tools":
        return "lite", items[0], items[1:]
    return "standard", body.get("tools"), items


class PrefixCache:
    def __init__(self, ttl=86400, capacity=256, storage_path=None, namespace=""):
        self.ttl = ttl
        self.capacity = capacity
        self.lock = threading.Lock()
        self.snapshots = OrderedDict()
        self.store = SnapshotStore(storage_path) if storage_path else None
        self.namespace = digest(namespace)
        self.restored = 0
        self.storage_error = None
        if self.store:
            try:
                saved = self.store.load()
                if saved is not None:
                    if saved.get("version") != 1 or saved.get("namespace") != self.namespace:
                        raise ValueError("Snapshot namespace changed")
                    now = time.time()
                    for key, snapshot in saved["snapshots"][-self.capacity:]:
                        if (isinstance(key, str) and len(key) == 64
                                and isinstance(snapshot, dict)
                                and 0 <= now-snapshot["time"] <= self.ttl
                                and snapshot.get("mode") in ("lite", "standard")
                                and isinstance(snapshot.get("hashes"), list)
                                and isinstance(snapshot.get("settings"), str)
                                and isinstance(snapshot.get("parallel"), bool)):
                            self.snapshots[key] = snapshot
                    self.restored = len(self.snapshots)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                self.snapshots.clear()
                self.storage_error = "snapshot-load-failed"

    def persist(self):
        if not self.store:
            return
        try:
            self.store.save({"version": 1, "namespace": self.namespace,
                             "snapshots": list(self.snapshots.items())})
            self.storage_error = None
        except (OSError, ValueError, TypeError):
            self.storage_error = "snapshot-save-failed"

    def status(self):
        with self.lock:
            now = time.time()
            return {"persistent": bool(self.store), "restored_at_start": self.restored,
                    "available": sum(0 <= now-s["time"] <= self.ttl for s in self.snapshots.values()),
                    "ttl_seconds": self.ttl, "error": self.storage_error}

    def prepare(self, body, headers):
        meta, thread = metadata(body, headers)
        compact = local_compaction(meta)
        kind = meta.get("request_kind", "unknown")
        note = {"request_kind": kind, "local_compaction": compact, "patched": False}
        auth = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
        if not thread or not auth or not body.get("model"):
            note["reason"] = "missing-identity"
            return body, note
        key = digest({"auth": hashlib.sha256(auth.encode()).hexdigest(),
                      "thread": str(thread), "model": str(body["model"])})
        note["session_tag"] = key[:16]
        mode, tools, items = tool_prefix(body)
        if not isinstance(items, list) or not items:
            note["reason"] = "non-full-input"
            return body, note
        hashes = [visible_hash(item) for item in items]
        legacy_settings = digest({k: body.get(k) for k in ("instructions", "reasoning", "text")})
        setting_values = {k: digest(body.get(k)) for k in ("instructions", "reasoning", "text")}
        available = tools.get("tools") if mode == "lite" and isinstance(tools, dict) else tools
        with self.lock:
            now = time.time()
            expired = [k for k, v in self.snapshots.items() if not 0 <= now-v["time"] <= self.ttl]
            for expired_key in expired:
                del self.snapshots[expired_key]
            if kind == "turn" and available:
                self.snapshots[key] = {"tools": copy.deepcopy(tools), "hashes": hashes,
                                       "mode": mode, "settings": legacy_settings,
                                       "settings_fields": setting_values,
                                       "parallel": body.get("parallel_tool_calls", True), "time": now}
                self.snapshots.move_to_end(key)
                while len(self.snapshots) > self.capacity:
                    self.snapshots.popitem(last=False)
                self.persist()
                note["reason"] = "snapshot-saved"
                return body, note
            if expired:
                self.persist()
            if not compact:
                note["reason"] = "not-local-compaction"
                return body, note
            snapshot = self.snapshots.get(key)
            if not snapshot:
                note["reason"] = "no-snapshot"
                return body, note
            if mode != snapshot["mode"]:
                note["reason"] = "prefix-mode-changed"
                return body, note
            previous_settings = snapshot.get("settings_fields")
            if isinstance(previous_settings, dict):
                changed_settings = sorted(k for k, value in setting_values.items()
                                          if previous_settings.get(k) != value)
            else:
                changed_settings = ["legacy-settings"] if legacy_settings != snapshot.get("settings") else []
            common = 0
            for previous, current in zip(snapshot["hashes"], hashes):
                if previous != current:
                    break
                common += 1
            note["shared_items"] = common
            history_matches = common == len(snapshot["hashes"])
            note.update(history_matches=history_matches,
                        snapshot_items=len(snapshot["hashes"]), current_items=len(hashes))
            if available:
                note["reason"] = "tools-already-present"
                return body, note
            patched = copy.deepcopy(body)
            if mode == "lite":
                patched["input"][0] = copy.deepcopy(snapshot["tools"])
            else:
                patched["tools"] = copy.deepcopy(snapshot["tools"])
            patched["parallel_tool_calls"] = snapshot["parallel"]
            note.update(patched=True,
                        reason=("tool-prefix-restored-history-changed" if not history_matches else
                                "tool-prefix-restored-settings-changed" if changed_settings else
                                "tool-prefix-restored"),
                        changed_settings=changed_settings, mode=mode,
                        tool_count=len(snapshot["tools"].get("tools", [])) if mode == "lite" else len(snapshot["tools"]))
            return patched, note


def summary_error(response):
    if not isinstance(response, dict):
        return "invalid-summary-response"
    if response.get("status") not in (None, "completed"):
        return "summary-response-not-completed"
    output = response.get("output") or []
    if not isinstance(output, list):
        return "invalid-summary-output"
    text = []
    for item in output:
        if not isinstance(item, dict):
            return "invalid-summary-output"
        kind = item.get("type")
        if kind not in ("message", "reasoning"):
            return "summary-contained-tool-or-unexpected-output"
        if kind == "message" and item.get("role") == "assistant":
            text.extend(part.get("text", "") for part in item.get("content", [])
                        if part.get("type") == "output_text")
    if not "".join(text).strip():
        return "summary-empty"
    return None


class SSEInspector:
    def __init__(self):
        self.pending = b""
        self.response = None
        self.error = None
        self.usage = None
        self.types = set()

    def feed(self, chunk):
        self.pending += chunk
        # Accept LF and CRLF and arbitrary network chunk boundaries.
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if not line.startswith(b"data:"):
                continue
            raw = line[5:].strip()
            if raw == b"[DONE]":
                continue
            try:
                event = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                self.error = "invalid-sse-json"
                continue
            if not isinstance(event, dict):
                self.error = "invalid-sse-event"
                continue
            kind = event.get("type", "")
            if kind in ("response.output_item.added", "response.output_item.done"):
                item_type = (event.get("item") or {}).get("type")
                self.types.add(item_type)
                if item_type not in ("message", "reasoning"):
                    self.error = "summary-contained-tool-or-unexpected-output"
            if kind == "response.completed":
                self.response = event.get("response") or {}
                self.usage = self.response.get("usage") if isinstance(self.response, dict) else None
            if kind in ("response.failed", "response.incomplete", "error"):
                self.error = "summary-stream-failed"

    def finish(self):
        if self.pending.strip():
            self.feed(b"\n")
        if self.error:
            return self.error
        if self.response is None:
            return "summary-stream-missing-completion"
        return summary_error(self.response)


class State:
    def __init__(self, upstream, events=None, snapshot_path=None):
        self.upstream = upstream.rstrip("/")
        self.cache = PrefixCache(storage_path=snapshot_path, namespace=self.upstream)
        self.events = Path(events) if events else None
        self.lock = threading.Lock()
        self.upstream_lock = threading.RLock()
        self.sync_callback = None
        self.expected_authorization = None
        self.counters = Counter()
        self.recent = []

    def set_upstream(self, upstream, expected_authorization=UNCHANGED):
        parsed = urlsplit(upstream)
        if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Invalid upstream base URL")
        value = upstream.rstrip("/")
        with self.upstream_lock:
            if value != self.upstream:
                self.upstream = value
                self.cache = PrefixCache(storage_path=self.cache.store.path if self.cache.store else None,
                                         namespace=value)
            if expected_authorization is not UNCHANGED:
                self.expected_authorization = expected_authorization

    def record(self, note):
        clean = {k: v for k, v in note.items() if k in
                 ("request_kind", "local_compaction", "patched", "reason", "shared_items",
                  "history_matches", "snapshot_items", "current_items",
                  "mode", "tool_count", "status", "elapsed_ms", "usage", "summary_error", "model", "session_tag",
                  "provider", "changed_settings")}
        clean["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self.lock:
            self.counters[clean.get("reason", "request")] += 1
            if clean.get("summary_error"):
                self.counters["summary-rejected"] += 1
            self.recent.append(clean)
            self.recent = self.recent[-40:]
            if self.events:
                self.events.parent.mkdir(parents=True, exist_ok=True)
                with self.events.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(clean, ensure_ascii=False)+"\n")

    def metrics(self):
        with self.lock:
            return {"version": VERSION, "counters": dict(self.counters), "recent": self.recent.copy()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CodexCompactionProxy/"+VERSION

    def log_message(self, *args):
        pass

    def json_reply(self, status, value):
        body = encode(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self.json_reply(200, {"ok": True, "version": VERSION, "pid": os.getpid(),
                                         "directory": str(INSTALL_DIR),
                                         "snapshots": self.server.state.cache.status()})
        if self.path == "/metrics":
            return self.json_reply(200, self.server.state.metrics())
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        state = self.server.state
        started = time.monotonic()
        note = {"reason": "passthrough", "patched": False}
        try:
            if state.sync_callback:
                try:
                    state.sync_callback()
                except (OSError, ValueError, RuntimeError):
                    note["reason"] = "provider-route-sync-failed"
                    self.json_reply(503, {"error": {"message": "Provider route could not be verified; request was not forwarded"}})
                    return
            with state.upstream_lock:
                upstream = state.upstream
                cache = state.cache
                expected_auth = state.expected_authorization
            provider = urlsplit(upstream).hostname
            supplied_auth = self.headers.get("Authorization", "")
            if not expected_auth:
                note["reason"] = "provider-credential-unverifiable"
                self.json_reply(503, {"error": {"message": "Provider credential cannot be verified; request was not forwarded"}})
                return
            if hashlib.sha256(supplied_auth.encode()).hexdigest() != expected_auth:
                note["reason"] = "provider-credential-mismatch"
                self.json_reply(401, {"error": {"message": "Credential does not match the active Codex provider"}})
                return
            if self.headers.get("Origin"):
                return self.json_reply(403, {"error": {"message": "Browser-origin requests are unsupported"}})
            if not self.headers.get("Authorization"):
                return self.json_reply(401, {"error": {"message": "Upstream authorization is required"}})
            if self.headers.get("Upgrade"):
                return self.json_reply(426, {"error": {"message": "Use HTTP Responses; WebSocket proxying is unsupported"}})
            if self.headers.get("Transfer-Encoding"):
                return self.json_reply(400, {"error": {"message": "Send a Content-Length request"}})
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY:
                return self.json_reply(413, {"error": {"message": "Request body exceeds proxy limit"}})
            raw = self.rfile.read(length) if length else None
            path = urlsplit(self.path)
            if path.scheme or path.netloc or ".." in path.path.split("/"):
                return self.json_reply(400, {"error": {"message": "Invalid relative request path"}})
            # The configured public base ends in /v1; retain that base on forwarding.
            suffix = self.path[3:] if self.path.startswith("/v1/") else self.path
            if self.command == "POST" and path.path.endswith("/responses"):
                if self.headers.get("Content-Encoding") not in (None, "identity"):
                    return self.json_reply(415, {"error": {"message": "Compressed requests are unsupported"}})
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    return self.json_reply(400, {"error": {"message": "Expected a JSON object"}})
                body, note = cache.prepare(body, dict(self.headers))
                note["provider"] = provider
                note["model"] = body.get("model")
                if note["patched"]:
                    raw = encode(body)
            compact = note.get("local_compaction", False)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
            headers["Accept-Encoding"] = "identity"
            with requests.Session() as session:
                with session.request(self.command, upstream+suffix, data=raw, headers=headers,
                                     stream=True, timeout=(30, 600), allow_redirects=False) as response:
                    note["status"] = response.status_code
                    if compact and response.status_code == 200:
                        buffered = bytearray()
                        is_sse = "text/event-stream" in response.headers.get("Content-Type", "")
                        inspector = SSEInspector() if is_sse else None
                        for chunk in response.iter_content(chunk_size=None):
                            buffered.extend(chunk)
                            if len(buffered) > MAX_SUMMARY_STREAM:
                                note["summary_error"] = "summary-stream-too-large"
                                break
                            if inspector:
                                inspector.feed(chunk)
                                if inspector.error:
                                    note["summary_error"] = inspector.error
                                    break
                        if not note.get("summary_error"):
                            if inspector:
                                note["summary_error"] = inspector.finish()
                                note["usage"] = inspector.usage
                            else:
                                result = json.loads(buffered)
                                note["summary_error"] = summary_error(result)
                                note["usage"] = result.get("usage") if isinstance(result, dict) else None
                        if note.get("summary_error"):
                            note["status"] = 502
                            return self.json_reply(502, {"error": {"type": "compaction_validation_error",
                                                   "message": note["summary_error"]}})
                        return self.relay(response, [bytes(buffered)])
                    return self.relay(response, response.iter_content(chunk_size=None))
        except (BrokenPipeError, ConnectionResetError):
            note["reason"] = "client-disconnected"
        except (ValueError, requests.RequestException, OSError) as error:
            # Never include exception strings: libraries can embed auth/URLs or response text.
            note["reason"] = "proxy-error-"+type(error).__name__
            note["status"] = 502
            try:
                self.json_reply(502, {"error": {"message": note["reason"]}})
            except OSError:
                pass
        finally:
            note["elapsed_ms"] = round((time.monotonic()-started)*1000)
            state.record(note)

    def relay(self, response, chunks):
        self.send_response(response.status_code)
        for key, value in response.headers.items():
            if key.lower() not in HOP_HEADERS:
                self.send_header(key, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in chunks:
            if chunk:
                self.wfile.write(f"{len(chunk):X}\r\n".encode()+chunk+b"\r\n")
                self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def make_server(upstream, port=28615, events=None, snapshot_path=None):
    parsed = urlsplit(upstream)
    if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Invalid upstream base URL")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.state = State(upstream, events, snapshot_path)
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", required=True)
    args = parser.parse_args()
    settings = json.loads(Path(args.settings).read_text(encoding="utf-8-sig"))
    server = make_server(settings["upstream_base_url"], settings.get("port", 28615),
                         settings.get("events_path"), settings.get("snapshot_path"))
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
