# Live Presence

Local web app for live video calls: face swap/enhance, voice conversion, dual-tap transcription, and LM Studio talking-point suggestions — with per-service local/remote compute switching.

## Stack

- **Backend:** Python 3.11+, FastAPI, WebSockets
- **Frontend:** React + Vite + TypeScript
- **Face:** InsightFace `inswapper_128` (+ optional pyvirtualcam / OBS Virtual Camera)
- **Voice:** RVC/Applio-compatible path on PyTorch MPS, output via BlackHole 2ch
- **STT:** mlx-whisper (Apple Silicon) or faster-whisper
- **Assistant:** LM Studio OpenAI-compatible API (`http://127.0.0.1:1234`)

## Quick start

### 1. System deps (macOS)

```bash
brew install blackhole-2ch
# Install OBS Studio and enable Virtual Camera
# Keep LM Studio running with Local Server on (port 1234)
```

### 2. Backend

```bash
cd /path/to/Marketting_Agent_App
python3.11 -m venv .venv   # 3.11 or 3.12 recommended (3.14 works for core deps; some ML wheels lag)
source .venv/bin/activate
pip install -r backend/requirements.txt

# Apple Silicon torch (optional, for RVC):
pip install torch torchvision

# Optional STT on Apple Silicon (preferred over faster-whisper):
pip install mlx-whisper

# Optional face swap engine:
pip install insightface onnxruntime pyvirtualcam

uvicorn backend.gateway:app --host 0.0.0.0 --port 8000 --reload
```

First STT start may download the Whisper model and take a minute; the UI stays responsive while it loads.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173

Or use `./scripts/dev.sh` to start both.

## Models

| Asset | Where to put it |
|---|---|
| Source face photo | `models/faces/<name>.jpg` |
| `inswapper_128.onnx` | `models/inswapper_128.onnx` (auto-downloaded by FaceFusion/Deep-Live-Cam on first run) |
| RVC voice `.pth` (+ `.index`) | `models/voice/<name>.pth` |
| `hubert_base.pt` | `models/voice/hubert_base.pt` (Applio setup) |
| Whisper | pulled by mlx-whisper / faster-whisper on first use (`base.en` default) |

Optional clones:

```bash
./scripts/setup_face.sh
./scripts/setup_voice.sh
```

## WebSocket contract

Connect to `ws://127.0.0.1:8000/ws`. Messages are JSON multiplexed by `channel`:

```json
{ "channel": "face", "action": "set_model", "model_id": "avatar_1" }
{ "channel": "voice", "action": "set_pitch", "semitones": -3 }
{ "channel": "assistant", "action": "toggle", "enabled": true }
{ "channel": "system", "action": "set_compute", "service": "assistant", "location": "remote", "host": "192.168.1.42" }
{ "channel": "system", "action": "set_consent", "granted": true }
```

Inbound examples: `assistant/suggestion`, `stt/transcript`, `system/load`, `face/frame`.

## Consent

STT captures the client side of the call (via BlackHole loopback). The UI requires an explicit consent toggle before STT can start; grants are timestamped in the backend log. Check local recording/consent rules for your clients.

## Compute offload (Ubuntu / ROCm)

In **Compute** settings, set the PC LAN IP and flip services to Remote:

1. **Assistant** first — points at LM Studio on the PC
2. **Voice** second — RVC on PyTorch+ROCm
3. **Face** — possible but ONNX ROCm may fall back to CPU; benchmark first
4. **STT** — keep local

## Meeting app routing

- Camera → OBS Virtual Camera (or pyvirtualcam device)
- Mic → BlackHole 2ch (converted voice)

## Project layout

```
backend/
  gateway.py
  config.py
  clients/
  services/
frontend/
models/
scripts/
```

See `live-presence-app-plan.md` for the full design.
