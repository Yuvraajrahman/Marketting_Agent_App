"""Dual-tap speech-to-text with rolling transcript buffer."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any, Awaitable, Callable

import numpy as np

from backend.clients import client_for
from backend.config import AppConfig, ComputeLocation
from backend.services import emitter

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


def _mlx_repo_for(model: str) -> str:
    """Map whisper.cpp-style names to mlx-community HF repos."""
    key = model.strip().lower().replace("_", ".")
    mapping = {
        "tiny": "mlx-community/whisper-tiny-mlx",
        "tiny.en": "mlx-community/whisper-tiny.en-mlx",
        "base": "mlx-community/whisper-base-mlx",
        "base.en": "mlx-community/whisper-base.en-mlx",
        "small": "mlx-community/whisper-small-mlx",
        "small.en": "mlx-community/whisper-small.en-mlx",
        "medium": "mlx-community/whisper-medium-mlx",
        "medium.en": "mlx-community/whisper-medium.en-mlx",
    }
    if key in mapping:
        return mapping[key]
    if model.startswith("mlx-community/"):
        return model
    return f"mlx-community/whisper-{model}"


class TranscriptTurn:
    __slots__ = ("speaker", "text", "ts")

    def __init__(self, speaker: str, text: str, ts: float | None = None) -> None:
        self.speaker = speaker
        self.text = text
        self.ts = ts if ts is not None else time.time()

    def as_dict(self) -> dict[str, Any]:
        return {"speaker": self.speaker, "text": self.text, "ts": self.ts}


class STTService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.enabled = False
        self._running = False
        self._threads: list[threading.Thread] = []
        self._broadcast: BroadcastFn | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._buffer: deque[TranscriptTurn] = deque(maxlen=cfg.transcript_turns)
        self._whisper = None
        self._on_turn: Callable[[TranscriptTurn], Awaitable[None]] | None = None
        self.engine = "none"

    def attach(
        self,
        broadcast: BroadcastFn,
        loop: asyncio.AbstractEventLoop,
        on_turn: Callable[[TranscriptTurn], Awaitable[None]] | None = None,
    ) -> None:
        self._broadcast = broadcast
        self._loop = loop
        self._on_turn = on_turn

    def transcript_text(self) -> str:
        lines = []
        for t in self._buffer:
            label = "You" if t.speaker == "you" else "Client"
            lines.append(f"{label}: {t.text}")
        return "\n".join(lines)

    def recent_turns(self) -> list[dict[str, Any]]:
        return [t.as_dict() for t in self._buffer]

    def _load_whisper(self) -> bool:
        if self._whisper is not None:
            return True
        # Prefer mlx-whisper on Apple Silicon
        try:
            import mlx_whisper  # noqa: F401

            self._whisper = ("mlx", self.cfg.whisper_model)
            self.engine = "mlx-whisper"
            return True
        except Exception:
            pass
        try:
            from faster_whisper import WhisperModel

            device = "cpu"
            compute = "int8"
            if self.cfg.device == "cuda":
                device = "cuda"
                compute = "float16"
            self._whisper = WhisperModel(self.cfg.whisper_model, device=device, compute_type=compute)
            self.engine = "faster-whisper"
            return True
        except Exception as exc:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    emitter.status("stt", "warn", f"Whisper unavailable: {exc}"),
                    self._loop,
                )
            return False

    def _transcribe(self, audio: np.ndarray, sr: int) -> str:
        if self._whisper is None:
            return ""
        if isinstance(self._whisper, tuple) and self._whisper[0] == "mlx":
            import mlx_whisper
            import tempfile
            import soundfile as sf

            with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
                sf.write(tmp.name, audio, sr)
                result = mlx_whisper.transcribe(
                    tmp.name,
                    path_or_hf_repo=_mlx_repo_for(self.cfg.whisper_model),
                )
                return str(result.get("text", "")).strip()

        # faster-whisper
        segments, _ = self._whisper.transcribe(audio, language="en")
        return " ".join(s.text.strip() for s in segments).strip()

    def _emit_turn(self, speaker: str, text: str) -> None:
        if not text:
            return
        turn = TranscriptTurn(speaker, text)
        self._buffer.append(turn)
        msg = {"channel": "stt", "type": "transcript", "speaker": speaker, "text": text, "ts": turn.ts}
        if self._broadcast and self._loop:
            asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)
            if self._on_turn:
                asyncio.run_coroutine_threadsafe(self._on_turn(turn), self._loop)

    def start(self) -> dict[str, Any]:
        if not self.cfg.consent_granted:
            return {"ok": False, "error": "Consent required before starting STT / call capture"}

        if self.cfg.stt.location == ComputeLocation.REMOTE:
            return {"ok": True, "remote": True, "host": self.cfg.stt.host}

        if self._running:
            return {"ok": True, "already": True}

        if not self._load_whisper():
            return {"ok": False, "error": "No whisper engine installed (mlx-whisper or faster-whisper)"}

        self.enabled = True
        self._running = True
        for speaker, kind in (("you", "mic"), ("client", "loopback")):
            t = threading.Thread(target=self._listen_loop, args=(speaker, kind), daemon=True)
            t.start()
            self._threads.append(t)
        return {"ok": True, "engine": self.engine}

    async def start_async(self) -> dict[str, Any]:
        """Non-blocking start — model download/load runs in a worker thread."""
        if not self.cfg.consent_granted:
            return {"ok": False, "error": "Consent required before starting STT / call capture"}

        if self.cfg.stt.location == ComputeLocation.REMOTE:
            return {"ok": True, "remote": True, "host": self.cfg.stt.host}

        if self._running:
            return {"ok": True, "already": True}

        await emitter.status("stt", "info", "Loading whisper model (first run may download)…")
        ok = await asyncio.to_thread(self._load_whisper)
        if not ok:
            return {"ok": False, "error": "No whisper engine installed (mlx-whisper or faster-whisper)"}

        self.enabled = True
        self._running = True
        self._threads = []
        for speaker, kind in (("you", "mic"), ("client", "loopback")):
            t = threading.Thread(target=self._listen_loop, args=(speaker, kind), daemon=True)
            t.start()
            self._threads.append(t)
        return {"ok": True, "engine": self.engine}

    def stop(self) -> dict[str, Any]:
        self.enabled = False
        self._running = False
        self._threads = []
        return {"ok": True}

    def _device_index(self, sd, kind: str) -> int | None:
        devices = sd.query_devices()
        if kind == "loopback":
            hint = self.cfg.blackhole_name.lower()
            for i, d in enumerate(devices):
                name = str(d.get("name", "")).lower()
                if hint in name and d.get("max_input_channels", 0) > 0:
                    return i
            return None
        # mic: first non-blackhole input
        for i, d in enumerate(devices):
            name = str(d.get("name", "")).lower()
            if d.get("max_input_channels", 0) > 0 and "blackhole" not in name:
                return i
        try:
            return int(sd.default.device["input"])
        except Exception:
            return None

    def _listen_loop(self, speaker: str, kind: str) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            return

        sr = self.cfg.sample_rate
        # Accumulate ~2s windows for usable transcription latency
        window = sr * 2
        idx = self._device_index(sd, kind)
        if idx is None:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    emitter.status("stt", "warn", f"No audio device for {kind}"),
                    self._loop,
                )
            return

        buf = np.zeros(0, dtype=np.float32)
        try:
            with sd.InputStream(samplerate=sr, channels=1, dtype="float32", device=idx, blocksize=sr // 5) as stream:
                if self._loop:
                    asyncio.run_coroutine_threadsafe(
                        emitter.status("stt", "info", f"STT listening on {kind} ({self.engine})"),
                        self._loop,
                    )
                while self._running:
                    data, _ = stream.read(sr // 5)
                    mono = data[:, 0] if data.ndim > 1 else data
                    buf = np.concatenate([buf, mono.astype(np.float32)])
                    if len(buf) >= window:
                        chunk = buf[:window]
                        buf = buf[window // 4 :]  # overlap
                        # Skip near-silence
                        if float(np.sqrt(np.mean(chunk**2))) < 0.01:
                            continue
                        try:
                            text = self._transcribe(chunk, sr)
                            self._emit_turn(speaker, text)
                        except Exception as exc:
                            if self._loop:
                                asyncio.run_coroutine_threadsafe(
                                    emitter.status("stt", "warn", f"Transcribe error: {exc}"),
                                    self._loop,
                                )
        except Exception as exc:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    emitter.status("stt", "error", f"STT {kind} failed: {exc}"),
                    self._loop,
                )

    async def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        action = msg.get("action")
        if (
            self.cfg.stt.location == ComputeLocation.REMOTE
            and not msg.get("_force_local")
            and action not in (None, "status", "get_transcript")
        ):
            try:
                remote = client_for(self.cfg, "stt")
                return await remote.post_json("/stt", msg)
            except Exception as exc:
                await emitter.status("stt", "error", f"Remote STT failed: {exc}")
                return {"ok": False, "error": str(exc)}

        if action == "start":
            return await self.start_async()
        if action == "stop":
            return self.stop()
        if action == "get_transcript":
            return {"ok": True, "turns": self.recent_turns(), "text": self.transcript_text()}
        if action == "status":
            return {
                "ok": True,
                "enabled": self.enabled,
                "engine": self.engine,
                "consent": self.cfg.consent_granted,
                "compute": self.cfg.stt.model_dump(mode="json"),
            }
        return {"ok": False, "error": f"unknown stt action: {action}"}
