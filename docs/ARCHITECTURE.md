# RustyRealTime: architecture

> The sentence-counting hold rule below is replaced by a byte lookahead: see
> [LOOKAHEAD.md](LOOKAHEAD.md), and the voice-transcript tag (Hardening, below) is dropped to keep the patch to call
> code. The rest of this document still applies.

Goal: in Open WebUI's Realtime calls, start speaking a hand-off's answer while the chat model is still
writing it, without speaking the narration between tool calls. Upstream request:
[open-webui#32311](https://github.com/open-webui/open-webui/issues/32311). Our call setup:
[rustys-fully-local-realtime-chat-via-open-webui](https://github.com/catapultcase/rustys-fully-local-realtime-chat-via-open-webui).

## How a hand-off works today (dev, 2026-10-12)

```
voice model ──function_call generate_chat_completion──▶ provider ─▶ backend ─▶ frontend (realtime.ts)
frontend: submitHandler() runs it as an ordinary chat turn (model, tools, history), streamed into the chat
frontend: message state reaches completed|failed|cancelled
          ─▶ bridge.result {call_id, status, answer = getOutputText(message.output)}      (realtime.ts result())
backend:  validates (status ∈ completed|failed|cancelled|deferred, answer ≤ 100,000 chars)
          ─▶ conversation.item.create function_call_output {status, answer}            (routers/audio/realtime.py)
frontend: bridge.respond {call_id} ─▶ backend ─▶ response.create {metadata.call_id}
provider: speaks it. Stock: the voice model rereads and paraphrases. Ours (patch-bridge.py): text straight to TTS, 0.19 s
```

Two facts that shape the design:

- **Nothing reaches the provider until the turn completes**, so speech waits for the whole answer.
- **`getOutputText` joins every `message` item in the turn**, so narration written between tool calls ("Let me
  actually query the lab.") is already in the answer today. The voice model drops it when it paraphrases; our
  word-for-word bridge speaks it.

## Design considerations from review (#32311)

Two points raised on the request shaped this design. Both are addressed below:

1. **Interim narration.** Agentic answers mix text with tool calls ("let me search some more", then another
   search). Only the answer itself should be spoken. → the hold rule and the structural narration filter.
2. **Concise speech.** Many setups want the voice model to summarise a long answer, which needs the whole text.
   → streaming is an opt-in for setups that read answers word for word; summarising setups are unchanged.

## Proposed design

| Piece | Change |
|---|---|
| Frontend `realtime.ts` | while the turn streams, watch `message.output`. A `message` item followed by a `function_call` is narration and is never sent. For the last item, apply the **hold rule**, then send each newly complete sentence as `bridge.result.delta`. On completion, send `bridge.result` as now |
| Hold rule | commit an item only once it holds N complete sentences with no tool call after it (`AUDIO_REALTIME_STREAM_MIN_SENTENCES`, default 2, from the replay below). Narration is one sentence and is followed by a call, so it is never committed. Structural only: no word lists |
| Backend `realtime.py` | accept `bridge.result.delta {call_id, delta}` only when `AUDIO_REALTIME_STREAM_RESULTS` is on (Admin → Audio → Voice calls, off by default). Forward it as `openwebui.function_call_output.delta {call_id, delta}`. The final `function_call_output` is unchanged and still carries the whole answer. `bridge.ready` tells the frontend the setting |
| Provider | opts in by handling the delta event: speak deltas as they arrive, then speak only the part of the final answer not yet spoken. Providers that don't support it leave the setting off and see no change |
| Interruptions | talking over a streamed answer ends it: the frontend sends no more deltas for that turn and records its result without speaking it; the provider drops any deltas still queued. The full answer stays in the chat and in the voice model's history |
| Gate | streaming only when the call reads answers **word for word**. Summarising setups keep today's path (consideration 2) |

Side benefit: the narration filter could also clean today's completed answers (a separate change).

## Hardening: spoken transcripts are tagged, not labelled

Open WebUI gives the chat model earlier spoken replies as assistant messages whose text starts with
`[Historical voice assistant transcript; not current task status]`. To the model that reads as its own reply style, and
it copies the line into answers, where a word-for-word provider speaks it. Filtering the string in each provider breaks
whenever the wording changes (the frontend already uses a second wording).

The patch moves the tag out of the text: the transcript is clean assistant content with the message `name` `voice`, and
one system note (appended to the system message, so every chat template accepts it) says what that name means. Checked:
TensorFold and llama.cpp both accept `name` on assistant messages. Our bridge keeps its string filter only as a fallback
for unpatched Open WebUI.

## Proof plan

1. **History and replay ([`proof/`](../proof)), no Open WebUI change.** `export.py` reads real tool-using answers from
   `webui.db` read-only. `replay.py` replays each answer's last step against the chat model, timestamps
   every token and tool boundary, and reports:
   - speech start today (end of answer + 0.19 s) against the hold rule (first committed sentence + Kokoro's 0.1 s);
   - how much narration each rule would have spoken (today: all of it).
2. **Prototype.** Patch `realtime.ts` and `realtime.py` in a test Open WebUI built from source, teach our
   bridge `in_progress`, and make real calls. Record one, and post the numbers and the recording on #32311.

## Results

| Date | What | Result |
|---|---|---|
| 2026-10-12 | our chat history, Qwen3.8-Flash-Next (two DGX Sparks) | 34 tool turns: 8 with narration as message items (2,612 chars), all spoken word for word today. GLM-5.3-Flash: 1 of 5 |
| 2026-10-12 | replay of those 34 answers' last step, Flash-Next, `reasoning_effort` low | table below |

Hold rule against today (speech start = end of answer + 0.19 s). Prefill is in both columns, so the saving is what counts:

| Rule | Speech starts (median) | Saved median | Saved p90 | Narration spoken |
|---|---|---|---|---|
| today | 4.8 s | – | – | 14 of 14 |
| 1 sentence | 2.8 s | 2.3 s | 5.0 s | 7 of 14 |
| **2 sentences (default)** | **3.2 s** | **1.7 s** | **4.1 s** | **1 of 14**, and that one is a full answer the model wrote before saving it with `write_note`: correct to speak |
| 2 sentences + 120 chars | 3.8 s | 0.5 s | 2.9 s | 1 of 14 |
| 3 sentences | 3.5 s | 1.5 s | 4.0 s | 1 of 14 |

Answers here are short (median 613 characters, about 2.6 s of writing), so the saving grows with answer length: the p90
is the long answers.

## Worst case: narration before another tool call (2026-10-13)

Open WebUI can't know whether the model will call another tool after the text it is writing. [`proof/narration_stress.py`](../proof/narration_stress.py)
drives real agent loops with ten prompts built to provoke narration (multi-step research, "check A, then B, then C"),
with canned tool results that come back thin so the models go round again, and counts what the hold rule would have
streamed before another tool call.

| Model | Runs | Steps followed by a tool call | With narration text | Streamed, 1 sentence | Streamed, 2 sentences (default) |
|---|---|---|---|---|---|
| Qwen3.8-Flash-Next, `reasoning_effort` low | 20 | 42 | 10 | 10 | **0** |
| GLM-5.3-Flash, `reasoning_effort` high | 20 | 44 | 16 | 2 | **0** |

Every narration line was one sentence announcing the next action ("I'll pull your lab's network details and look up
pfSense VLAN setup at the same time."). The run also found a bug, now fixed: Flash-Next ends narration with blank lines,
and the rule counted each line break as a sentence, so one sentence plus two blank lines was released.

## Proof of concept (2026-10-13)

| Piece | Where | State |
|---|---|---|
| Open WebUI change | [`poc/open-webui-stream-results.patch`](../poc/open-webui-stream-results.patch), against dev `93d25db` | built and running on a test instance ([`run-test-instance.sh`](../poc/run-test-instance.sh)) |
| Provider support | `stream-results` in our recipe's [`patch-bridge.py`](https://github.com/catapultcase/rustys-fully-local-realtime-chat-via-open-webui/blob/main/bridge/patch-bridge.py) | live on our bridge |
| Provider check | [`proof/bridge_stream_test.py`](../proof/bridge_stream_test.py) | pass: streamed sentences spoken 0.11-0.15 s after they arrived, the final result added nothing already said and no narration, no error events |

Building the image needs `NODE_OPTIONS=--max-old-space-size=8192` (Open WebUI's Dockerfile has it commented out).
