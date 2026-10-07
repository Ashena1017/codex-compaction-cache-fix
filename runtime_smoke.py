"""Exercise manual and automatic compaction using the real Desktop binary.

Ephemeral synthetic threads only; no tools are requested or approved.
"""
import json
import argparse
import subprocess
import threading
import time
import tomllib
import requests
from collections import Counter
from pathlib import Path
from experiment_support import read_config, codex_binary

from experiment import configured_provider
from proxy import make_server


class RPC:
    def __init__(self, argv):
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.cv = threading.Condition()
        self.responses = {}
        self.events = []
        self.seq = 0
        self.unexpected_calls = 0
        threading.Thread(target=self.read, daemon=True).start()

    def send(self, value):
        self.process.stdin.write(json.dumps(value)+"\n")
        self.process.stdin.flush()

    def read(self):
        for line in self.process.stdout:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            with self.cv:
                if "id" in value and "method" not in value:
                    self.responses[value["id"]] = value
                else:
                    self.events.append(value)
                    if "id" in value and "method" in value:
                        self.unexpected_calls += 1
                        self.send({"jsonrpc": "2.0", "id": value["id"], "error":
                                   {"code": -32603, "message": "Tools are disabled in this synthetic test"}})
                self.cv.notify_all()

    def call(self, method, params, timeout=120):
        with self.cv:
            self.seq += 1
            request_id = self.seq
            self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            deadline = time.monotonic()+timeout
            while request_id not in self.responses:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("RPC timeout: "+method)
                self.cv.wait(min(remaining, 2))
            response = self.responses.pop(request_id)
            if "error" in response:
                raise RuntimeError("RPC error: "+method+" code "+str(response["error"].get("code")))
            return response.get("result") or {}

    def wait_event(self, method, thread, cursor, timeout=180):
        with self.cv:
            deadline = time.monotonic()+timeout
            while True:
                for event in self.events[cursor:]:
                    if event.get("method") == method and event.get("params", {}).get("threadId") == thread:
                        return event["params"]
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    counts = dict(Counter(event.get("method") for event in self.events[cursor:]))
                    raise RuntimeError("RPC event timeout: "+method+"; methods="+json.dumps(counts))
                self.cv.wait(min(remaining, 2))

    def turn(self, thread, text):
        cursor = len(self.events)
        self.call("turn/start", {"threadId": thread, "input": [{"type": "text", "text": text}]})
        event = self.wait_event("turn/completed", thread, cursor)
        if event["turn"].get("status") != "completed":
            raise RuntimeError("Runtime test turn did not complete")
        usages = [event.get("params", {}).get("tokenUsage", {}).get("last")
                  for event in self.events[cursor:] if event.get("method") == "thread/tokenUsage/updated"]
        return [usage for usage in usages if usage]

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=10)


def run(installed=False):
    base, _, model = configured_provider()
    user_config = read_config()
    provider = user_config["model_provider"]
    server = None
    if installed:
        proxy_base = user_config["model_providers"][provider]["base_url"]
        def metrics():
            return requests.get(proxy_base.rsplit("/", 1)[0]+"/metrics", timeout=5).json()
    else:
        server = make_server(base, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        proxy_base = f"http://127.0.0.1:{server.server_port}/v1"
        def metrics():
            return server.state.metrics()
    binary = codex_binary(user_config)
    argv = [str(binary), "app-server", "--stdio", "-c", "mcp_servers={}"]
    if not installed:
        argv += ["-c", f'model_providers.{provider}.base_url="{proxy_base}"']
    rpc = RPC(argv)
    report = {"binary": str(binary), "model": model, "ephemeral": True,
              "installed_config": installed, "scenarios": []}
    output = Path("installed-smoke-results.json" if installed else "runtime-smoke-results.json")
    try:
        rpc.call("initialize", {"clientInfo": {"name": "compaction-proxy-test", "version": "1.0"},
                                 "capabilities": {"experimentalApi": True}})
        rpc.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        print(json.dumps({"phase": "runtime-initialized"}), flush=True)
        for scenario in (("manual",) if installed else ("manual", "automatic")):
            result = rpc.call("thread/start", {"model": model, "modelProvider": provider,
                "cwd": str(Path.cwd()), "ephemeral": True, "sandbox": "read-only",
                "baseInstructions": "This is a synthetic integration test. Never call tools or read files. "
                    "Reply READY to ordinary test prompts. When summarizing, produce short plain text.",
                "developerInstructions": "Never use tools. Preserve the checkpoint phrase LIME-42 in summaries.",
                "config": {"model_auto_compact_token_limit": 1000000 if scenario == "manual" else 5000,
                           "compact_prompt": "Summarize the synthetic test context in under 60 words, "
                                "preserving checkpoint LIME-42. Never call tools. Return only plain text."}})
            thread = result["thread"]["id"]
            print(json.dumps({"phase": "thread-ready", "scenario": scenario}), flush=True)
            prompt = "Synthetic checkpoint LIME-42.\n"+"\n".join(
                f"Fictional record {i}: sample project M-{i%9}, mock reference {i*31+7}; "
                "all values are invented for a transport integration test and must not trigger any tool."
                for i in range(60))+"\nReply only READY. Never call tools."
            metrics_start = len(metrics()["recent"])
            row = {"scenario": scenario, "warm_usage": rpc.turn(thread, prompt)}
            print(json.dumps({"phase": "warm-completed", "scenario": scenario}), flush=True)
            if scenario == "manual":
                cursor = len(rpc.events)
                rpc.call("thread/compact/start", {"threadId": thread})
                completed = rpc.wait_event("turn/completed", thread, cursor)
                if completed["turn"].get("status") != "completed":
                    raise RuntimeError("Manual compaction did not complete")
            # For the automatic case, the next turn forces a pre-turn compaction.
            if not installed or scenario == "automatic":
                row["continuation_usage"] = rpc.turn(thread, "Checkpoint test continuation. Reply READY, no tools.")
            recent = metrics()["recent"][metrics_start:]
            row["compactions"] = [event for event in recent if event.get("local_compaction")]
            row["passed"] = bool(row["compactions"]) and all(
                event.get("patched") and event.get("status") == 200 and not event.get("summary_error")
                for event in row["compactions"])
            report["scenarios"].append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["unexpected_tool_requests"] = rpc.unexpected_calls
        report["passed"] = all(row["passed"] for row in report["scenarios"]) and rpc.unexpected_calls == 0
        report["proxy_metrics"] = metrics()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"runtime_passed": report["passed"]}), flush=True)
    finally:
        rpc.close()
        if server:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed", action="store_true")
    run(parser.parse_args().installed)
