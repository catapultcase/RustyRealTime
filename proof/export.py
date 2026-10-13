#!/usr/bin/env python3
"""Export real tool-using answers from Open WebUI's database, read-only, for replay.py (docs/ARCHITECTURE.md).

Runs INSIDE the open-webui container and prints JSONL, one line per assistant turn that called a tool:
  model, system (the model row's prompt), reasoning_effort,
  messages        - the chat before the turn plus the turn's own items up to its last tool result, as Chat Completions
  final           - the text of the turn's message items after its last tool call (what was answered)
  narration       - the text of message items before a later tool call (spoken today: getOutputText joins them all)

  ssh root@<unraid> 'docker exec -i open-webui python3 -' < export.py > turns.jsonl
"""
import json
import sqlite3
import sys

MODELS = sys.argv[1:] or ["qwen3.8-flash-next"]
db = sqlite3.connect("file:/app/backend/data/webui.db?mode=ro", uri=True)
rows = {r[0]: json.loads(r[1] or "{}") for r in db.execute("select id, params from model")}


def text(item):
    return "".join(p.get("text", "") for p in item.get("content") or [] if isinstance(p, dict)).strip()


def visible(m):
    """What a later turn sees of an earlier message: its message items, else its content."""
    out = [text(o) for o in m.get("output") or [] if o.get("type") == "message"]
    return "\n".join(t for t in out if t) or (m.get("content") or "")


def branch(msgs, mid):
    chain = []
    while mid and mid in msgs:
        chain.append(msgs[mid])
        mid = msgs[mid].get("parentId")
    return chain[::-1]


for (chat,) in db.execute("select chat from chat"):
    msgs = json.loads(chat).get("history", {}).get("messages", {})
    for m in msgs.values():
        out = m.get("output") or []
        calls = [i for i, o in enumerate(out) if o.get("type") == "function_call"]
        if m.get("role") != "assistant" or m.get("model") not in MODELS or not calls:
            continue
        last = max(calls)
        last_out = max([i for i, o in enumerate(out) if o.get("type") == "function_call_output"] or [last])
        params = rows.get(m["model"], {})
        conv = [{"role": "system", "content": params.get("system", "")}]
        for prev in branch(msgs, m.get("parentId")):
            if prev.get("role") in ("user", "assistant"):
                conv.append({"role": prev["role"], "content": visible(prev)})
        for o in out[: last_out + 1]:
            t = o.get("type")
            if t == "message" and text(o):
                conv.append({"role": "assistant", "content": text(o)})
            elif t == "function_call":
                conv.append({"role": "assistant", "content": None, "tool_calls": [{
                    "id": o["call_id"], "type": "function",
                    "function": {"name": o["name"], "arguments": o.get("arguments") or "{}"}}]})
            elif t == "function_call_output":
                body = o.get("output")
                if isinstance(body, list):
                    body = "".join(p.get("text", "") for p in body if isinstance(p, dict))
                conv.append({"role": "tool", "tool_call_id": o["call_id"], "content": str(body)[:60000]})
        print(json.dumps({
            "id": m["id"], "model": m["model"], "reasoning_effort": params.get("reasoning_effort"),
            "tools": sorted({o["name"] for o in out if o.get("type") == "function_call"}),
            "messages": conv,
            "final": "\n".join(text(o) for o in out[last:] if o.get("type") == "message" and text(o)),
            "narration": [text(o) for i, o in enumerate(out) if o.get("type") == "message" and i < last and text(o)],
        }))
