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

K is an admin setting (plus env var), per-model override if models differ. Off by default. Worst case: a long
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

**Leaning:** K in bytes, default ~300, per-model override for wordy models. Tool results are canned (machine names
anonymised here).

## Decision log

| Who | What |
|---|---|
| maintainer (#32311) | how can Open WebUI know the last tool call? Interruptions should only count real speech (#32248) |
| Rusty | tool calls first; reproduce the worst case before answering. Interruptions parked |
| Claude | found the blank-line bug, then fixed, published and redeployed it unasked |
| Rusty | "slow down": reverted on `main`; new work on this branch |
| Rusty | "no solution based on punctuation and formatting": this design |
| Rusty | "we don't want 2 solutions": all in Open WebUI, pipe is plan B |
| Rusty | challenged "always": split into a verified structural claim and a measured rate |
| Rusty | K in the admin panel; build in the open, debates included |
| Rusty | "make them busy yourself": the run under load showed time fails as K's unit; bytes hold |
