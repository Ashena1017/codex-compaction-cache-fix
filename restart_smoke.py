"""Paid real-Desktop regression: warm, restart proxy, compact, continue."""
import json
import tempfile
import threading
import tomllib
from pathlib import Path
from experiment_support import read_config, codex_binary

from experiment import configured_provider, make_experiment_server
from proxy import VERSION
from runtime_smoke import RPC


def run():
    base, key, model = configured_provider()
    config = read_config()
    provider = config["model_provider"]
    binary = codex_binary(config)
    report = {"version": VERSION, "model": model, "ephemeral": True,
              "scenario": "warm-restart-compact-continue"}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/"prefix.dpapi"
        server = make_experiment_server(base, key, snapshot_path=path)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        rpc = RPC([str(binary), "app-server", "--stdio", "-c", "mcp_servers={}",
                   "-c", f'model_providers.{provider}.base_url="http://127.0.0.1:{port}/v1"'])
        try:
            rpc.call("initialize", {"clientInfo": {"name": "restart-regression", "version": VERSION},
                                     "capabilities": {"experimentalApi": True}})
            rpc.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            result = rpc.call("thread/start", {"model": model, "modelProvider": provider,
                "cwd": str(Path(__file__).resolve().parent), "ephemeral": True, "sandbox": "read-only",
                "baseInstructions": "Synthetic integration test. Never call tools or read files. Reply READY "
                    "to ordinary prompts. When summarizing, produce short plain text.",
                "developerInstructions": "Never use tools. Preserve checkpoint LIME-42 in summaries.",
                "config": {"model_auto_compact_token_limit": 1000000,
                    "compact_prompt": "Summarize the synthetic context in under 60 words, preserving "
                        "checkpoint LIME-42. Never call tools. Return only plain text."}})
            thread = result["thread"]["id"]
            prompt = "Synthetic checkpoint LIME-42.\n"+"\n".join(
                f"Fictional record {i}: sample project M-{i%9}, mock reference {i*31+7}; "
                "all values are invented for a transport integration test and must not trigger any tool."
                for i in range(60))+"\nReply only READY. Never call tools."
            report["warm_usage"] = rpc.turn(thread, prompt)
            report["before_restart"] = server.state.cache.status()
            print(json.dumps({"phase": "warm-completed", "snapshots": report["before_restart"]}), flush=True)
            server.shutdown()
            server.server_close()
            # A new server and PrefixCache instance, recovered only from encrypted disk.
            server = make_experiment_server(base, key, port, path)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            report["after_restart"] = server.state.cache.status()
            print(json.dumps({"phase": "proxy-restarted", "snapshots": report["after_restart"]}), flush=True)
            cursor = len(rpc.events)
            rpc.call("thread/compact/start", {"threadId": thread})
            compact = rpc.wait_event("turn/completed", thread, cursor)
            if compact["turn"].get("status") != "completed":
                raise RuntimeError("Compaction did not complete")
            report["compactions"] = [r for r in server.state.metrics()["recent"] if r.get("local_compaction")]
            report["continuation_usage"] = rpc.turn(thread, "Checkpoint continuation. Reply READY, no tools.")
            report["unexpected_tool_requests"] = rpc.unexpected_calls
            usages = [r.get("usage") or {} for r in report["compactions"]]
            report["cache_hit_rates"] = [(u.get("input_tokens_details") or {}).get("cached_tokens", 0)
                                         / max(1, u.get("input_tokens", 0)) for u in usages]
            report["passed"] = (report["after_restart"]["restored_at_start"] == 1
                and bool(report["compactions"]) and all(r.get("patched") and r.get("status") == 200
                    and not r.get("summary_error") for r in report["compactions"])
                and all(rate > .90 for rate in report["cache_hit_rates"]) and rpc.unexpected_calls == 0)
        finally:
            rpc.close()
            server.shutdown()
            server.server_close()
    output = Path(__file__).with_name("restart-smoke-results.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if not report["passed"]:
        raise RuntimeError("Restart regression failed; inspect saved metrics")


if __name__ == "__main__":
    run()
