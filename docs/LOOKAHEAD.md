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
| Lookahead | release text only once the model has kept writing for **K** after it; narration is followed by nothing but the call | measured |
| Model signal | if the model marks commentary vs final answer, use it | yes, where available |
| Contract | Voice Mode Prompt: no text before tool calls in voice turns | reduces it at the source |

K is an admin setting (plus env var), per-model override if models differ. Off by default. Worst case: a long
narration's start is spoken, a short true sentence.

## Results (96 runs, 3 models, English/Chinese/Japanese/German, lists/tables/code)

- **Narration comes right before the tool call:** text after a tool call started: **0 of 251** steps.
- **K's unit is time.** Narration took under 1.3 s on every model, though its length in characters varied 2×; stream
  chunk counts vary 5× between servers, so they are no unit.

| K | Qwen3.8-Flash-Next | GLM-5.3-Flash | Qwen3.5-9B |
|---|---|---|---|
| 1.0 s | 2/20 narration spoken, saves 1.4 s | 4/30, 4.7 s | 0/22, 0.8 s |
| **1.5 s** | **0/20**, saves 0.9 s | **0/30**, 4.2 s | **0/22**, 0.3 s |
| sentence rule (old) | 4/20 | 0/30 | 15/22 |

The gain grows with answer length. **Open:** a busy server writes narration slower; next run under load. Tool results
are canned (machine names anonymised here).

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
