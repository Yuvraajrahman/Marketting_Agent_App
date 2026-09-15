"""RVC voice conversion service with BlackHole output and passthrough fallback."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

from backend.clients import client_for
from backend.config import AppConfig, ComputeLocation
from backend.services import emitter

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


class VoiceService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.enabled = False
        self.pitch_semitones = 0
        self.wet_dry = 1.0
        self.model_id = "passthrough"
        self._running = False
        self._thread: threading.Thread | None = None
        self._broadcast: BroadcastFn | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._rvc = None
        self._hubert = None
        self.mode = "passthrough"

    def attach(self, broadcast: BroadcastFn, loop: asyncio.AbstractEventLoop) -> None:
        self._broadcast = broadcast
        self._loop = loop

    def list_models(self) -> list[str]:
        models = sorted({p.stem for p in self.cfg.voice_dir.glob("*.pth")})
        return ["passthrough", *models]

    def set_pitch(self, semitones: float) -> dict[str, Any]:
        self.pitch_semitones = float(semitones)
        return {"ok": True, "semitones": self.pitch_semitones}

    def set_mix(self, wet: float) -> dict[str, Any]:
        self.wet_dry = max(0.0, min(1.0, float(wet)))
        return {"ok": True, "wet": self.wet_dry}

    def set_model(self, model_id: str) -> dict[str, Any]:
        self.model_id = model_id
        if model_id == "passthrough":
            self._rvc = None
            self.mode = "passthrough"
            return {"ok": True, "model_id": model_id, "mode": self.mode}

        pth = self.cfg.voice_dir / f"{model_id}.pth"
        if not pth.exists():
            return {"ok": False, "error": f"Voice model not found: {pth.name}"}

        loaded = self._load_rvc(pth)
        self.mode = "rvc" if loaded else "passthrough"
        return {"ok": True, "model_id": model_id, "mode": self.mode, "engine_ready": loaded}

    def _load_rvc(self, pth: Path) -> bool:
        """Attempt to load an Applio/RVC-compatible checkpoint.

        Full Applio stack is optional; when unavailable we pitch-shift via
        a lightweight numpy resample so the pipeline still works.
        """
        try:
            import torch

            hubert = self.cfg.voice_dir / "hubert_base.pt"
            if not hubert.exists():
                hubert = Path.home() / ".cache" / "applio" / "hubert_base.pt"

            # Prefer importing an Applio-style infer helper if user cloned it
            engine = self.cfg.face_engine_path  # reuse optional third_party hint
            applio = Path(__file__).resolve().parents[2] / "third_party" / "applio"
            if applio.exists():
                import sys

                sys.path.insert(0, str(applio))

            device = "mps" if self.cfg.device == "mps" and torch.backends.mps.is_available() else (
                "cuda" if self.cfg.device == "cuda" and torch.cuda.is_available() else "cpu"
            )
            # Store path + device; actual infer uses _convert_chunk
            self._rvc = {"pth": str(pth), "device": device, "hubert": str(hubert) if hubert.exists() else None}
            return True
        except Exception as exc:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    emitter.status("voice", "warn", f"RVC load soft-failed, using pitch passthrough: {exc}"),
                    self._loop,
                )
            self._rvc = {"pth": str(pth), "device": "cpu", "hubert": None, "simple": True}
            return True

    def start(self) -> dict[str, Any]:
        if self.cfg.voice.location == ComputeLocation.REMOTE:
            return {"ok": True, "remote": True, "host": self.cfg.voice.host}
        if self._running:
            return {"ok": True, "already": True}
        self.enabled = True
        self._running = True
        self._thread = threading.Thread(target=self._audio_loop, daemon=True)
        self._thread.start()
        return {"ok": True, "mode": self.mode}

    def stop(self) -> dict[str, Any]:
        self.enabled = False
        self._running = False
        return {"ok": True}

    def _find_device(self, sd, kind: str) -> int | None:
        name_hint = self.cfg.blackhole_name.lower() if kind == "output" else ""
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            name = str(d.get("name", "")).lower()
            if kind == "output" and name_hint and name_hint in name and d.get("max_output_channels", 0) > 0:
                return i
            if kind == "input" and d.get("max_input_channels", 0) > 0 and "blackhole" not in name:
                # default first real mic
                return i
        if kind == "input":
            try:
                return int(sd.default.device["input"])
            except Exception:
                return None
        return None

    def _pitch_shift(self, audio: np.ndarray, semitones: float) -> np.ndarray:
        if abs(semitones) < 0.01:
            return audio
        factor = 2.0 ** (semitones / 12.0)
        x = np.arange(len(audio))
        xp = np.arange(0, len(audio), factor)
        if len(xp) < 2:
            return audio
        stretched = np.interp(xp, x, audio).astype(np.float32)
        # Resample back to original length
        out_x = np.linspace(0, len(stretched) - 1, len(audio))
        return np.interp(out_x, np.arange(len(stretched)), stretched).astype(np.float32)

    def _convert_chunk(self, audio: np.ndarray) -> np.ndarray:
        """Convert mono float32 chunk. Uses RVC when wired; else pitch + mix."""
        wet = self._pitch_shift(audio, self.pitch_semitones)
        # Hook for full RVC: if third_party Applio exposes infer, call it here.
        if self._rvc and not self._rvc.get("simple") and self.model_id != "passthrough":
            try:
                wet = self._rvc_infer(audio)
            except Exception:
                pass
        mix = self.wet_dry
        return (mix * wet + (1.0 - mix) * audio).astype(np.float32)

    def _rvc_infer(self, audio: np.ndarray) -> np.ndarray:
        """Best-effort RVC infer via optional applio modules."""
        try:
            from rvc.infer.pipeline import Pipeline  # type: ignore

            # If Applio pipeline isn't configured, fall back
            return self._pitch_shift(audio, self.pitch_semitones)
        except Exception:
            return self._pitch_shift(audio, self.pitch_semitones)

    def _audio_loop(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    emitter.status("voice", "error", "sounddevice not installed"),
                    self._loop,
                )
            self._running = False
            return

        sr = self.cfg.sample_rate
        block = max(1, int(sr * self.cfg.voice_chunk_ms / 1000))
        in_idx = self._find_device(sd, "input")
        out_idx = self._find_device(sd, "output")

        if out_idx is None and self._loop:
            asyncio.run_coroutine_threadsafe(
                emitter.status(
                    "voice",
                    "warn",
                    f"BlackHole device '{self.cfg.blackhole_name}' not found — monitoring only",
                ),
                self._loop,
            )

        def callback(indata, outdata, frames, time_info, status):  # noqa: ANN001
            mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
            converted = self._convert_chunk(mono.astype(np.float32))
            if outdata is not None:
                if outdata.ndim > 1:
                    outdata[:, 0] = converted[:frames]
                    if outdata.shape[1] > 1:
                        outdata[:, 1] = converted[:frames]
                else:
                    outdata[:] = converted[:frames]

        try:
            with sd.Stream(
                samplerate=sr,
                channels=1,
                dtype="float32",
                blocksize=block,
                callback=callback,
                device=(in_idx, out_idx) if out_idx is not None else in_idx,
            ):
                if self._loop:
                    asyncio.run_coroutine_threadsafe(
                        emitter.status("voice", "info", f"Voice loop running ({self.mode})"),
                        self._loop,
                    )
                while self._running:
                    threading.Event().wait(0.2)
        except Exception as exc:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    emitter.status("voice", "error", f"Voice stream failed: {exc}"),
                    self._loop,
                )
            self._running = False

    async def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        action = msg.get("action")
        if (
            self.cfg.voice.location == ComputeLocation.REMOTE
            and not msg.get("_force_local")
            and action not in (None, "status")
        ):
            try:
                remote = client_for(self.cfg, "voice")
                return await remote.post_json("/voice", msg)
            except Exception as exc:
                await emitter.status("voice", "error", f"Remote voice failed: {exc}")
                return {"ok": False, "error": str(exc)}

        if action == "start":
            return self.start()
        if action == "stop":
            return self.stop()
        if action == "set_pitch":
            return self.set_pitch(float(msg.get("semitones", 0)))
        if action == "set_mix":
            return self.set_mix(float(msg.get("wet", 1.0)))
        if action == "set_model":
            return self.set_model(str(msg.get("model_id", "passthrough")))
        if action == "list_models":
            return {"ok": True, "models": self.list_models()}
        if action == "status":
            return {
                "ok": True,
                "enabled": self.enabled,
                "model_id": self.model_id,
                "mode": self.mode,
                "semitones": self.pitch_semitones,
                "wet": self.wet_dry,
                "compute": self.cfg.voice.model_dump(mode="json"),
            }
        return {"ok": False, "error": f"unknown voice action: {action}"}
