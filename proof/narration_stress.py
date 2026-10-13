#!/usr/bin/env python3
"""Worst case for the hold rule: narration written before another tool call (docs/ARCHITECTURE.md).

Open WebUI can't know whether the model will call another tool after the text it is writing, so the hold rule streams
text since the most recent tool call once it holds N complete sentences. This drives real agent loops (streaming, tool
calls) with prompts built to provoke narration, and reports every step whose text would have been streamed and then
turned out to be followed by another tool call: what a word-for-word provider would have said that was not the answer.

Tools return canned results so runs repeat; the model's behaviour between calls is what is measured.

  python3 narration_stress.py profiles.json runs.jsonl [reps]                         # run (on a box near the models)
  python3 narration_stress.py --report runs.jsonl                                    # summary and worst cases

profiles.json: {"label": {"url": "http://host/v1", "model": "id", "effort": "low", "system": "..."}, ...}
"""
import json
import re
import sys
import time

# The frontend's rule (src/lib/utils/realtime.ts getStreamableText), one step at a time.
SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]*_]*(?=\s)|\n")
RULES = (1, 2, 3)

PROMPTS = [
    "Compare the weather in Fort Worth and Austin this afternoon, then tell me which is better for a run.",
    "Check which machines in my lab are always on, then look up the current price of an RTX 5090 and tell me if I should buy one.",
    "Search for the latest Open WebUI release, read its changelog, and summarise the voice changes.",
    "Find three reviews of the Framework 13 laptop and compare their verdicts.",
    "Look up today's top tech news, pick the most important story, and fetch the article for the details.",
    "What does my lab's backup setup look like, and are there gaps? Then search 3-2-1 backup best practice and compare.",
    "Research the best 360 mm AIO coolers right now, check prices on two sites, and recommend one.",
    "Who directed the original Halloween, and what are his three most recent films? Check each one's rating.",
    "Plan a weekend in Dallas for me: check the weather, find two restaurants and one event.",
    "Check my lab's network, then search how to set up VLANs on pfSense, and tell me what I should change.",
    # other languages: punctuation and spacing differ, the design must not care
    "比较一下沃斯堡和奥斯汀今天下午的天气，然后告诉我哪个更适合跑步。",
    "最新のOpen WebUIのリリースを検索して、変更履歴を読み、音声機能の変更点をまとめてください。",
    "Prüfe, welche Rechner in meinem Lab immer laufen, suche dann den aktuellen Preis einer RTX 5090 und sag mir, ob sich der Kauf lohnt.",
    # formats: lists, tables and code in the answer
    "Search for the three best NAS drives right now and give me a numbered list with prices.",
    "Look up the specs of the RTX 5090 and RTX 4090 and put them in a markdown table.",
    "Search how to rotate logs with logrotate, then write me the config file for /var/log/myapp.",
]

TOOLS = [
    {"type": "function", "function": {"name": "search_web", "description": "Search the web (Brave). Returns titles, links and snippets.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "count": {"type": "integer"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fetch_url", "description": "Fetch a web page and return its text.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "junctionrelay_lab_briefing", "description": "Summary of the user's lab: machines, roles, networks.",
     "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "junctionrelay_lab_query", "description": "Answer a question from the user's lab database.",
     "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}},
]


def canned(name, args):
    if name == "search_web":
        q = args.get("query", "")
        return json.dumps([{"title": f"{q} - result {i}", "link": f"https://example.com/{i}",
                            "snippet": f"Details about {q}: figures, dates and opinions from source {i}."} for i in (1, 2, 3)])
    if name == "fetch_url":
        return ("Page text. The article covers the topic in depth with several figures, a short history, two expert "
                "quotes and a conclusion recommending a balanced approach. Prices start at $199 and go up to $349.")
    if name == "junctionrelay_lab_briefing":
        return ("MACHINES (21): Server A (file server, always-on), Server B (backups, always-on), Server C (support "
                "models, always-on), Server D (agents, always-on), four GPU boxes (LLM pairs), a desktop (RTX 5090). "
                "Networks: LAN 10G core, IoT VLAN, guest VLAN. Backups: nightly to Server B, cloud offsite.")
    return "Result: three matching records with dates, sizes and notes; one gap flagged (no offsite copy for the photo library)."


def sentence_ends(text):
    # A boundary counts only after real text: blank lines between paragraphs are not sentences.
    ends, last = [], 0
    for m in SENTENCE_END.finditer(text):
        if text[last : m.end()].strip():
            ends.append(m.end())
        last = m.end()
    return ends


def stream_step(url, body):
    import requests
    t0 = time.monotonic()
    content, calls, deltas, tool_start = "", {}, [], None
    with requests.post(url + "/chat/completions", json=body, stream=True, timeout=600) as r:
        r.raise_for_status()
        for raw in r.iter_lines():  # bytes, decoded whole: chunked decoding splits multi-byte characters
            line = raw.decode("utf-8") if raw else ""
            if not line or not line.startswith("data: ") or line == "data: [DONE]":
                continue
            delta = (json.loads(line[6:]).get("choices") or [{}])[0].get("delta") or {}
            now = round(time.monotonic() - t0, 3)
            if delta.get("content"):
                content += delta["content"]
                deltas.append((now, delta["content"]))
            if delta.get("tool_calls") and tool_start is None:
                tool_start = now
            for tc in delta.get("tool_calls") or []:
                c = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "arguments": ""})
                c["id"] = tc.get("id") or c["id"]
                fn = tc.get("function") or {}
                c["name"] += fn.get("name") or ""
                c["arguments"] += fn.get("arguments") or ""
    return content, [calls[i] for i in sorted(calls)], round(time.monotonic() - t0, 2), deltas, tool_start


def run(label, profile, prompt, max_steps=8):
    messages = [{"role": "system", "content": profile["system"]}, {"role": "user", "content": prompt}]
    steps = []
    for _ in range(max_steps):
        body = {"model": profile["model"], "messages": messages, "stream": True, "tools": TOOLS}
        if profile.get("effort"):
            body["reasoning_effort"] = profile["effort"]
        content, calls, secs, deltas, tool_start = stream_step(profile["url"], body)
        steps.append({"text": content, "tools": [c["name"] for c in calls], "secs": secs, "deltas": deltas,
                      "tool_start": tool_start})
        if not calls:
            break
        messages.append({"role": "assistant", "content": content or None, "tool_calls": [
            {"id": c["id"] or f"call_{i}", "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
            for i, c in enumerate(calls)]})
        for i, c in enumerate(calls):
            try:
                args = json.loads(c["arguments"] or "{}")
            except ValueError:
                args = {}
            messages.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}", "content": canned(c["name"], args)})
    return {"model": label, "prompt": prompt, "steps": steps}


# Lookahead: text is released once the model has written K more after it in the same step. Three candidate units.
UNITS = {"deltas": (2, 4, 8, 12, 16, 24), "chars": (20, 40, 80, 120, 160), "ms": (250, 500, 1000, 1500, 2000)}


def released_before_tool(step, unit, k):
    """Text a lookahead of k would have released before the step's tool call started (narration spoken)."""
    d = step.get("deltas") or []
    if not d:
        return ""
    if unit == "deltas":
        return "".join(x for _, x in d[: max(0, len(d) - k)])
    if unit == "chars":
        text = "".join(x for _, x in d)
        return text[: max(0, len(text) - k)]
    cutoff = (step["tool_start"] if step.get("tool_start") is not None else step["secs"]) - k / 1000
    return "".join(x for t, x in d if t <= cutoff)


def first_release(step, unit, k):
    """When a lookahead of k first releases text in an answer step (seconds into the step), or None."""
    d = step.get("deltas") or []
    if unit == "deltas":
        return d[k][0] if len(d) > k else None
    if unit == "chars":
        n = 0
        for t, x in d:
            n += len(x)
            if n > k:
                return t
        return None
    return d[0][0] + k / 1000 if d and d[-1][0] >= d[0][0] + k / 1000 else None


def lookahead_report(rs):
    narr = [s for r in rs for s in r["steps"] if s["tools"] and s["text"].strip() and s.get("deltas")]
    answers = [r["steps"][-1] for r in rs if r["steps"] and not r["steps"][-1]["tools"] and r["steps"][-1].get("deltas")]
    if not narr and not answers:
        return
    lens = sorted(len(s["deltas"]) for s in narr)
    chars = sorted(len(s["text"].strip()) for s in narr)
    ms = sorted(round(((s["tool_start"] or s["secs"]) - s["deltas"][0][0]) * 1000) for s in narr)
    if narr:
        print(f"   narration: deltas median {lens[len(lens)//2]} max {lens[-1]} | chars median {chars[len(chars)//2]} "
              f"max {chars[-1]} | ms from first text to tool call median {ms[len(ms)//2]} max {ms[-1]}")
    for unit, ks in UNITS.items():
        row = []
        for k in ks:
            leaked = sum(1 for s in narr if released_before_tool(s, unit, k).strip())
            saved = []
            for a in answers:
                t = first_release(a, unit, k)
                saved.append(a["secs"] - t if t is not None else 0.0)
            med = sorted(saved)[len(saved) // 2] if saved else 0
            row.append(f"K={k}: {leaked}/{len(narr)} spoken, saves {med:.1f}s")
        print(f"   {unit:6} " + " | ".join(row))


def report(path):
    runs = [json.loads(l) for l in open(path)]
    print(f"{len(runs)} runs")
    for model in sorted({r["model"] for r in runs}):
        rs = [r for r in runs if r["model"] == model]
        narr = [s for r in rs for s in r["steps"] if s["tools"] and s["text"].strip()]
        print(f"\n== {model}: {len(rs)} runs, {sum(len(r['steps']) for r in rs)} steps, "
              f"{sum(1 for r in rs for s in r['steps'] if s['tools'])} followed by a tool call, {len(narr)} of those with text")
        for n in RULES:
            leaks = [(r, s) for r in rs for s in r["steps"] if s["tools"] and len(sentence_ends(s["text"])) >= n]
            runs_hit = len({id(r) for r, _ in leaks})
            print(f"   rule {n} sentence(s): {len(leaks)} narration step(s) streamed, in {runs_hit} of {len(rs)} runs")
        lookahead_report(rs)
        worst = sorted(narr, key=lambda s: -len(sentence_ends(s["text"])))[:3]
        for s in worst:
            print(f"   worst ({len(sentence_ends(s['text']))} sentences, then {s['tools']}): {s['text'].strip()[:300]!r}")


if __name__ == "__main__":
    if sys.argv[1] == "--report":
        report(sys.argv[2])
        sys.exit()
    profiles = json.load(open(sys.argv[1]))
    reps = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 2
    with open(sys.argv[2], "a") as out:
        for rep in range(reps):
            for prompt in PROMPTS:
                for label, profile in profiles.items():
                    try:
                        out.write(json.dumps(run(label, profile, prompt)) + "\n")
                        out.flush()
                    except Exception as e:
                        print(f"{label}: {prompt[:40]}: {e}", file=sys.stderr, flush=True)
