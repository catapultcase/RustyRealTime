# RustyRealTime

Making Open WebUI's Realtime voice calls start speaking a long answer while the chat model is still writing
it, without reading out the "let me search some more" in between.

A real call on the proof of concept: small talk from the voice model (`qwen3.5-9b`), then a question handed to the
chat model (`qwen3.8-flash-next`) and spoken while it is still being written
([MP4](docs/media/realtime-streaming-poc.mp4)):

https://github.com/user-attachments/assets/dd96054b-d518-41f0-b179-80e6e99884dc

The work is done in the open:

- **Request:** [open-webui#32311](https://github.com/open-webui/open-webui/issues/32311). The design builds on the
  points raised there: no spoken narration, and summarising setups unchanged.
- **Design and results:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). On this branch, the hold rule is replaced by a
  byte lookahead with no punctuation rules: [docs/LOOKAHEAD.md](docs/LOOKAHEAD.md).
- **Proof scripts:** [proof/](proof). `export.py` pulls real tool-using answers from an Open WebUI database
  (read-only); `replay.py` replays their last step against your chat model and reports when speech could
  start, and how much narration each hold rule would speak.
- **Our call setup:** [rustys-fully-local-realtime-chat-via-open-webui](https://github.com/catapultcase/rustys-fully-local-realtime-chat-via-open-webui).

```bash
ssh root@<openwebui-host> 'docker exec -i open-webui python3 -' < proof/export.py > proof/turns.jsonl
python3 proof/replay.py proof/turns.jsonl http://<chat-server>/v1 > proof/results.jsonl
python3 proof/replay.py --report proof/results.jsonl proof/turns.jsonl
```

The `.jsonl` files hold your own chats, so they are gitignored. Only the summaries are published.

MIT ([LICENSE](LICENSE)).
