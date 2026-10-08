"""Small paid A/B experiment using synthetic input and the existing provider key.

Reads the credential in memory, never writes it or prints request bodies/headers.
"""
import copy
import hashlib
import json
import os
import threading
import time
import tomllib
import uuid
from pathlib import Path

import requests

from proxy import SSEInspector, make_server, summary_error
from layout import SETTINGS_FILE


def configured_provider():
    config = tomllib.loads((Path.home()/".codex"/"config.toml").read_text(encoding="utf-8-sig"))
    provider = config["model_providers"][config["model_provider"]]
    key = provider.get("experimental_bearer_token") or os.environ.get(provider.get("env_key", ""))
    if not key:
        raise RuntimeError("Configured provider credential unavailable")
    base = provider["base_url"]
    if base.startswith("http://127.0.0.1:"):
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
        base = settings["upstream_base_url"]
    return base.rstrip("/"), key, config["model"]


def request(url, body, key):
    started = time.monotonic()
    headers = {"Authorization": "Bearer "+key, "Content-Type": "application/json",
               "x-codex-turn-metadata": body["client_metadata"]["x-codex-turn-metadata"],
               "session_id": body["prompt_cache_key"]}
    inspector = SSEInspector()
    with requests.post(url, json=body, headers=headers, stream=True, timeout=(30, 180), allow_redirects=False) as response:
        if response.status_code != 200:
            raise RuntimeError("Experiment HTTP status "+str(response.status_code))
        if "text/event-stream" in response.headers.get("Content-Type", ""):
            for chunk in response.iter_content(chunk_size=None):
                inspector.feed(chunk)
            error = inspector.finish()
            result = inspector.response or {}
        else:
            result = response.json()
            error = summary_error(result)
    if error:
        raise RuntimeError("Experiment output validation: "+error)
    usage = result.get("usage") or {}
    inputs = usage.get("input_tokens", 0)
    details = usage.get("input_tokens_details") or {}
    cached = details.get("cached_tokens", 0)
    return {"input_tokens": inputs, "cached_tokens": cached,
            "cache_write_tokens": details.get("cache_write_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "hit_rate": cached/inputs if inputs else 0,
            "elapsed_seconds": round(time.monotonic()-started, 3),
            "nonempty_summary": True, "tool_calls": 0}


def make_experiment_server(base, key, port=0, snapshot_path=None):
    server = make_server(base, port, snapshot_path=snapshot_path)
    expected = hashlib.sha256(("Bearer "+key).encode()).hexdigest()
    server.state.set_upstream(base, expected)
    return server


def run():
    base, key, model = configured_provider()
    server = make_experiment_server(base, key)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proxy_url = f"http://127.0.0.1:{server.server_port}/v1/responses"
    report = {"model": model, "upstream_host": base.split("/")[2],
              "synthetic_input_only": True, "pairs": []}
    try:
        for trial in range(2):
            identity = "compaction-experiment-"+uuid.uuid4().hex
            tools = [{"type": "function", "name": f"synthetic_lookup_{i}",
                      "description": "A synthetic test tool. Never invoke during this experiment. "+
                       " ".join(f"Field {j} represents synthetic reference number {i*100+j}." for j in range(45)),
                      "parameters": {"type": "object", "properties": {"reference": {"type": "string"}},
                                     "required": ["reference"], "additionalProperties": False}}
                     for i in range(4)]
            context = "Synthetic test identity: "+identity+"\n"+"\n".join(
                f"Record {i:03d}: Example project item {i*17+3}, owner Team-{i%7}, milestone M-{i%11}; "
                f"its mock status is verified, synthetic checksum {uuid.uuid5(uuid.NAMESPACE_OID, identity+str(i)).hex}. "
                "These records describe a fictional test project; do not access files or call tools."
                for i in range(75))
            warm = {"model": model, "instructions": "You are evaluating a synthetic project. Never call tools. "
                    "When asked to summarize, produce a short plain text summary; otherwise say READY.",
                    "input": [{"role": "user", "content": context+"\nReply only READY."}],
                    "tools": tools, "parallel_tool_calls": True, "reasoning": {"effort": "medium"},
                    "max_output_tokens": 1024, "stream": True, "store": False, "prompt_cache_key": identity,
                    "client_metadata": {"thread_id": identity, "session_id": identity,
                        "x-codex-turn-metadata": json.dumps({"thread_id": identity, "request_kind": "turn"})}}
            row = {"trial": trial+1}
            row["warm"] = request(proxy_url, warm, key)
            print(json.dumps({"trial": trial+1, "phase": "warm", **row["warm"]}), flush=True)
            compact = copy.deepcopy(warm)
            compact["input"].extend([
                {"role": "assistant", "content": "READY"},
                {"role": "user", "content": "Summarize this fictional project context in at most 60 words. "
                 "Return only the summary, never call tools."}])
            compact["tools"] = []
            compact["parallel_tool_calls"] = False
            compact["client_metadata"]["x-codex-turn-metadata"] = json.dumps(
                {"thread_id": identity, "request_kind": "compaction",
                 "compaction": {"implementation": "responses", "trigger": "manual"}})
            row["baseline"] = request(base+"/responses", compact, key)
            print(json.dumps({"trial": trial+1, "phase": "baseline", **row["baseline"]}), flush=True)
            row["fixed"] = request(proxy_url, compact, key)
            print(json.dumps({"trial": trial+1, "phase": "fixed", **row["fixed"]}), flush=True)
            report["pairs"].append(row)
            Path("experiment-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        for phase in ("baseline", "fixed"):
            inputs = sum(row[phase]["input_tokens"] for row in report["pairs"])
            cached = sum(row[phase]["cached_tokens"] for row in report["pairs"])
            report[phase+"_weighted_hit_rate"] = cached/inputs
        report["proxy_metrics"] = server.state.metrics()
        report["passed"] = (report["fixed_weighted_hit_rate"] > report["baseline_weighted_hit_rate"]+0.30
                            and all(row["fixed"]["hit_rate"] > 0.60 for row in report["pairs"]))
        Path("experiment-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "baseline_hit": report["baseline_weighted_hit_rate"],
                          "fixed_hit": report["fixed_weighted_hit_rate"]}), flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    run()
