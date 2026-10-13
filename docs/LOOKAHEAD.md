# Lookahead (branch `lookahead`, work in progress)

Replaces the sentence-counting hold rule in [ARCHITECTURE.md](ARCHITECTURE.md). No punctuation or formatting rules.

**Problem:** while the model writes, nobody knows if a tool call follows. Text before a tool call is narration ("I'll
check your network"); text without one is the answer.

## Design (in Open WebUI; a provider-side pipe is plan B)

| Layer | What |
|---|---|
| Structure | never speak reasoning or tool output; only text after the latest tool result; stop once a tool call starts |
| Lookahead | release text only once the model has written **K** more UTF-8 bytes after it; narration is followed only by the call |
| Model signal | if the model marks commentary vs final answer, use it |
| Contract | Voice Mode Prompt: no text before tool calls in voice turns |

K: admin setting + `AUDIO_REALTIME_STREAM_LOOKAHEAD_BYTES`, default 400, off unless streaming is on. Worst case: a long
narration's start is spoken. Detection doesn't depend on model speed; the gain does (speech starts ~100 tokens into the
answer), and it is never later than today.

## Tests

**Automated** ([`proof/`](../proof); canned tool results, machine names anonymised):

| Test | Result |
|---|---|
| 96 runs: 3 models, English/Chinese/Japanese/German, lists/tables/code | text after a tool call started: 0 of 251 steps |
| narration spoken, K = 400 bytes, idle and under load | 0 on all models (old sentence rule: 4/20, 0/30, 15/22) |
| K in time instead | fails under load (narration up to 2.7 s against 1.3 s idle) |
| speech starts sooner, K = 400 bytes | 0.9-1.3 s Flash-Next, 4.0-8.2 s GLM-5.3, 1.1 s Qwen3.5-9B |
| bridge: streamed sentences | each spoken once, in order, no errors |
| follow-ups after an answer (9B voice model, 40 trials) | handed off 20/20 unstreamed, 18/20 streamed: not yet a clear difference |

**Manual** (live calls, Flash-Next chat model, Qwen3.5-9B voice model):

| Call | Result |
|---|---|
| lab check, then VLAN advice (tool calls) | no narration spoken; answer streamed in whole sentences. Follow-up missed: the voice model said "I'll check" without handing off |
| lab overview with recommendations (2 tool calls) | no narration spoken; answer streamed sentence by sentence, about 1 s apart |
| follow-up: VLAN advice and a joke | handed off; answer streamed |

## Build

- **Open WebUI:** [patch](../poc/open-webui-stream-results.patch) against dev `93d25db`, 7 files, 146 lines.
- **Bridge:** [recipe `lookahead` branch](https://github.com/catapultcase/rustys-fully-local-realtime-chat-via-open-webui/tree/lookahead):
  speaks released text in whole sentences by its own TTS splitter.
- **Open:** streamed sentences reach the voice model's history only with the final result; may matter for follow-ups.
