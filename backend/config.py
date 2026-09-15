"""Runtime configuration for Live Presence services."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"


class ComputeLocation(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"


class ServiceCompute(BaseModel):
    location: ComputeLocation = ComputeLocation.LOCAL
    host: str = "127.0.0.1"
    port: int = 0


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LP_", env_file=".env", extra="ignore")

    device: Literal["mps", "cpu", "cuda"] = "mps"
    mem_warn_gb: float = 13.0
    transcript_turns: int = 12
    whisper_model: str = "base.en"
    faces_dir: Path = MODELS_DIR / "faces"
    voice_dir: Path = MODELS_DIR / "voice"
    whisper_dir: Path = MODELS_DIR / "whisper"
    face_engine_path: Path | None = None
    lm_studio_port: int = 1234
    blackhole_name: str = "BlackHole 2ch"
    sample_rate: int = 16000
    voice_chunk_ms: int = 320

    face: ServiceCompute = Field(
        default_factory=lambda: ServiceCompute(port=8000)
    )
    voice: ServiceCompute = Field(
        default_factory=lambda: ServiceCompute(port=8000)
    )
    stt: ServiceCompute = Field(
        default_factory=lambda: ServiceCompute(port=8000)
    )
    assistant: ServiceCompute = Field(
        default_factory=lambda: ServiceCompute(port=8000)
    )

    consent_granted: bool = False
    consent_at: str | None = None

    def lm_studio_base(self, host: str | None = None) -> str:
        h = host or self.assistant.host
        return f"http://{h}:{self.lm_studio_port}/v1"

    def set_compute(
        self,
        service: str,
        location: str,
        host: str | None = None,
    ) -> ServiceCompute:
        if service not in ("face", "voice", "stt", "assistant"):
            raise ValueError(f"Unknown service: {service}")
        sc: ServiceCompute = getattr(self, service)
        sc.location = ComputeLocation(location)
        if host:
            sc.host = host
        return sc


config = AppConfig()

# Ensure model directories exist at import time
for _dir in (config.faces_dir, config.voice_dir, config.whisper_dir, MODELS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)
