"""Single WebSocket gateway for the Live Presence app."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.config import config
from backend.services import emitter
from backend.services.assistant_service import AssistantService
from backend.services.face_service import FaceService
from backend.services.stt_service import STTService, TranscriptTurn
from backend.services.voice_service import VoiceService

face_service = FaceService(config)
voice_service = VoiceService(config)
stt_service = STTService(config)
assistant_service = AssistantService(config)

clients: set[WebSocket] = set()
_load_task: asyncio.Task | None = None


async def broadcast(message: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    data = json.dumps(message)
    for ws in list(clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


async def on_stt_turn(turn: TranscriptTurn) -> None:
    await assistant_service.on_transcript(stt_service.transcript_text())


async def load_loop() -> None:
    while True:
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None) / 100.0
        mem_gb = (mem.total - mem.available) / (1024**3)
        await broadcast(
            {
                "channel": "system",
                "type": "load",
                "cpu": round(cpu, 3),
                "mem_gb": round(mem_gb, 2),
                "mem_total_gb": round(mem.total / (1024**3), 2),
                "mem_percent": mem.percent,
                "warn": mem_gb >= config.mem_warn_gb,
            }
        )
        await asyncio.sleep(2.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _load_task
    loop = asyncio.get_running_loop()
    from backend.config import MODELS_DIR

    for d in (config.faces_dir, config.voice_dir, config.whisper_dir, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    emitter.attach(broadcast)
    face_service.attach(broadcast, loop)
    voice_service.attach(broadcast, loop)
    stt_service.attach(broadcast, loop, on_turn=on_stt_turn)
    assistant_service.attach(broadcast, loop)
    _load_task = asyncio.create_task(load_loop())
    yield
    if _load_task:
        _load_task.cancel()
    face_service.stop()
    voice_service.stop()
    stt_service.stop()


app = FastAPI(title="Live Presence", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "consent": config.consent_granted}


@app.post("/face")
async def remote_face(msg: dict[str, Any]) -> dict[str, Any]:
    msg = {**msg, "_force_local": True}
    result = await face_service.handle(msg)
    return result or {"ok": False}


@app.post("/voice")
async def remote_voice(msg: dict[str, Any]) -> dict[str, Any]:
    msg = {**msg, "_force_local": True}
    result = await voice_service.handle(msg)
    return result or {"ok": False}


@app.post("/stt")
async def remote_stt(msg: dict[str, Any]) -> dict[str, Any]:
    msg = {**msg, "_force_local": True}
    result = await stt_service.handle(msg)
    return result or {"ok": False}


@app.post("/assistant")
async def remote_assistant(msg: dict[str, Any]) -> dict[str, Any]:
    msg = {**msg, "_force_local": True}
    result = await assistant_service.handle(msg)
    return result or {"ok": False}


async def handle_system(msg: dict[str, Any]) -> dict[str, Any]:
    action = msg.get("action")
    if action == "set_compute":
        service = str(msg.get("service", ""))
        location = str(msg.get("location", "local"))
        host = msg.get("host")
        try:
            sc = config.set_compute(service, location, host)
            await emitter.status(
                "system",
                "info",
                f"{service} compute → {location}" + (f" ({host})" if host else ""),
            )
            return {"ok": True, "service": service, "compute": sc.model_dump(mode="json")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    if action == "set_consent":
        granted = bool(msg.get("granted", False))
        config.consent_granted = granted
        config.consent_at = datetime.now(timezone.utc).isoformat() if granted else None
        log_line = f"[consent] granted={granted} at={config.consent_at}\n"
        print(log_line.strip())
        try:
            from backend.config import ROOT

            log_path = ROOT / "consent.log"
            with log_path.open("a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass
        if not granted and stt_service.enabled:
            stt_service.stop()
        return {
            "ok": True,
            "granted": config.consent_granted,
            "at": config.consent_at,
        }

    if action == "get_config":
        return {
            "ok": True,
            "consent": config.consent_granted,
            "consent_at": config.consent_at,
            "mem_warn_gb": config.mem_warn_gb,
            "face": config.face.model_dump(mode="json"),
            "voice": config.voice.model_dump(mode="json"),
            "stt": config.stt.model_dump(mode="json"),
            "assistant": config.assistant.model_dump(mode="json"),
        }

    if action == "ping":
        return {"ok": True, "pong": True}

    return {"ok": False, "error": f"unknown system action: {action}"}


async def route_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    channel = msg.get("channel")
    if channel == "face":
        return await face_service.handle(msg)
    if channel == "voice":
        return await voice_service.handle(msg)
    if channel == "stt":
        return await stt_service.handle(msg)
    if channel == "assistant":
        return await assistant_service.handle(msg)
    if channel == "system":
        return await handle_system(msg)
    return {"ok": False, "error": f"unknown channel: {channel}"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    clients.add(ws)
    # Send initial config snapshot
    await ws.send_text(
        json.dumps(
            {
                "channel": "system",
                "type": "hello",
                "consent": config.consent_granted,
                "mem_warn_gb": config.mem_warn_gb,
            }
        )
    )
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"ok": False, "error": "invalid json"}))
                continue

            result = await route_message(msg)
            if result is not None:
                # Echo reply tied to request channel
                reply = {
                    "channel": msg.get("channel", "system"),
                    "type": "ack",
                    "action": msg.get("action"),
                    **result,
                }
                await ws.send_text(json.dumps(reply))
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


def main() -> None:
    import uvicorn

    uvicorn.run("backend.gateway:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
