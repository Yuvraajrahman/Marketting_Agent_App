"""Face swap / enhance service — InsightFace inswapper with graceful passthrough."""

from __future__ import annotations

import asyncio
import base64
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

from backend.clients import client_for
from backend.config import AppConfig, ComputeLocation
from backend.services import emitter

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


class FaceService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.enabled = False
        self.model_id = "passthrough"
        self.background_mode = "native"
        self._source_face: np.ndarray | None = None
        self._swapper = None
        self._analyser = None
        self._restorer = None
        self._cam = None
        self._virtual_cam = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._broadcast: BroadcastFn | None = None
        self._latest_jpeg: bytes | None = None
        self._lock = threading.Lock()
        self.mode = "passthrough"  # passthrough | swap

    def attach(self, broadcast: BroadcastFn, loop: asyncio.AbstractEventLoop) -> None:
        self._broadcast = broadcast
        self._loop = loop

    def list_models(self) -> list[str]:
        faces = sorted(p.stem for p in self.cfg.faces_dir.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
        return ["passthrough", *faces]

    def set_model(self, model_id: str) -> dict[str, Any]:
        self.model_id = model_id
        if model_id == "passthrough":
            self._source_face = None
            self._source_faces = None
            self.mode = "passthrough"
            return {"ok": True, "model_id": model_id, "mode": self.mode}

        path = self._resolve_face(model_id)
        if not path or not path.exists():
            return {"ok": False, "error": f"Face image not found for model_id={model_id}"}

        if cv2 is None:
            return {"ok": False, "error": "opencv-python not installed"}

        img = cv2.imread(str(path))
        self._source_face = img
        self._source_faces = None
        loaded = self._ensure_swapper()
        self.mode = "swap" if loaded else "passthrough"
        return {"ok": True, "model_id": model_id, "mode": self.mode, "engine_ready": loaded}

    def set_background(self, mode: str) -> dict[str, Any]:
        self.background_mode = mode
        return {"ok": True, "background_mode": mode}

    def _resolve_face(self, model_id: str) -> Path | None:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = self.cfg.faces_dir / f"{model_id}{ext}"
            if p.exists():
                return p
        return None

    def _ensure_swapper(self) -> bool:
        if self._swapper is not None:
            return True
        try:
            import insightface
            from insightface.app import FaceAnalysis

            providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
            if self.cfg.device == "cuda":
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

            self._analyser = FaceAnalysis(name="buffalo_l", providers=providers)
            self._analyser.prepare(ctx_id=0, det_size=(640, 640))
            model_path = self.cfg.faces_dir.parent / "inswapper_128.onnx"
            # Also check common FaceFusion / Deep-Live-Cam locations
            candidates = [
                model_path,
                Path.home() / ".insightface" / "models" / "inswapper_128.onnx",
                Path(__file__).resolve().parents[2] / "models" / "inswapper_128.onnx",
            ]
            onnx = next((c for c in candidates if c.exists()), None)
            if onnx is None:
                if self._loop:
                    asyncio.run_coroutine_threadsafe(
                        emitter.status(
                            "face",
                            "warn",
                            "inswapper_128.onnx not found — face passthrough only. Place model in models/.",
                        ),
                        self._loop,
                    )
                return False

            self._swapper = insightface.model_zoo.get_model(str(onnx), providers=providers)
            return True
        except Exception as exc:  # pragma: no cover
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    emitter.status("face", "warn", f"Face engine unavailable: {exc}"),
                    self._loop,
                )
            return False

    def start(self, capture: str = "browser") -> dict[str, Any]:
        """Enable face pipeline.

        Default capture is ``browser`` (frames via WebSocket from getUserMedia)
        to avoid fighting the browser for the same webcam on macOS.
        Pass ``capture="device"`` to open OpenCV VideoCapture(0) instead.
        """
        if self.cfg.face.location == ComputeLocation.REMOTE:
            return {"ok": True, "remote": True, "host": self.cfg.face.host}

        if self.enabled and self._running and capture == getattr(self, "_capture", "browser"):
            return {"ok": True, "already": True}

        self.enabled = True
        self._capture = capture
        self._running = True

        if capture == "device":
            if self._thread and self._thread.is_alive():
                return {"ok": True, "already": True, "capture": "device"}
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
        else:
            # Browser frames only — do not open a second camera device.
            self._running = True

        return {"ok": True, "mode": self.mode, "capture": capture}

    def stop(self) -> dict[str, Any]:
        self.enabled = False
        self._running = False
        if self._cam is not None and cv2 is not None:
            try:
                self._cam.release()
            except Exception:
                pass
            self._cam = None
        if self._virtual_cam is not None:
            try:
                self._virtual_cam.close()
            except Exception:
                pass
            self._virtual_cam = None
        return {"ok": True}

    def process_browser_frame(self, jpeg_b64: str) -> dict[str, Any]:
        """Accept a browser getUserMedia JPEG frame, process, return preview."""
        if not self.enabled:
            return {"ok": False, "error": "face service not started"}
        if cv2 is None:
            return {"ok": False, "error": "opencv-python not installed"}

        raw = base64.b64decode(jpeg_b64)
        arr = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return {"ok": False, "error": "bad frame"}

        out = self._process_frame(frame)
        ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if not ok:
            return {"ok": False, "error": "encode failed"}
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        self._push_virtual(out)
        return {"ok": True, "jpeg": b64, "type": "frame"}

    def _ensure_source_faces(self) -> list[Any]:
        if getattr(self, "_source_faces", None) is not None:
            return self._source_faces  # type: ignore[return-value]
        if self._analyser is None or self._source_face is None:
            return []
        self._source_faces = self._analyser.get(self._source_face)
        return self._source_faces or []

    def _process_frame(self, frame: np.ndarray) -> np.ndarray:
        if self.mode != "swap" or self._swapper is None or self._analyser is None or self._source_face is None:
            return frame
        try:
            src_faces = self._ensure_source_faces()
            dst_faces = self._analyser.get(frame)
            if not src_faces or not dst_faces:
                return frame
            result = frame.copy()
            for face in dst_faces:
                result = self._swapper.get(result, face, src_faces[0], paste_back=True)
            return result
        except Exception:
            return frame

    def _push_virtual(self, frame: np.ndarray) -> None:
        if cv2 is None:
            return
        try:
            if self._virtual_cam is None:
                import pyvirtualcam

                h, w = frame.shape[:2]
                # Use RGB and send RGB frames — matches pyvirtualcam's common path
                self._virtual_cam = pyvirtualcam.Camera(
                    width=w,
                    height=h,
                    fps=30,
                    fmt=pyvirtualcam.PixelFormat.RGB,
                )
                if self._loop:
                    asyncio.run_coroutine_threadsafe(
                        emitter.status("face", "info", f"Virtual camera started: {self._virtual_cam.device}"),
                        self._loop,
                    )
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            self._virtual_cam.send(rgb)
            self._virtual_cam.sleep_until_next_frame()
        except Exception:
            # OBS / pyvirtualcam optional
            pass

    def _capture_loop(self) -> None:
        if cv2 is None:
            return
        self._cam = cv2.VideoCapture(0)
        if not self._cam.isOpened():
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    emitter.status("face", "error", "Could not open webcam (index 0)"),
                    self._loop,
                )
            self._running = False
            return

        while self._running:
            ok, frame = self._cam.read()
            if not ok:
                continue
            out = self._process_frame(frame)
            self._push_virtual(out)
            ok2, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if ok2 and self._broadcast and self._loop:
                b64 = base64.b64encode(buf.tobytes()).decode("ascii")
                msg = {"channel": "face", "type": "frame", "jpeg": b64}
                asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)
            # ~15 fps preview
            threading.Event().wait(1 / 15)

        if self._cam is not None:
            self._cam.release()
            self._cam = None

    async def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        action = msg.get("action")
        if (
            self.cfg.face.location == ComputeLocation.REMOTE
            and not msg.get("_force_local")
            and action not in (None, "status")
        ):
            try:
                remote = client_for(self.cfg, "face")
                result = await remote.post_json("/face", msg, timeout=60.0)
                # Forward remote preview frames to local UI clients
                if action == "frame" and result.get("jpeg") and self._broadcast:
                    await self._broadcast(
                        {"channel": "face", "type": "frame", "jpeg": result["jpeg"]}
                    )
                    return {"ok": bool(result.get("ok", True)), "preview": True}
                return result
            except Exception as exc:
                await emitter.status("face", "error", f"Remote face failed: {exc}")
                return {"ok": False, "error": str(exc)}

        if action == "start":
            capture = str(msg.get("capture", "browser"))
            return self.start(capture=capture)
        if action == "stop":
            return self.stop()
        if action == "set_model":
            return self.set_model(str(msg.get("model_id", "passthrough")))
        if action == "set_background":
            return self.set_background(str(msg.get("mode", "native")))
        if action == "list_models":
            return {"ok": True, "models": self.list_models()}
        if action == "frame":
            result = self.process_browser_frame(str(msg.get("jpeg", "")))
            if result.get("ok") and result.get("jpeg"):
                if self._broadcast:
                    await self._broadcast(
                        {"channel": "face", "type": "frame", "jpeg": result["jpeg"]}
                    )
                # HTTP remote callers need the jpeg body; WS acks stay small
                if msg.get("_force_local"):
                    return result
                return {"ok": True, "preview": True}
            return result
        if action == "status":
            return {
                "ok": True,
                "enabled": self.enabled,
                "model_id": self.model_id,
                "mode": self.mode,
                "background_mode": self.background_mode,
                "capture": getattr(self, "_capture", "browser"),
                "compute": self.cfg.face.model_dump(mode="json"),
            }
        return {"ok": False, "error": f"unknown face action: {action}"}
