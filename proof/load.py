#!/usr/bin/env python3
"""Keep a chat server busy for the under-load run (docs/LOOKAHEAD.md): N parallel long streaming answers, repeated
until the stop file appears. Prints tokens per second of the load streams every minute.

  python3 load.py http://<server>/v1 <model> <streams> <stopfile> [reasoning_effort]
"""
import json
import os
import sys
import threading
import time

URL, MODEL, N, STOP = sys.argv[1].rstrip("/"), sys.argv[2], int(sys.argv[3]), sys.argv[4]
EFFORT = sys.argv[5] if len(sys.argv) > 5 else None
PROMPT = ("Write a long, detailed technical history of home computer networking from the 1980s to today, with many "
          "sections and examples.")
done = {"deltas": 0}


def worker():
    import requests
    while not os.path.exists(STOP):
        body = {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "stream": True}
        if EFFORT:
            body["reasoning_effort"] = EFFORT
        try:
            with requests.post(URL + "/chat/completions", json=body, stream=True, timeout=900) as r:
                for raw in r.iter_lines():
                    if os.path.exists(STOP):
                        return
                    if raw.startswith(b"data: {"):
                        done["deltas"] += 1
        except Exception as e:
            print("load stream:", e, flush=True)
            time.sleep(2)


threads = [threading.Thread(target=worker, daemon=True) for _ in range(N)]
for t in threads:
    t.start()
last, t0 = 0, time.monotonic()
while not os.path.exists(STOP):
    time.sleep(60)
    print(f"{MODEL}: {(done['deltas'] - last) / 60:.1f} deltas/s across {N} load streams", flush=True)
    last = done["deltas"]
