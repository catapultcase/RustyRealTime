#!/usr/bin/env python3
"""Does streaming change whether the voice model hands off a follow-up? Plays Open WebUI's side over a real provider.

Each trial: a spoken question is handed off; the answer arrives either
  A  unstreamed: the final result is spoken at once, so the voice model's history holds the answer as said, or
  B  streamed: sentences go out as deltas, the final result item is created but not yet spoken (as when the user's
     follow-up jumps the queue);
then a spoken follow-up that needs the chat model. Records whether the voice model handed the follow-up off.

  python3 followup_test.py ws://<bridge>/v1 http://<tts>/v1 <voice> realtime-call-prompt.txt <reps> > followups.jsonl
"""
import asyncio
import base64
import io
import json
import sys
import urllib.request
import wave


QUESTION = "Check my home lab and tell me what you recommend for a VLAN setup."
ANSWER = ("Your only managed device is the pfSense box, and every switch in the path is unmanaged. The desk switch "
          "passes 802.1Q tags through, so VLANs work as one tagged trunk off pfSense's LAN port. Put cameras and "
          "smart plugs on an IoT VLAN, guests on their own VLAN, and keep servers on the main LAN.")
FOLLOWUPS = [
    "Confirm I have the right hardware for that, and explain it in one line.",
    "Are you sure? Check that again.",
    "What would a managed switch change? Look up a good one.",
    "How much would that switch cost right now?",
    "Which of my machines should go on the IoT VLAN?",
]
CHAT_TOOL = {"type": "function", "name": "generate_chat_completion",
             "description": "Handle substantive questions and tasks using the selected chat model.",
             "parameters": {"type": "object", "properties": {"request": {"type": "string"}},
                            "required": ["request"], "additionalProperties": False}}


def speech(text):
    req = urllib.request.Request(TTS + "/audio/speech", data=json.dumps(
        {"model": "kokoro-82m", "input": text, "voice": VOICE, "response_format": "wav"}).encode(),
        headers={"Content-Type": "application/json"})
    with wave.open(io.BytesIO(urllib.request.urlopen(req, timeout=60).read())) as w:
        return w.readframes(w.getnframes()) + b"\0" * 96000


async def trial(condition, followup, audio):
    import websockets  # only the run needs it, not --report

    async with websockets.connect(BRIDGE + "/realtime?model=voice", max_size=None,
                                  additional_headers={"Authorization": "Bearer none"}) as ws:
        send = lambda e: ws.send(json.dumps(e))
        assert json.loads(await ws.recv())["type"] == "session.created"
        await send({"type": "session.update", "session": {
            "type": "realtime", "output_modalities": ["audio"], "instructions": open(PROMPT).read(),
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000},
                                "transcription": {"model": "parakeet-tdt-0.6b-v3"},
                                "turn_detection": {"type": "server_vad", "interrupt_response": True, "create_response": False}},
                      "output": {"format": {"type": "audio/pcm", "rate": 24000}, "voice": VOICE}},
            "tools": [CHAT_TOOL], "tool_choice": "auto"}})
        assert json.loads(await ws.recv())["type"] == "session.updated"
        st = {"calls": [], "active": set(), "said": {}, "heard": []}

        async def reader():
            async for raw in ws:
                e = json.loads(raw)
                k = e.get("type", "")
                if k == "conversation.item.input_audio_transcription.completed" and e.get("transcript", "").strip():
                    st["heard"].append(e["transcript"])
                    await send({"type": "response.create", "response": {"metadata": {"input_item_id": e["item_id"]}}})
                elif k == "response.created":
                    st["active"].add(e["response"]["id"])
                elif k == "response.done":
                    st["active"].discard(e["response"]["id"])
                elif k == "response.output_audio_transcript.done":
                    st["said"][e["response_id"]] = st["said"].get(e["response_id"], "") + e.get("transcript", "")
                elif k == "response.output_item.done" and (e.get("item") or {}).get("name") == "generate_chat_completion":
                    st["calls"].append(e["item"]["call_id"])

        async def wait(pred, secs=60):
            for _ in range(secs * 10):
                if pred():
                    return True
                await asyncio.sleep(0.1)
            return False

        async def speak(pcm):
            for i in range(0, len(pcm), 4800):
                await send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[i:i + 4800]).decode()})
                await asyncio.sleep(0.1)

        task = asyncio.create_task(reader())
        await speak(audio[QUESTION])
        if not await wait(lambda: st["calls"] and not st["active"]):
            task.cancel()
            return {"condition": condition, "followup": followup, "first_handoff": False}
        call = st["calls"][0]
        result = {"type": "conversation.item.create", "item": {"type": "function_call_output", "call_id": call,
                  "output": json.dumps({"status": "completed", "answer": ANSWER})}}
        respond = {"type": "response.create", "response": {"tools": [], "tool_choice": "none", "metadata": {"call_id": call}}}
        if condition == "A":
            await send(result)
            await send(respond)
        else:
            half = ANSWER.index(" Put cameras")
            await send({"type": "openwebui.function_call_output.delta", "call_id": call, "delta": ANSWER[:half]})
            await asyncio.sleep(0.5)
            await send({"type": "openwebui.function_call_output.delta", "call_id": call, "delta": ANSWER[half:]})
            await asyncio.sleep(0.5)
            await send(result)
        await asyncio.sleep(1.0)
        await wait(lambda: not st["active"])
        before = len(st["calls"])
        await speak(audio[followup])
        await wait(lambda: len(st["heard"]) >= 2 and not st["active"], 40)
        await asyncio.sleep(1.5)
        await wait(lambda: not st["active"], 20)
        handed = len(st["calls"]) > before
        if condition == "B":
            await send(respond)
            await wait(lambda: not st["active"], 20)
        task.cancel()
        return {"condition": condition, "followup": followup, "first_handoff": True, "handed_off": handed,
                "heard": st["heard"][1:2], "said": list(st["said"].values())[-2:]}


async def main():
    audio = {t: speech(t) for t in [QUESTION] + FOLLOWUPS}
    for rep in range(REPS):
        for followup in FOLLOWUPS:
            for condition in ("A", "B"):
                for attempt in range(6):  # the provider takes one call at a time and frees it a moment after hang-up
                    await asyncio.sleep(3)
                    try:
                        print(json.dumps(await trial(condition, followup, audio)), flush=True)
                        break
                    except Exception as e:
                        err = repr(e)
                else:
                    print(json.dumps({"condition": condition, "followup": followup, "error": err}), flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--report":
        rows = [json.loads(l) for l in open(sys.argv[2])]
        for c in ("A", "B"):
            ok = [r for r in rows if r["condition"] == c and r.get("first_handoff")]
            print(f"{c}: follow-up handed off {sum(r['handed_off'] for r in ok)}/{len(ok)}"
                  f" (first question not handed off: {sum(1 for r in rows if r['condition'] == c and r.get('first_handoff') is False)},"
                  f" errors: {sum(1 for r in rows if r['condition'] == c and 'error' in r)})")
        sys.exit()
    BRIDGE, TTS, VOICE, PROMPT, REPS = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/"), sys.argv[3], sys.argv[4], int(sys.argv[5])
    asyncio.run(main())
