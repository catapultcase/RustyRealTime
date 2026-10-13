#!/usr/bin/env python3
"""Replay the last step of real tool-using answers (export.py) and time when speech could start (docs/ARCHITECTURE.md).

For each turn: stream the chat model's final answer from the chat as it stood after its last tool result, timestamp
every reasoning and content delta, and compare
  today    - speech starts when the answer is complete: t_end + DIRECT (our bridge speaks a finished result in 0.19 s)
  stream   - speech starts on the first sentence the hold rule commits: t_commit + TTFA (Kokoro's first audio, 0.1 s)
Prefill is in both, so the saving (today - stream) does not depend on the replay missing Open WebUI's prompt cache.

The hold rule is also run over every message item that came BEFORE a later tool call (interim narration) to count
how many would have been spoken. Today they all are: Open WebUI's getOutputText joins every message item.

  python3 replay.py turns.jsonl http://<chat-server>/v1 > results.jsonl
  python3 replay.py --report results.jsonl turns.jsonl
"""
import json
import re
import statistics
import sys
import time

DIRECT, TTFA = 0.19, 0.10
# (sentences, chars): commit once the item so far holds at least this many complete sentences AND characters.
RULES = [(1, 0), (2, 0), (2, 120), (2, 200), (3, 0)]
SENTENCE = re.compile(r"[.!?…](?:[\"')\]*_]*)(?=\s)|\n")


def sentences_done(text):
    """Complete sentences in text, and the end offset of each (a newline ends a line of a list too)."""
    return [m.end() for m in SENTENCE.finditer(text) if text[: m.end()].strip()]


def commit_at(deltas, rule):
    """Time the rule commits on a timed stream of (t, text) deltas, or None if the answer ended first."""
    n, c = rule
    text = ""
    for t, d in deltas:
        text += d
        ends = sentences_done(text)
        if len(ends) >= n and ends[n - 1] >= c:
            return t
    return None


def leaks(item, rule):
    """Would the rule speak this narration item before the tool call that followed it?"""
    n, c = rule
    # The item's own end is followed by a tool call, not by more text: only sentences complete INSIDE it count.
    ends = [e for e in sentences_done(item) if e < len(item)]
    return len(ends) >= n and ends[n - 1] >= c


def replay(turn, base):
    import requests
    tools = [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}}
             for n in turn["tools"]]
    body = {"model": turn["model"], "messages": turn["messages"], "stream": True, "tools": tools, "tool_choice": "none"}
    if turn.get("reasoning_effort"):
        body["reasoning_effort"] = turn["reasoning_effort"]
    t0 = time.monotonic()
    first_reason = first_content = None
    deltas = []
    with requests.post(base + "/chat/completions", json=body, stream=True, timeout=600) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: ") or line == "data: [DONE]":
                continue
            ch = json.loads(line[6:]).get("choices") or [{}]
            delta = ch[0].get("delta") or {}
            t = time.monotonic() - t0
            if delta.get("reasoning_content") or delta.get("reasoning"):
                first_reason = first_reason if first_reason is not None else t
            if delta.get("content"):
                first_content = first_content if first_content is not None else t
                deltas.append((round(t, 3), delta["content"]))
    return {"id": turn["id"], "t_end": round(time.monotonic() - t0, 3), "first_reason": first_reason,
            "first_content": first_content, "deltas": deltas}


def report(results, turns):
    by_id = {t["id"]: t for t in turns}
    print(f"{len(results)} replayed answers; speech start measured from the request, prefill included in both")
    for rule in RULES:
        today, stream, saved = [], [], []
        for r in results:
            if not r["deltas"]:
                continue
            a = r["t_end"] + DIRECT
            c = commit_at(r["deltas"], rule)
            b = (c + TTFA) if c is not None else a
            today.append(a); stream.append(b); saved.append(a - b)
        nar = [n for t in turns for n in t["narration"]]
        leaked = sum(leaks(n, rule) for n in nar)
        q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))]
        print(f"rule {rule[0]} sentence(s) + {rule[1]:>3} chars | speech start median today {statistics.median(today):5.1f} s"
              f" -> {statistics.median(stream):5.1f} s | saved median {statistics.median(saved):4.1f} s, p90 {q(saved, .9):4.1f} s"
              f" | narration spoken {leaked}/{len(nar)} (today {len(nar)}/{len(nar)})")


if __name__ == "__main__":
    if sys.argv[1] == "--report":
        results = [json.loads(l) for l in open(sys.argv[2])]
        turns = [json.loads(l) for l in open(sys.argv[3])]
        report(results, turns)
    else:
        for line in open(sys.argv[1]):
            turn = json.loads(line)
            try:
                print(json.dumps(replay(turn, sys.argv[2].rstrip("/"))), flush=True)
            except Exception as e:
                print(f"{turn['id']}: {e}", file=sys.stderr)
