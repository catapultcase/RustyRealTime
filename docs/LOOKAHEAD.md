# Lookahead: streaming answers without guessing (branch `lookahead`)

Work in progress. Replaces the sentence-counting hold rule in [ARCHITECTURE.md](ARCHITECTURE.md): guessing "narration
or answer" from punctuation broke on one model's blank lines, doesn't know `。`, and every model formats differently.
**Rule for this design: no punctuation or formatting rules.**

## Problem

While the model writes, nobody knows if a tool call follows. Text before a tool call is narration ("I'll check your
network"); text without one is the answer.

## Design (all in Open WebUI; a provider-side pipe is plan B)

| Layer | What | Exact? |
|---|---|---|
| Structure | never speak reasoning or tool output; only text after the latest tool result; stop releasing once a tool call starts | yes |
| Lookahead | release text only once the model has written **K** more bytes after it; narration is followed by nothing but the call | measured |
| Model signal | if the model marks commentary vs final answer, use it | yes, where available |
| Contract | Voice Mode Prompt: no text before tool calls in voice turns | reduces it at the source |

K is an admin setting (plus env var), one global value. Off by default. Worst case: a long
narration's start is spoken, a short true sentence.

## Results

**Runs:** 96 idle (3 models; English, Chinese, Japanese, German; lists, tables, code) and 32 with both Spark pairs at
their 4-request limit ([`proof/load.py`](../proof/load.py)).

- **Narration comes right before the tool call:** text after a tool call started, **0 of 251** steps.
- **Time fails under load.** Narration took ≤1.3 s idle but up to 2.7 s under load, so K = 1.5 s released 7/15 (GLM).
- **Text amount doesn't change with load. Counted in UTF-8 bytes it evens out scripts:** GLM's Chinese narration is
  31 characters but 93 bytes, against 98 bytes in English.

| K = 300 bytes | Narration spoken | Speech starts sooner (median) |
|---|---|---|
| Qwen3.8-Flash-Next, idle / loaded | 0/20 / 0/10 | 1.3 s / 2.1 s |
| GLM-5.3-Flash, idle / loaded | 0/30 / 0/15 | 4.5 s / 9.3 s |
| Qwen3.5-9B, idle | 2/22 (wordy: up to 405 bytes in Japanese) | 1.2 s |
| sentence rule (old), idle | 4/20, 0/30, 15/22 | |

**Model speed:** filler detection doesn't depend on it: K counts what the model wrote, not how long it took (time
failed under load, bytes held). The gain does: speech starts after ~400 bytes (~100 tokens) of answer, about 2 s at 60
tok/s and 10 s at 10 tok/s, always sooner than the whole answer; answers under K are spoken at the end, as today.

**Per-model override? Not needed.** One global K = 410 bytes speaks no narration on any model, and costs the Spark
models 0.4-1.1 s of their gain against K = 300 (Flash-Next 0.9 / 1.3 s, GLM 4.0 / 8.2 s, 9B 1.1 s sooner, idle /
loaded). **Decided: K in UTF-8 bytes, one global default ~400, no override until a model needs one.** Recheck as
models are added (the 9B's Japanese case is 3 samples). Tool results are canned (machine names anonymised here).

## Build status

- **Open WebUI** ([patch](../poc/open-webui-stream-results.patch), 7 files, 146 lines): sentence rule removed; text after
  the latest tool call is released minus the last `AUDIO_REALTIME_STREAM_LOOKAHEAD_BYTES` (default 400, admin setting).
  Unit-tested: narration held, CJK never cut mid-character.
- **Bridge** ([recipe `lookahead` branch](https://github.com/catapultcase/rustys-fully-local-realtime-chat-via-open-webui/tree/lookahead)): speaks released text in whole sentences by its own TTS splitter; the rest waits for more or the final
  result. [`bridge_stream_test.py`](../proof/bridge_stream_test.py): each sentence once, in order, no errors.
- **Follow-ups after a streamed answer** ([`followup_test.py`](../proof/followup_test.py), Qwen3.5-9B voice model, 40
  trials): handed off 20/20 unstreamed, 18/20 streamed. Both misses answered from the earlier answer. Streamed
  sentences stay out of the voice model's history until the final result is spoken, which can come after the
  follow-up; 2 in 20 is not yet a clear difference.
- **Next**: live worst-case call on the test instance.
