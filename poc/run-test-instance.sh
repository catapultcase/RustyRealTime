#!/usr/bin/env bash
# Start a throwaway Open WebUI built from the stream-results branch, with Realtime calls preset (docs/ARCHITECTURE.md).
# Fresh data directory: open it, create the first account (it becomes admin), pick the chat model, start a call.
#
#   CHAT_URL=http://<chat-server>/v1 BRIDGE_URL=http://<bridge>:8765/v1 VOICE=am_onyx \
#   PROMPT=realtime-call-prompt.txt ./run-test-instance.sh [image] [port]
#
# VOICE_MODEL names the voice model on its replies in the chat (default qwen3.5-9b); our bridge picks its own model.
# The microphone needs HTTPS or localhost: on another machine, add http://<this-host>:<port> to
# chrome://flags/#unsafely-treat-insecure-origin-as-secure.
set -euo pipefail
IMAGE=${1:-open-webui-rrt:poc}
PORT=${2:-3010}
: "${CHAT_URL:?chat model server, OpenAI-compatible}" "${BRIDGE_URL:?Realtime provider}" "${VOICE:?provider voice}"
: "${PROMPT:?call prompt file}"
DATA=${DATA:-$HOME/owui-rrt-data}
mkdir -p "$DATA"
# The session-signing key lives with the data, so recreating the container for a new image keeps everyone signed in.
[ -s "$DATA/.webui_secret_key" ] || head -c 32 /dev/urandom | base64 > "$DATA/.webui_secret_key"
docker rm -f open-webui-rrt >/dev/null 2>&1 || true
docker run -d --name open-webui-rrt --restart unless-stopped -p "$PORT:8080" -v "$DATA:/app/backend/data" \
  -e OPENAI_API_BASE_URLS="$CHAT_URL" -e OPENAI_API_KEYS=none \
  -e ENABLE_OLLAMA_API=false \
  -e WEBUI_SECRET_KEY="$(cat "$DATA/.webui_secret_key")" \
  -e AUDIO_REALTIME_ENABLED=true \
  -e AUDIO_REALTIME_OPENAI_API_BASE_URL="$BRIDGE_URL" -e AUDIO_REALTIME_OPENAI_API_KEY=none \
  -e AUDIO_REALTIME_MODEL="${VOICE_MODEL:-qwen3.5-9b}" -e AUDIO_REALTIME_VOICE="$VOICE" \
  -e AUDIO_REALTIME_TRANSCRIPTION_MODEL="${TRANSCRIPTION_MODEL:-parakeet-tdt-0.6b-v3}" \
  -e REALTIME_CALL_PROMPT_TEMPLATE="$(cat "$PROMPT")" \
  -e AUDIO_REALTIME_STREAM_RESULTS=true -e AUDIO_REALTIME_STREAM_LOOKAHEAD_BYTES=400 \
  "$IMAGE"
echo "Open WebUI (stream-results) on :$PORT, data in $DATA"
