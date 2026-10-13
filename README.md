# RustyRealTime (branch `lookahead`)

Open WebUI Realtime voice calls that start speaking a long answer while the chat model is still writing it.
Request: [open-webui#32311](https://github.com/open-webui/open-webui/issues/32311). Work in progress.

https://github.com/user-attachments/assets/dd96054b-d518-41f0-b179-80e6e99884dc

| Problem | Solution |
|---|---|
| The voice side gets the answer only when it's complete: long answers mean silence | opt-in streaming: answer text goes to the voice provider while it's written |
| Text before a tool call is narration ("let me search more") and shouldn't be spoken; nobody knows a tool call is coming until it starts | only text after the latest tool result, held back until the model has written 400 more bytes after it; in every test narration came straight before the call and was shorter than that, so it was held back (a longer one could be partly spoken) |
| Rules based on punctuation break across models, languages and formats | none used: structure and a byte count only |
| Summarising voice setups need the whole answer | off by default; nothing changes unless switched on |
| Providers that don't know the streamed event would reject it, ending the call | streams only to a provider that announces `openwebui_stream_results` |
| Talking over a streamed answer | stops it, as today |

**Settings** (Admin → Audio → Voice calls): *Stream answers to the voice provider* (`AUDIO_REALTIME_STREAM_RESULTS`)
and *Streaming lookahead (bytes)* (`AUDIO_REALTIME_STREAM_LOOKAHEAD_BYTES`, default 400).

**Tested:** 256 agent runs, 7 model set-ups, 4 languages, lists/tables/code, idle and under load: 154 of 155 narration
lines held back (99.4%); speech starts 1-8 s
sooner. Details: [docs/LOOKAHEAD.md](docs/LOOKAHEAD.md).

## Try it

1. **Open WebUI with the patch** (Docker):

   ```bash
   git clone https://github.com/open-webui/open-webui.git && cd open-webui && git checkout 93d25db
   curl -sL https://raw.githubusercontent.com/catapultcase/RustyRealTime/lookahead/poc/open-webui-stream-results.patch | git apply
   sed -i 's|^# ENV NODE_OPTIONS=.*|ENV NODE_OPTIONS="--max-old-space-size=8192"|' Dockerfile   # the frontend build needs it
   docker build -t open-webui-stream .
   ```

   Run it like any Open WebUI image ([`poc/run-test-instance.sh`](poc/run-test-instance.sh) presets the call settings).
2. **A voice provider that handles streamed text:** OpenAI's Realtime API doesn't (yet). Set up the
   [recipe's `lookahead` branch](https://github.com/catapultcase/rustys-fully-local-realtime-chat-via-open-webui/tree/lookahead):
   HF speech-to-speech with its patch, all local.
3. **Admin → Settings → Audio → Voice calls:** Call mode *Realtime*, the provider's URL, then *Stream answers to the voice
   provider* on.

| | |
|---|---|
| Open WebUI change | [patch](poc/open-webui-stream-results.patch): 6 files, 131 lines, call code and settings only, plus a vitest file |
| Voice provider | [recipe `lookahead` branch](https://github.com/catapultcase/rustys-fully-local-realtime-chat-via-open-webui/tree/lookahead) (HF speech-to-speech + patch) |
| Test scripts | [proof/](proof) |
| Earlier design (`main`) | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |

MIT ([LICENSE](LICENSE)).
