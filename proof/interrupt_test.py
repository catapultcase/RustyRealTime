#!/usr/bin/env python3
"""Talking over a streamed answer: does the rest stay unspoken? Plays Open WebUI's side over a real provider.

A question is handed off; the answer streams as deltas, one sentence every 0.7 s. Once the first sentence is playing,
the user talks over it ("speech") or a burst of noise arrives ("noise"). Deltas keep arriving, as from a frontend that
missed the interruption, so this tests the provider's own backstop. Records what was spoken after the interruption.

  python3 interrupt_test.py ws://<bridge>/v1 http://<tts>/v1 <voice> realtime-call-prompt.txt <reps> > interrupts.jsonl
  python3 interrupt_test.py --report interrupts.jsonl
"""
import asyncio
import base64
import io
import json
import random
import sys
import urllib.request
import wave

QUESTION = "Give me a rundown of the Halloween films."
SENTENCES = ["John Carpenter's original came out in 1978.", " It made Michael Myers a horror icon.",
             " Halloween II followed in 1981.", " The series was rebooted twice.",
             " The 2018 film ignored every sequel.", " Halloween Ends closed that trilogy in 2022."]
STOP = "Wait, stop."
CHAT_TOOL = {"type": "function", "name": "generate_chat_completion", "description": "Handle substantive questions.",
             "parameters": {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"],
                            "additionalProperties": False}}


def speech(tts, voice, text):
    req = urllib.request.Request(tts + "/audio/speech", data=json.dumps(
        {"model": "kokoro-82m", "input": text, "voice": voice, "response_format": "wav"}).encode(),
        headers={"Content-Type": "application/json"})
    with wave.open(io.BytesIO(urllib.request.urlopen(req, timeout=60).read())) as w:
        return w.readframes(w.getnframes())


def noise(seconds=1.5, level=6000):
    return b"".join(random.randint(-level, level).to_bytes(2, "little", signed=True) for _ in range(int(24000 * seconds)))


async def trial(bridge, voice, prompt, kind, audio):
    import websockets
    async with websockets.connect(bridge + "/realtime?model=voice", max_size=None,
                                  additional_headers={"Authorization": "Bearer none"}) as ws:
        send = lambda e: ws.send(json.dumps(e))
        assert json.loads(await ws.recv())["type"] == "session.created"
        await send({"type": "session.update", "session": {
            "type": "realtime", "output_modalities": ["audio"], "instructions": open(prompt).read(),
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000},
                                "transcription": {"model": "parakeet-tdt-0.6b-v3"},
                                "turn_detection": {"type": "server_vad", "interrupt_response": True, "create_response": False}},
                      "output": {"format": {"type": "audio/pcm", "rate": 24000}, "voice": voice}},
            "tools": [CHAT_TOOL], "tool_choice": "auto"}})
        assert json.loads(await ws.recv())["type"] == "session.updated"
        st = {"calls": [], "active": set(), "said": [], "stream_started": 0, "interrupted_at": None, "heard": []}

        async def reader():
            async for raw in ws:
                e = json.loads(raw)
                k = e.get("type", "")
                if k == "conversation.item.input_audio_transcription.completed" and e.get("transcript", "").strip():
                    st["heard"].append(e["transcript"])
                    await send({"type": "response.create", "response": {"metadata": {"input_item_id": e["item_id"]}}})
                elif k == "response.created":
                    st["active"].add(e["response"]["id"])
                    if (e["response"].get("metadata") or {}).get("openwebui_stream_text"):
                        st["stream_started"] += 1
                elif k == "response.done":
                    st["active"].discard(e["response"]["id"])
                elif k == "response.output_audio_transcript.done" and e.get("transcript", "").strip():
                    st["said"].append((len(st["heard"]), st["interrupted_at"] is not None, e["transcript"]))
                elif k == "response.output_item.done" and (e.get("item") or {}).get("name") == "generate_chat_completion":
                    st["calls"].append(e["item"]["call_id"])

        async def wait(pred, secs=60):
            for _ in range(secs * 10):
                if pred():
                    return True
                await asyncio.sleep(0.1)
            return False

        async def play(pcm):
            for i in range(0, len(pcm), 4800):
                await send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[i:i + 4800]).decode()})
                await asyncio.sleep(0.1)

        task = asyncio.create_task(reader())
        await play(audio["question"])
        if not await wait(lambda: st["calls"] and not st["active"]):
            task.cancel()
            return {"kind": kind, "first_handoff": False}
        call = st["calls"][0]

        async def stream():
            for s in SENTENCES:
                await send({"type": "openwebui.function_call_output.delta", "call_id": call, "delta": s})
                await asyncio.sleep(0.7)

        streamer = asyncio.create_task(stream())
        await wait(lambda: st["stream_started"] >= 1, 20)
        await asyncio.sleep(0.6)  # the first sentence is playing
        st["interrupted_at"] = len(st["said"])
        await play(audio[kind])
        await streamer
        await asyncio.sleep(2.0)
        await wait(lambda: not st["active"], 20)
        answer = "".join(SENTENCES)
        await send({"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": call,
                    "output": json.dumps({"status": "completed", "answer": answer})}})
        await send({"type": "response.create", "response": {"tools": [], "tool_choice": "none", "metadata": {"call_id": call}}})
        await asyncio.sleep(1.0)
        await wait(lambda: not st["active"], 20)
        task.cancel()
        after = [t for _, i, t in st["said"] if i]
        spoken_answer_after = [t for t in after if any(s.strip()[:20] in t for s in SENTENCES)]
        return {"kind": kind, "first_handoff": True, "heard": st["heard"][1:], "said_after": after,
                "answer_sentences_after": len(spoken_answer_after)}


async def main(bridge, tts, voice, prompt, reps):
    audio = {"question": speech(tts, voice, QUESTION) + b"\0" * 96000,
             "speech": speech(tts, voice, STOP) + b"\0" * 96000, "noise": noise() + b"\0" * 96000}
    for _ in range(reps):
        for kind in ("speech", "noise"):
            for attempt in range(6):  # the provider takes one call at a time and frees it a moment after hang-up
                await asyncio.sleep(3)
                try:
                    print(json.dumps(await trial(bridge, voice, prompt, kind, audio)), flush=True)
                    break
                except Exception as e:
                    err = repr(e)
            else:
                print(json.dumps({"kind": kind, "error": err}), flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "--report":
        rows = [json.loads(l) for l in open(sys.argv[2])]
        for kind in ("speech", "noise"):
            ok = [r for r in rows if r["kind"] == kind and r.get("first_handoff")]
            clean = sum(1 for r in ok if r["answer_sentences_after"] == 0)
            print(f"{kind}: answer stopped after the interruption {clean}/{len(ok)}"
                  f" (errors {sum(1 for r in rows if r['kind'] == kind and 'error' in r)})")
            for r in ok:
                if r["answer_sentences_after"]:
                    print(f"   still spoke: {r['said_after'][:3]}")
        sys.exit()
    asyncio.run(main(sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/"), sys.argv[3], sys.argv[4], int(sys.argv[5])))
