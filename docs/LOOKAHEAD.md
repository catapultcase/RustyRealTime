# Streaming hand-off answers without guessing: lookahead design (branch `lookahead`)

Work in progress, not proposed yet. Replaces the sentence-counting hold rule in [ARCHITECTURE.md](ARCHITECTURE.md),
which decides "narration or answer" from punctuation and line breaks. That is fragile: it broke on one model's blank
lines (found by the worst-case run, 2026-10-13), it does not know `。`, and every model formats differently.

**Principle: no punctuation or formatting rules.** Every decision comes from structure the model already produces, a
signal the model gives, or a format-free count. Where a part is a guess, the design says so and measures it.

## The problem

While the chat model writes text, nobody knows whether a tool call will follow: only the model does, and only once it
starts the call. Text followed by a tool call is narration ("I'll pull your network details and check pfSense VLANs").
Text not followed by one is the answer. Speaking early means deciding before the model has.

## Design: four layers

| Layer | Decides | Basis | Exact? |
|---|---|---|---|
| 1. Structure | reasoning and tool output are never spoken; only text after the latest tool result is a candidate; nothing more is released once a tool call starts | Open WebUI's output items (`reasoning`, `message`, `function_call`, `function_call_output`) | yes |
| 2. Lookahead | text is released only once the model has kept writing **K** more after it in the same step | narration is the last text before a tool call, so nothing follows it but the call; an answer keeps going | no: measured, one parameter |
| 3. Model signal | when the model marks text as commentary/preamble or final answer, that mark decides | model or API format (Harmony-style channels and similar) | yes, where available |
| 4. Contract | the chat model is asked not to write text before tool calls in voice turns | Open WebUI's existing Voice Mode Prompt | no: reduces narration at the source; layers 1-3 still apply |

Speech segmentation (where to split text for text-to-speech) stays the provider's job, as it already is for its TTS.

**The unit of K is open.** Candidates, all format-free:

| Unit | For | Against |
|---|---|---|
| tokens | uniform across languages | Open WebUI sees text, not tokens; delta size depends on server and the stream chunk setting |
| characters | always available | CJK packs far more meaning per character |
| time (ms of continued writing) | language- and tokenizer-free | depends on model speed |

The analysis below picks one.

## Where it lives (decided 2026-10-13)

**All four layers live in Open WebUI.** It is the only place that sees the structure (tool calls, reasoning) and any
model signal, and one implementation serves every provider: a provider only speaks what it receives.

**Plan B**, only if upstream won't own the lookahead: Open WebUI forwards text plus structural events (a dumb pipe) and
the provider applies the same lookahead. Same design, other place; only one of the two ships.

## Protocol

Open WebUI sends released text as `openwebui.function_call_output.delta {call_id, delta}` and the structural events the
provider may use ("tool call started", "answer complete"); the final `function_call_output` still carries the whole
answer, so a provider can always catch up. Off by default (`AUDIO_REALTIME_STREAM_RESULTS`); summarising setups are
unchanged.

**Failure mode, stated:** narration longer than K has its start spoken, a short true sentence. Nothing is lost or wrong,
and nothing more is streamed for that turn.

## Test plan (models are added as we go)

| | |
|---|---|
| Models | Qwen3.8-Flash-Next (TensorFold, a pair of DGX Sparks), GLM-5.3-Flash (TensorFold, a second pair), Qwen3.5-9B (llama.cpp, RTX 3080 Ti), Qwen3.8-27B (llama.cpp, RTX 5090, when that desktop is on) |
| Prompts | the ten multi-step prompts of the worst-case run, plus other languages (Chinese, Japanese, German) and formats (lists, headings, code) |
| Tools | the names and schemas Open WebUI gives the model; canned results that come back thin, so models go round again |
| Recorded | every content delta with its time, when each tool call starts, the step's text |
| Reported | per model: narration length (deltas, characters, ms); for each K: narration released, and speech start against today |

[`proof/narration_stress.py`](../proof/narration_stress.py) runs it; nothing in Open WebUI or the bridge changes until
the numbers pick K and its unit.

## Results

| Date | Run | Result |
|---|---|---|
| 2026-10-13 | worst case, sentence rule, Flash-Next + GLM, 40 runs | 26 narration lines before a tool call, every one a single sentence. Flash-Next ends narration with blank lines, which the sentence rule counted as sentences: the reason for this design |
| 2026-10-13 | lookahead, three models, 16 prompts (English, Chinese, Japanese, German; lists, tables, code), 96 runs | below |

**Is narration always the last text before a tool call?** In 251 steps that ended in a tool call (all three models, two
server types), text arrived after the tool call started **0** times. Evidence, not proof: the design stops releasing a
step's text once its tool call starts either way.

**Narration length:**

| Model | Narration lines | Characters (median / max) | First word to tool call (median / max) |
|---|---|---|---|
| Qwen3.8-Flash-Next | 20 | 85 / 165 | 0.41 s / 1.03 s |
| GLM-5.3-Flash | 30 | 98 / 177 | 0.75 s / 1.28 s |
| Qwen3.5-9B | 22 | 155 / 292 | 0.46 s / 0.72 s |

Deltas (stream pieces) are no unit: the servers chunk differently (median 8 for Flash-Next, 42 for the 9B, for similar
text).

**Narration spoken / median speech start saved, by K:**

| K | Flash-Next | GLM-5.3 | Qwen3.5-9B |
|---|---|---|---|
| 160 characters | 2/20, 1.9 s | 2/30, 5.0 s | 9/22, 1.4 s |
| 1.0 s of continued writing | 2/20, 1.4 s | 4/30, 4.7 s | 0/22, 0.8 s |
| **1.5 s of continued writing** | **0/20**, 0.9 s | **0/30**, 4.2 s | **0/22**, 0.3 s |

- **Time is the most consistent unit**: every model wrote its narration in under 1.3 s, however long it was in
  characters. At 1.5 s nothing was spoken on any model, language or format.
- **The gain grows with answer length**: 0.3-0.9 s on these short answers, 4.2 s on GLM's longer ones.
- **The sentence rule fails here too**: two-sentence narration (Flash-Next in German) and three-sentence narration (the
  9B: "The search results are returning placeholders… This suggests… Let me try…") were released 4 and 15 times.
- **Open: load.** A busy server writes narration more slowly, in time. Next: the same run with the servers under load.

The tool results are canned (anonymised for this repo: the runs used the author's own machine names).

## Decision log

What was debated and decided, including the author's steering, in the open.

| Date | Who | What |
|---|---|---|
| 2026-10-13 | maintainer (#32311) | asked how Open WebUI can know which tool call is the last; noted interruptions should only count real speech (#32248) |
| 2026-10-13 | Rusty | focus on the tool-call question first; reproduce the worst case before answering. The interruption point is parked |
| 2026-10-13 | Claude | the worst-case run found the blank-line bug; Claude fixed it, published and redeployed it without being asked |
| 2026-10-13 | Rusty | "slow down": the fix was reverted on `main`, and new work happens on this branch |
| 2026-10-13 | Rusty | "we can not do a solution that is based on punctuation and formatting": this design |
| 2026-10-13 | Rusty | "we don't want 2 solutions": everything in Open WebUI; the provider-side pipe is plan B |
| 2026-10-13 | Rusty | challenged "narration is *always* the last text before a tool call": split into a structural claim (verified, 0 in 251) and a measured one (how often narration outlasts K) |
| 2026-10-13 | Rusty | K is an admin setting with an environment variable; a per-model override if models differ |
| 2026-10-13 | Rusty | build in the open: progress, debates and steering go on this branch |
