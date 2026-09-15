# Live Presence App — Build Plan

**Target machine:** MacBook Air M4, 16GB unified memory, fanless
**Fallback machine:** Ubuntu desktop, RX 9060 XT 16GB VRAM, ROCm installed
**Goal:** A local web app that, during a live video call, (1) swaps/enhances your face, (2) modifies your voice, and (3) listens to the conversation and surfaces suggested talking points on screen — all running locally, with the option to offload the heavy compute to the Ubuntu box over the LAN when the Mac is under too much load.

---

## 1. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend services | Python 3.11, FastAPI + WebSockets | Each AI component runs as its own small service; WebSockets give you the low-latency streaming you need for live video/audio |
| Face swap (face only) | FaceFusion or Deep-Live-Cam, using InsightFace's `inswapper_128` model + GFPGAN/CodeFormer for sharpening | Real-time capable via CoreML on the Neural Engine, no CUDA needed. Swaps facial features only — your real hairline/head shape stays visible, see note in 3.1 |
| Full head/hair coverage (different category of tool) | A driven avatar instead of a swap: VTube Studio or Animaze running a VRM/Live2D model with its own hair, animated by real-time webcam face tracking | The "full head with hair" swap models that exist (BFS Head, Segmind Faceswap v5, etc.) are diffusion image editors — seconds per frame, not real-time video. An avatar rig sidesteps that since it's just landmark tracking driving a pre-made character |
| Voice conversion | RVC (via the Applio fork, actively maintained) on PyTorch's MPS backend | Real-time voice-to-voice conversion; MPS backend runs on Apple GPU |
| Speech-to-text | whisper.cpp (or its MLX port, `mlx-whisper`), small/base model | Runs efficiently on-device, low memory footprint |
| Conversation assistant | LM Studio's local server (OpenAI-compatible API at `127.0.0.1:1234`), whatever model is currently loaded | You're already running models through LM Studio — reuse its server instead of loading a model separately in-process. Swapping models is then a GUI action, no code changes. The Qwen3-4B-2507 (4-bit, 2.28GB) you've got loaded now is a good fit size-wise given the other three services sharing RAM |
| Virtual camera output | OBS Virtual Camera | Lets any meeting app (Zoom, Meet, Teams) pick up the processed feed as a normal webcam |
| Virtual audio output | BlackHole (2ch) | Routes converted voice output back into the meeting app's mic input; also used to capture the client's side of the call for transcription |
| Frontend | React + Vite, plain WebSocket/WebRTC client | Live preview, control panel, suggestions panel |
| Compute mode switch | Per-service toggle, set at runtime from the UI (see `ComputeSettings.tsx`) | Each service's client accepts a `host` (default `127.0.0.1`) — flipping it to the PC's LAN IP is a UI action, not a restart |

---

## 2. Project structure

```
live-presence/
├── backend/
│   ├── services/
│   │   ├── face_service.py        # webcam in -> face-swapped frames out
│   │   ├── voice_service.py       # mic in -> converted audio out
│   │   ├── stt_service.py         # mic + system audio -> live transcript
│   │   └── assistant_service.py   # transcript -> suggested phrasing
│   ├── gateway.py                 # single WebSocket entrypoint the frontend talks to, fans out to services
│   ├── config.py                  # per-service compute location (local/remote host), model paths, device selection
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── LivePreview.tsx    # processed video feed
│   │   │   ├── ControlPanel.tsx   # sliders: pitch, face model, background
│   │   │   ├── ComputeSettings.tsx # per-service Local/Remote toggle + PC IP field
│   │   │   └── SuggestionsPanel.tsx  # live assistant text
│   │   ├── hooks/useLiveSocket.ts
│   │   └── App.tsx
│   └── package.json
├── models/                        # downloaded model weights (gitignored)
└── README.md
```

---

## 3. Component specs

### 3.1 Face service
Two different approaches, depending on whether hair/hairline coverage matters to you:

**Path A — face-only swap (real-time capable) — building this first:**
- Input: webcam frames (via `cv2.VideoCapture` or the browser's `getUserMedia` piped down over WebSocket)
- Processing: FaceFusion or Deep-Live-Cam running InsightFace's `inswapper_128` model, with GFPGAN/CodeFormer as a sharpening pass
- Output: processed frames pushed to OBS Virtual Camera device
- Important: this swaps eyes/nose/mouth/jaw/skin inside the face region — your actual hairline and head silhouette from the webcam feed still show through. It doesn't address hair loss.

**Path B — full head/hair coverage (driven avatar, a different tool category) — deferred, come back to this later:**
- Input: webcam frames, run through real-time face-landmark tracking (a standard webcam works fine, no TrueDepth needed)
- Processing: tracked landmarks drive a pre-made VRM (3D) or Live2D (2D) avatar — built once with the head of hair you want — inside VTube Studio or Animaze
- Output: the avatar app's own virtual camera output, picked up by the meeting app the same way
- This is the only real-time-capable route that gives full control over the head/hair, since it's rendering a lightweight rigged character rather than trying to synthesize photoreal hair onto your real head every frame

Controls exposed to frontend either way: model/avatar selector, background mode (native macOS blur vs. custom image)

### 3.2 Voice service
- Input: mic audio stream, chunked (e.g. 320ms windows for low-latency RVC inference)
- Processing: RVC model inference on MPS
- Output: converted audio written to BlackHole input, which the meeting app selects as its mic
- Controls exposed to frontend: pitch shift (semitones), voice model selector, wet/dry mix

### 3.3 Speech-to-text service
- Input: two audio taps — your mic (your side) and BlackHole's output loopback (client's side, i.e. what's playing through your speakers/headphones)
- Processing: whisper.cpp/mlx-whisper streaming transcription, speaker-tagged by source
- Output: rolling transcript buffer, pushed to the assistant service and optionally shown as captions in the UI

### 3.4 Assistant service
- Input: rolling transcript (last N turns)
- Processing: calls LM Studio's OpenAI-compatible server, using whatever model is currently loaded — fetched dynamically each request so switching models in the LM Studio GUI just works, no restart needed:

```python
import httpx

LM_STUDIO = "http://127.0.0.1:1234/v1"

async def get_active_model() -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{LM_STUDIO}/models")
        models = r.json()["data"]
        if not models:
            raise RuntimeError("No model loaded in LM Studio")
        return models[0]["id"]

async def get_suggestion(transcript: str):
    model_id = await get_active_model()
    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream("POST", f"{LM_STUDIO}/chat/completions", json={
            "model": model_id,
            "messages": [
                {"role": "system", "content": "Suggest 1-2 short, natural phrases the salesperson could say next. Keep it brief."},
                {"role": "user", "content": transcript},
            ],
            "stream": True,
        }) as resp:
            async for line in resp.aiter_lines():
                yield line  # forward SSE chunks straight to the frontend
```

  Since LM Studio also runs on your Ubuntu box, `COMPUTE_MODE=remote` for this service is just swapping `127.0.0.1` for the Ubuntu machine's LAN IP — same API either way.
- Output: streamed suggestion text pushed to the frontend's suggestions panel
- Controls exposed to frontend: on/off toggle, suggestion style (e.g. "closing", "objection-handling", "rapport-building")

---

## 4. Data flow (WebSocket contract)

Frontend connects once to `gateway.py`, which multiplexes messages by `channel`:

```json
// Frontend -> Backend
{ "channel": "face", "action": "set_model", "model_id": "avatar_1" }
{ "channel": "voice", "action": "set_pitch", "semitones": -3 }
{ "channel": "assistant", "action": "toggle", "enabled": true }

// Switch where a service runs, at runtime — this is what ComputeSettings.tsx sends
{ "channel": "system", "action": "set_compute", "service": "assistant", "location": "remote", "host": "192.168.1.42" }

// Backend -> Frontend
{ "channel": "assistant", "type": "suggestion", "text": "..." }
{ "channel": "stt", "type": "transcript", "speaker": "client", "text": "..." }
{ "channel": "system", "type": "load", "cpu": 0.8, "mem_gb": 13.2 }
```

The `system/load` message matters here specifically because of the RAM budget — have the UI show a live meter and flip to a visible "switch to Ubuntu" prompt once memory pressure crosses a threshold (e.g. ~13GB), rather than waiting for it to actually stutter.

---

## 5. Build phases (feed these to Cursor one at a time)

1. **Scaffold** — FastAPI backend skeleton + React frontend skeleton, WebSocket gateway wired up with dummy echo responses.
2. **Face service** — get Deep-Live-Cam running standalone first (CLI), then wrap it as a service streaming to OBS Virtual Camera.
3. **Voice service** — same pattern: RVC standalone first, then wrapped as a service streaming to BlackHole.
4. **STT service** — whisper.cpp streaming from both audio taps, rolling transcript buffer.
5. **Assistant service** — MLX model loaded once at startup (not per-request), prompted from the transcript buffer, streamed back.
6. **Frontend polish** — control panel sliders wired to the actual services, live preview, suggestions panel, load meter.
7. **Compute-mode switch** — abstract each service behind a client interface that points at `localhost` or the Ubuntu box's LAN IP based on `COMPUTE_MODE`.

Building it in this order means you have something testable after every phase, rather than one big bang at the end.

---

## 6. Setup checklist

- `brew install blackhole-2ch`
- Install OBS, enable Virtual Camera
- Keep LM Studio running with its local server on (already set up, reachable at `http://127.0.0.1:1234`) — the assistant service just calls it over HTTP, no separate model loading in your own code
- `pip install fastapi uvicorn websockets httpx whisper-cpp-python torch torchvision`
- Set up FaceFusion (or Deep-Live-Cam) and RVC (Applio fork) per their own install docs — both have Apple Silicon-specific setup steps
- Full head/hair coverage (Path B in 3.1) is deferred for now — skip VTube Studio/Animaze setup until you revisit it
- Route your meeting app's camera → OBS Virtual Camera (or the avatar app's virtual camera), mic → BlackHole 2ch

## 7. One thing to decide before you ship this
The STT service captures the client's side of the call, not just yours. Some places require notice or consent before a call is recorded/processed like this — worth adding a simple consent toggle/log to the app now, and checking what applies wherever your clients are, rather than retrofitting it later.

## 8. Files/assets you'll need to download
Most of this is handled automatically by each tool's own setup script on first run — you're not hunting files down by hand for most of it:

- **Face swap:** FaceFusion/Deep-Live-Cam auto-downloads `inswapper_128.onnx` (~550MB) and a GFPGAN/CodeFormer restorer (~300-700MB) on first run. What you supply yourself: a clear, front-facing photo (or short video) of the face you want to swap to.
- **Voice conversion:** Applio's setup script auto-downloads the `hubert_base.pt` content-encoder (~180MB). For the voice model itself, either download a pretrained `.pth`/`.index` pair, or train your own — training needs ~10+ minutes of clean audio of the target voice.
- **Speech-to-text:** pick a whisper.cpp model size and pull it via the repo's `download-ggml-model.sh` script — `base.en` (~140MB) is a good starting point, `small.en` (~460MB) if you want better accuracy and can spare the memory.
- **LLM assistant:** nothing extra needed on the Mac — Qwen3-4B-2507 is already loaded in LM Studio. A bigger model on the PC side (below) is a separate download through LM Studio's own model browser.
- **Software (installs, not model files):** OBS, BlackHole, and the FaceFusion/Applio repos with their Python dependencies.

## 9. Offloading to the Ubuntu PC (9060 XT 16GB / i5-10400 / 32GB RAM)
Once both machines are on the same network, here's what's worth moving and in what order:

1. **LLM assistant — move this first.** LM Studio already runs on your PC with ROCm. Point the assistant service's `host` at the PC's LAN IP instead of `127.0.0.1` — same code, zero extra setup. And since the PC has 16GB of *dedicated* VRAM instead of memory shared with the OS, you can load a noticeably bigger model there (12-14B class instead of the 4B you're running locally) for better suggestions, without touching your Mac's RAM budget at all.
2. **Voice conversion — move this second.** RVC runs on PyTorch, and PyTorch+ROCm is already a proven path on your PC from your LLM work. Low friction, frees up real memory on the Mac.
3. **Face swap — possible, but expect more friction.** FaceFusion/Deep-Live-Cam run on ONNXRuntime, whose ROCm execution provider is far less mature than its CUDA one — it may quietly fall back to CPU on your PC instead of using the 9060 XT, which could end up slower than running it locally on the Mac's Neural Engine. Worth benchmarking before committing to the move.
4. **Speech-to-text — leave this on the Mac.** It's the lightest of the four (~1.5GB) and runs fine on CPU alone; offloading it saves the least and adds network latency to something that's already cheap locally.

This is what `ComputeSettings.tsx` and the `system/set_compute` message (sections 2 and 4) are for — a per-service Local/Remote toggle in the UI plus a field for the PC's LAN IP, so you can flip each piece over independently as you test what actually helps.
