#!/usr/bin/env python3
"""End-to-end check of a provider's streamed-results support, playing Open WebUI's side of a Realtime call.

  python3 bridge_stream_test.py ws://<bridge>:8765/v1 http://<tts-server>/v1 <voice> realtime-call-prompt.txt

1. Speaks a question into the call (TTS audio, paced in real time) and lets the voice model hand it off.
2. Streams an answer the way the patched Open WebUI does: two sentences as openwebui.function_call_output.delta,
   a third a second later, then the complete answer (with a narration line before it) as the function_call_output
   and a response.create for it.
3. Reports every response the provider started, what each one said, when its first audio arrived, and any error
   event (Open WebUI ends the call on one).

Pass: the streamed sentences are spoken in order, audio starts well before the final result is sent, the final
response says nothing already said and no narration, and there are no errors.
"""
import asyncio
import base64
import io
import json
import sys
import time
import urllib.request
import wave

import websockets

BRIDGE, TTS, VOICE, PROMPT = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/"), sys.argv[3], sys.argv[4]
QUESTION = "Who directed the original Halloween?"
NARRATION = "Let me look that up."
STREAM = ["John Carpenter directed the original Halloween in 1978.",
          " He co-wrote it with Debra Hill and composed its score himself.",
          " Jamie Lee Curtis played Laurie Strode in her film debut."]
ANSWER = NARRATION + "\n" + "".join(STREAM)

CHAT_TOOL = {"type": "function", "name": "generate_chat_completion",
             "description": "Handle substantive questions and tasks using the selected chat model.",
             "parameters": {"type": "object", "properties": {"request": {"type": "string"}},
                            "required": ["request"], "additionalProperties": False}}


def speech_pcm(text):
    req = urllib.request.Request(TTS + "/audio/speech", data=json.dumps(
        {"model": "kokoro-82m", "input": text, "voice": VOICE, "response_format": "wav"}).encode(),
        headers={"Content-Type": "application/json"})
    with wave.open(io.BytesIO(urllib.request.urlopen(req, timeout=60).read())) as w:
        assert w.getframerate() == 24000 and w.getsampwidth() == 2 and w.getnchannels() == 1
        return w.readframes(w.getnframes())


async def main():
    pcm = speech_pcm(QUESTION) + b"\0" * 48000 * 2  # two seconds of silence ends the turn
    t0 = time.monotonic()
    now = lambda: round(time.monotonic() - t0, 2)
    responses, errors, log = {}, [], []
    async with websockets.connect(BRIDGE + "/realtime?model=voice", max_size=None,
                                  additional_headers={"Authorization": "Bearer none"}) as ws:
        send = lambda e: ws.send(json.dumps(e))
        assert json.loads(await ws.recv())["type"] == "session.created"
        await send({"type": "session.update", "session": {
            "type": "realtime", "output_modalities": ["audio"], "instructions": open(PROMPT).read(),
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000},
                                "transcription": {"model": "parakeet-tdt-0.6b-v3"},
                                "turn_detection": {"type": "server_vad", "interrupt_response": True,
                                                   "create_response": False}},
                      "output": {"format": {"type": "audio/pcm", "rate": 24000}, "voice": VOICE}},
            "tools": [CHAT_TOOL], "tool_choice": "auto"}})
        assert json.loads(await ws.recv())["type"] == "session.updated"

        state = {"call_id": None, "active": set()}

        async def reader():
            async for raw in ws:
                e = json.loads(raw)
                k = e.get("type", "")
                if k == "error":
                    errors.append((now(), e.get("error")))
                elif k == "conversation.item.input_audio_transcription.completed":
                    log.append((now(), "heard", e.get("transcript")))
                    await send({"type": "response.create", "response": {"metadata": {"input_item_id": e["item_id"]}}})
                elif k == "response.created":
                    r = e["response"]
                    responses[r["id"]] = {"metadata": r.get("metadata") or {}, "created": now(), "first_audio": None, "said": ""}
                    state["active"].add(r["id"])
                elif k == "response.output_audio.delta":
                    r = responses.get(e["response_id"])
                    if r and r["first_audio"] is None:
                        r["first_audio"] = now()
                elif k == "response.output_audio_transcript.done":
                    if e["response_id"] in responses:
                        responses[e["response_id"]]["said"] += e.get("transcript", "")
                elif k == "response.output_item.done" and (e.get("item") or {}).get("name") == "generate_chat_completion":
                    state["call_id"] = e["item"]["call_id"]
                    log.append((now(), "hand-off", e["item"].get("arguments")))
                elif k == "response.done":
                    state["active"].discard(e["response"]["id"])

        task = asyncio.create_task(reader())
        for i in range(0, len(pcm), 4800):  # 100 ms per append, in real time
            await send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[i:i + 4800]).decode()})
            await asyncio.sleep(0.1)
        for _ in range(300):
            if state["call_id"] and not state["active"]:
                break
            await asyncio.sleep(0.1)
        call_id = state["call_id"]
        if not call_id:
            sys.exit("FAIL: the voice model did not hand the question off")
        # The chat model writes; Open WebUI releases two sentences, then the third a second later.
        await asyncio.sleep(1.0)
        log.append((now(), "delta", STREAM[0] + STREAM[1]))
        await send({"type": "openwebui.function_call_output.delta", "call_id": call_id, "delta": STREAM[0] + STREAM[1]})
        await asyncio.sleep(1.0)
        log.append((now(), "delta", STREAM[2]))
        await send({"type": "openwebui.function_call_output.delta", "call_id": call_id, "delta": STREAM[2]})
        await asyncio.sleep(0.5)
        log.append((now(), "final result", ANSWER))
        await send({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": call_id,
            "output": json.dumps({"status": "completed", "answer": ANSWER})}})
        for _ in range(300):  # Open WebUI asks for the result's response only when nothing is playing
            if not state["active"]:
                break
            await asyncio.sleep(0.1)
        log.append((now(), "respond", call_id))
        await send({"type": "response.create", "response": {"tools": [], "tool_choice": "none", "metadata": {"call_id": call_id}}})
        await asyncio.sleep(1.0)
        for _ in range(300):
            if not state["active"]:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(1.0)
        task.cancel()

    for t, what, text in log:
        print(f"{t:6.2f} s  {what:12} {text!r}")
    print()
    for r in responses.values():
        print(f"response created {r['created']:6.2f} s, first audio {r['first_audio']} s, metadata {r['metadata']}\n    said {r['said']!r}")
    print("\nerrors:", errors or "none")


asyncio.run(main())
