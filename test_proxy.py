import copy
import hashlib
import os
import time
import json
import threading
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from proxy import PrefixCache, SSEInspector, encode, make_server, summary_error
from control import change_provider_url
from snapshot_store import SnapshotStore
import control


def body(kind="turn", lite=False):
    tools = [{"type": "function", "name": "read_test", "description": "Read synthetic test data",
              "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}]
    inputs = [{"role": "user", "content": "synthetic history"}]
    if lite:
        inputs.insert(0, {"type": "additional_tools", "id": "at_test", "role": "developer", "tools": tools})
    result = {"model": "test-model", "instructions": "Be concise", "input": inputs,
              "tools": None if lite else tools, "parallel_tool_calls": not lite,
              "reasoning": {"effort": "medium"}, "store": False,
              "client_metadata": {"thread_id": "thread-test", "x-codex-turn-metadata": json.dumps(
                  {"thread_id": "thread-test", "request_kind": kind,
                   "compaction": {"implementation": "responses"}})}}
    if kind == "compaction":
        if lite:
            result["input"][0] = {"type": "additional_tools", "id": "at_empty", "role": "developer", "tools": []}
        else:
            result["tools"] = []
        result["parallel_tool_calls"] = False
        result["input"].append({"role": "user", "content": "Summarize without tools"})
    return result


def summary(text="A valid synthetic summary."):
    return {"id": "resp-test", "status": "completed", "output": [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 1234, "input_tokens_details": {"cached_tokens": 1100}}}


def sse(response, extra=None):
    events = [{"type": "response.created", "response": {"id": "resp-test"}}]
    if extra:
        events.append(extra)
    events.append({"type": "response.completed", "response": response})
    return b"".join(b"data: "+encode(event)+b"\r\n\r\n" for event in events)


class PrefixTests(unittest.TestCase):
    def setUp(self):
        self.cache = PrefixCache()
        self.headers = {"Authorization": "Bearer unit-test"}
        self.cache.prepare(body(), self.headers)

    def test_restore_exact_tools_and_parallel_without_changing_history(self):
        original = body("compaction")
        fixed, note = self.cache.prepare(original, self.headers)
        self.assertTrue(note["patched"])
        self.assertEqual(fixed["tools"], body()["tools"])
        self.assertTrue(fixed["parallel_tool_calls"])
        self.assertEqual(fixed["input"], original["input"])
        self.assertEqual(original["tools"], [])

    def test_no_cross_account_snapshot(self):
        _, note = self.cache.prepare(body("compaction"), {"Authorization": "Bearer other-test"})
        self.assertEqual(note["reason"], "no-snapshot")

    def test_no_cross_thread_snapshot(self):
        compact = body("compaction")
        compact["client_metadata"]["x-codex-turn-metadata"] = json.dumps(
            {"thread_id": "other-thread", "request_kind": "compaction", "compaction": {"implementation": "responses"}})
        _, note = self.cache.prepare(compact, self.headers)
        self.assertEqual(note["reason"], "no-snapshot")

    def test_settings_changes_are_logged_but_do_not_block_tool_restore(self):
        for field, value in (("instructions", "changed"), ("reasoning", {"effort": "low"}),
                             ("text", {"format": {"type": "json_object"}})):
            compact = body("compaction")
            compact[field] = value
            fixed, note = self.cache.prepare(compact, self.headers)
            self.assertTrue(note["patched"])
            self.assertIn(field, note["changed_settings"])
            self.assertEqual(fixed["tools"], body()["tools"])
        legacy = next(iter(self.cache.snapshots.values()))
        legacy.pop("settings_fields")
        compact = body("compaction")
        compact["instructions"] = "changed after upgrade"
        fixed, note = self.cache.prepare(compact, self.headers)
        self.assertTrue(note["patched"])
        self.assertIn("legacy-settings", note["changed_settings"])
        compact = body("compaction")
        compact["input"][0]["content"] = "changed history"
        fixed, note = self.cache.prepare(compact, self.headers)
        self.assertTrue(note["patched"])
        self.assertEqual(note["reason"], "tool-prefix-restored-history-changed")
        self.assertFalse(note["history_matches"])
        self.assertEqual(note["shared_items"], 0)
        self.assertEqual(fixed["tools"], body()["tools"])

    def test_noncompact_empty_tools_and_remote_compaction_unchanged(self):
        compact = body("compaction")
        compact["client_metadata"]["x-codex-turn-metadata"] = json.dumps(
            {"thread_id": "thread-test", "request_kind": "compaction", "compaction": {"implementation": "responses_v2"}})
        fixed, note = self.cache.prepare(compact, self.headers)
        self.assertIs(fixed, compact)
        self.assertFalse(note["patched"])
        normal = body()
        normal["tools"] = []
        self.assertFalse(self.cache.prepare(normal, self.headers)[1]["patched"])

    def test_existing_tools_not_replaced(self):
        compact = body("compaction")
        compact["tools"] = body()["tools"]
        self.assertEqual(self.cache.prepare(compact, self.headers)[1]["reason"], "tools-already-present")

    def test_lite_prefix_restored_including_stable_id(self):
        self.cache.prepare(body(lite=True), self.headers)
        fixed, note = self.cache.prepare(body("compaction", lite=True), self.headers)
        self.assertTrue(note["patched"])
        self.assertEqual(fixed["input"][0], body(lite=True)["input"][0])
        self.assertIsNone(fixed["tools"])
        self.assertFalse(fixed["parallel_tool_calls"])

    def test_expired_snapshots_skipped(self):
        self.cache.ttl = -1
        self.assertEqual(self.cache.prepare(body("compaction"), self.headers)[1]["reason"], "no-snapshot")


@unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
class PersistenceTests(unittest.TestCase):
    def test_restart_restores_both_formats_and_no_plaintext(self):
        for lite in (False, True):
            with self.subTest(lite=lite), tempfile.TemporaryDirectory() as folder:
                path = Path(folder)/"prefix.dpapi"
                headers = {"Authorization": "Bearer private-test-credential"}
                first = PrefixCache(storage_path=path, namespace="test-upstream")
                first.prepare(body(lite=lite), headers)
                raw = path.read_bytes()
                self.assertNotIn(b"read_test", raw)
                self.assertNotIn(b"synthetic history", raw)
                self.assertNotIn(b"private-test-credential", raw)
                decoded = SnapshotStore(path).load()
                self.assertNotIn("synthetic history", json.dumps(decoded))
                self.assertNotIn("private-test-credential", json.dumps(decoded))
                restarted = PrefixCache(storage_path=path, namespace="test-upstream")
                self.assertEqual(restarted.status()["restored_at_start"], 1)
                fixed, note = restarted.prepare(body("compaction", lite=lite), headers)
                self.assertTrue(note["patched"])
                if lite:
                    self.assertEqual(fixed["input"][0], body(lite=True)["input"][0])
                else:
                    self.assertEqual(fixed["tools"], body()["tools"])
                # Recovered snapshots keep account, model, settings and history checks.
                self.assertEqual(restarted.prepare(body("compaction", lite=lite),
                    {"Authorization": "Bearer other"})[1]["reason"], "no-snapshot")
                changed = body("compaction", lite=lite)
                changed["model"] = "other-model"
                self.assertEqual(restarted.prepare(changed, headers)[1]["reason"], "no-snapshot")
                changed = body("compaction", lite=lite)
                changed["instructions"] = "changed"
                fixed, note = restarted.prepare(changed, headers)
                self.assertTrue(note["patched"])
                self.assertIn("instructions", note["changed_settings"])
                changed = body("compaction", lite=lite)
                changed["input"][1 if lite else 0]["content"] = "changed"
                fixed, note = restarted.prepare(changed, headers)
                self.assertTrue(note["patched"])
                self.assertEqual(note["reason"], "tool-prefix-restored-history-changed")
                self.assertEqual(fixed["input"][1 if lite else 0:], changed["input"][1 if lite else 0:])

    def test_expiry_and_upstream_isolation_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"prefix.dpapi"
            first = PrefixCache(storage_path=path, namespace="one")
            first.prepare(body(), {"Authorization": "Bearer test"})
            changed = PrefixCache(storage_path=path, namespace="two")
            self.assertEqual(changed.status()["available"], 0)
            self.assertEqual(changed.status()["error"], "snapshot-load-failed")
            decoded = SnapshotStore(path).load()
            decoded["snapshots"][0][1]["time"] = time.time()-86401
            SnapshotStore(path).save(decoded)
            expired = PrefixCache(storage_path=path, namespace="one")
            self.assertEqual(expired.status()["available"], 0)
            self.assertEqual(expired.prepare(body("compaction"),
                {"Authorization": "Bearer test"})[1]["reason"], "no-snapshot")

    def test_corrupted_file_does_not_break_turn_and_is_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"prefix.dpapi"
            path.write_bytes(b"corrupted")
            cache = PrefixCache(storage_path=path)
            self.assertEqual(cache.status()["error"], "snapshot-load-failed")
            headers = {"Authorization": "Bearer test"}
            original = body()
            fixed, note = cache.prepare(original, headers)
            self.assertIs(fixed, original)
            self.assertEqual(note["reason"], "snapshot-saved")
            self.assertIsNone(cache.status()["error"])
            self.assertTrue(PrefixCache(storage_path=path).prepare(body("compaction"), headers)[1]["patched"])

    def test_disk_write_failure_keeps_in_memory_fix(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = PrefixCache(storage_path=Path(folder)/"prefix.dpapi")
            headers = {"Authorization": "Bearer test"}
            with patch.object(cache.store, "save", side_effect=OSError("synthetic failure")):
                self.assertEqual(cache.prepare(body(), headers)[1]["reason"], "snapshot-saved")
            self.assertEqual(cache.status()["error"], "snapshot-save-failed")
            self.assertTrue(cache.prepare(body("compaction"), headers)[1]["patched"])


class SummaryTests(unittest.TestCase):
    def test_valid_summary_split_across_network_boundaries(self):
        data = sse(summary())
        inspector = SSEInspector()
        for start in range(0, len(data), 3):
            inspector.feed(data[start:start+3])
        self.assertIsNone(inspector.finish())
        self.assertEqual(inspector.usage["input_tokens"], 1234)

    def test_tool_call_rejected_even_if_followed_by_valid_summary(self):
        inspector = SSEInspector()
        inspector.feed(sse(summary(), {"type": "response.output_item.added", "item": {"type": "function_call"}}))
        self.assertEqual(inspector.finish(), "summary-contained-tool-or-unexpected-output")

    def test_empty_or_incomplete_response_rejected(self):
        self.assertEqual(summary_error([]), "invalid-summary-response")
        self.assertEqual(summary_error(summary("  ")), "summary-empty")
        result = summary()
        result["status"] = "incomplete"
        self.assertEqual(summary_error(result), "summary-response-not-completed")
        inspector = SSEInspector()
        inspector.feed(b'data: {"type":"response.created"}\n\n')
        self.assertEqual(inspector.finish(), "summary-stream-missing-completion")


class ConfigTests(unittest.TestCase):
    def test_only_provider_url_changes_and_restore_preserves_later_edits(self):
        original = 'model = "test-model"\n[model_providers.custom]\nname = "test"\nbase_url = "https://example.invalid/v1"\nexperimental_bearer_token = "test-placeholder"\n[desktop]\nlocaleOverride = "zh-CN"\n'
        updated = change_provider_url(original, "custom", "https://example.invalid/v1", "http://127.0.0.1:28615/v1")
        self.assertEqual(updated, original.replace("https://example.invalid/v1", "http://127.0.0.1:28615/v1"))
        later = updated.replace('model = "test-model"', 'model = "other-model"')
        restored = change_provider_url(later, "custom", "http://127.0.0.1:28615/v1", "https://example.invalid/v1")
        self.assertEqual(restored, original.replace('model = "test-model"', 'model = "other-model"'))

    def test_changed_url_is_not_overwritten(self):
        with self.assertRaises(RuntimeError):
            change_provider_url('[model_providers.custom]\nbase_url = "https://other.invalid/v1"\n',
                                "custom", "https://expected.invalid/v1", "http://127.0.0.1:28615/v1")

    def test_restore_and_enable_are_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"config.toml"
            original = 'model = "test"\n[model_providers.custom]\nbase_url = "https://example.invalid/v1"\n'
            path.write_text(original, encoding="utf-8")
            values = {"provider_id": "custom", "config_path": str(path),
                      "upstream_base_url": "https://example.invalid/v1", "local_base_url": "http://127.0.0.1:28615/v1"}
            with patch.object(control, "settings", return_value=values):
                self.assertTrue(control.set_route(True))
                self.assertFalse(control.set_route(True))
                self.assertTrue(control.set_route(False))
                self.assertFalse(control.set_route(False))
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_duplicate_start_reuses_service(self):
        manager = MagicMock()
        with patch.object(control, "health", return_value={"directory": str(control.ROOT), "version": control.VERSION}), \
             patch.object(control, "ProviderRouteManager", return_value=manager), patch.object(control, "make_server") as server, \
             patch("builtins.print"):
            control.start()
            manager.sync.assert_called_once_with()
            server.assert_not_called()

    def test_sync_mode_setting_preserves_other_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"settings.json").write_text(json.dumps({"keep": "value"}), encoding="utf-8")
            with patch.object(control, "ROOT", root), patch("builtins.print"):
                control.set_sync_mode("event")
            saved = json.loads((root/"settings.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["provider_sync_mode"], "event")
            self.assertEqual(saved["keep"], "value")

    def test_duplicate_start_refuses_running_old_code(self):
        with patch.object(control, "health", return_value={"directory": str(control.ROOT), "version": "1.0.0"}), \
             patch.object(control, "set_route") as route, patch.object(control, "make_server") as server:
            with self.assertRaises(RuntimeError):
                control.start()
            route.assert_not_called()
            server.assert_not_called()

    def test_unhealthy_start_does_not_change_config(self):
        server = MagicMock()
        manager = MagicMock()
        manager.discover.return_value = "https://example.invalid/v1"
        with patch.object(control, "health", return_value=None), \
             patch.object(control, "settings", return_value={"upstream_base_url": "https://example.invalid/v1", "port": 0, "events_path": None}), \
             patch.object(control, "ProviderRouteManager", return_value=manager), \
             patch.object(control, "make_server", return_value=server):
            with self.assertRaises(RuntimeError):
                control.start()
            manager.sync.assert_not_called()
            server.server_close.assert_called_once()


class MockUpstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        data = self.server.output
        self.send_response(200)
        self.send_header("Content-Type", self.server.content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), MockUpstream)
        cls.second_upstream = ThreadingHTTPServer(("127.0.0.1", 0), MockUpstream)
        cls.upstream.received = []
        cls.second_upstream.received = []
        cls.upstream.output = encode(summary())
        cls.upstream.content_type = "application/json"
        cls.second_upstream.output = encode(summary("Second provider response."))
        cls.second_upstream.content_type = "application/json"
        cls.proxy = make_server(f"http://127.0.0.1:{cls.upstream.server_port}/v1", 0)
        cls.headers = {"Authorization": "Bearer integration-test"}
        cls.proxy.state.set_upstream(cls.proxy.state.upstream,
                                     hashlib.sha256(cls.headers["Authorization"].encode()).hexdigest())
        for server in (cls.upstream, cls.second_upstream, cls.proxy):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.proxy.server_port}/v1/responses"

    @classmethod
    def tearDownClass(cls):
        for server in (cls.proxy, cls.upstream, cls.second_upstream):
            server.shutdown()
            server.server_close()

    def test_http_patch_and_sse_forwarding(self):
        self.upstream.output = encode(summary())
        self.upstream.content_type = "application/json"
        self.assertEqual(requests.post(self.url, json=body(), headers=self.headers, timeout=5).status_code, 200)
        self.upstream.output = sse(summary())
        self.upstream.content_type = "text/event-stream"
        response = requests.post(self.url, json=body("compaction"), headers=self.headers, timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.upstream.output)
        self.assertEqual(self.upstream.received[-1]["tools"], body()["tools"])
        self.assertTrue(self.upstream.received[-1]["parallel_tool_calls"])

    def test_metrics_record_upstream_provider_host(self):
        response = requests.post(self.url, json=body(), headers=self.headers, timeout=5)
        self.assertEqual(response.status_code, 200)
        recent = requests.get(f"http://127.0.0.1:{self.proxy.server_port}/metrics", timeout=5).json()["recent"]
        self.assertEqual(recent[-1]["provider"], "127.0.0.1")

    def test_changed_provider_upstream_receives_request(self):
        original = f"http://127.0.0.1:{self.upstream.server_port}/v1"
        self.addCleanup(self.proxy.state.set_upstream, original)
        self.proxy.state.set_upstream(f"http://127.0.0.1:{self.second_upstream.server_port}/v1",
                                     hashlib.sha256(self.headers["Authorization"].encode()).hexdigest())
        before_old = len(self.upstream.received)
        before_new = len(self.second_upstream.received)
        response = requests.post(self.url, json=body(), headers=self.headers, timeout=5)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.upstream.received), before_old)
        self.assertEqual(len(self.second_upstream.received), before_new+1)
        self.assertEqual(self.second_upstream.received[-1], body())

    def test_invalid_summary_withheld_without_partial_sse(self):
        self.upstream.output = sse(summary(), {"type": "response.output_item.added", "item": {"type": "custom_tool_call"}})
        self.upstream.content_type = "text/event-stream"
        response = requests.post(self.url, json=body("compaction"), headers=self.headers, timeout=5)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b"response.created", response.content)
        self.assertEqual(response.json()["error"]["type"], "compaction_validation_error")

    def test_unauthorized_requests_not_forwarded(self):
        before = len(self.upstream.received)
        self.assertEqual(requests.post(self.url, json=body(), timeout=5).status_code, 401)
        self.assertEqual(len(self.upstream.received), before)

    def test_route_sync_failure_fails_closed(self):
        before = len(self.upstream.received)
        self.proxy.state.sync_callback = lambda: (_ for _ in ()).throw(RuntimeError("synthetic failure"))
        try:
            response = requests.post(self.url, json=body(), headers=self.headers, timeout=5)
        finally:
            self.proxy.state.sync_callback = None
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(self.upstream.received), before)

    def test_old_provider_credential_is_not_forwarded_to_new_upstream(self):
        before = len(self.second_upstream.received)
        self.proxy.state.set_upstream(f"http://127.0.0.1:{self.second_upstream.server_port}/v1",
                                      hashlib.sha256(b"Bearer new-provider-key").hexdigest())
        try:
            response = requests.post(self.url, json=body(), headers=self.headers, timeout=5)
        finally:
            self.proxy.state.set_upstream(f"http://127.0.0.1:{self.upstream.server_port}/v1",
                                          hashlib.sha256(self.headers["Authorization"].encode()).hexdigest())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(self.second_upstream.received), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
