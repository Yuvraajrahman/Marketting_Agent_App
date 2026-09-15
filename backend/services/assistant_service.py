"""LM Studio-backed conversation assistant with streaming suggestions."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

import httpx

from backend.clients import client_for
from backend.config import AppConfig, ComputeLocation
from backend.services import emitter

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]

STYLE_PROMPTS = {
    "closing": (
        "You are a sales coach. Suggest 1-2 short, natural closing phrases "
        "the salesperson could say next. Keep it brief and conversational."
    ),
    "objection-handling": (
        "You are a sales coach. Suggest 1-2 short phrases that empathetically "
        "handle the client's latest objection. Keep it brief."
    ),
    "rapport-building": (
        "You are a sales coach. Suggest 1-2 short, warm rapport-building phrases "
        "the salesperson could say next. Keep it brief."
    ),
}


class AssistantService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.enabled = False
        self.style = "rapport-building"
        self._broadcast: BroadcastFn | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._debounce_task: asyncio.Task | None = None
        self._pending_transcript = ""
        self._lock = asyncio.Lock()

    def attach(self, broadcast: BroadcastFn, loop: asyncio.AbstractEventLoop) -> None:
        self._broadcast = broadcast
        self._loop = loop

    def _base_url(self) -> str:
        host = self.cfg.assistant.host if self.cfg.assistant.location == ComputeLocation.REMOTE else "127.0.0.1"
        # When remote, LM Studio itself is on the remote host
        if self.cfg.assistant.location == ComputeLocation.REMOTE:
            return f"http://{self.cfg.assistant.host}:{self.cfg.lm_studio_port}/v1"
        return self.cfg.lm_studio_base("127.0.0.1")

    async def get_active_model(self) -> str:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{self._base_url()}/models")
            r.raise_for_status()
            models = r.json().get("data") or []
            if not models:
                raise RuntimeError("No model loaded in LM Studio")
            return models[0]["id"]

    async def stream_suggestion(self, transcript: str) -> str:
        model_id = await self.get_active_model()
        system = STYLE_PROMPTS.get(self.style, STYLE_PROMPTS["rapport-building"])
        full = ""
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{self._base_url()}/chat/completions",
                json={
                    "model": model_id,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": transcript or "(no transcript yet)"},
                    ],
                    "stream": True,
                    "temperature": 0.7,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = (
                        chunk.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content")
                    )
                    if not delta:
                        continue
                    full += delta
                    if self._broadcast:
                        await self._broadcast(
                            {
                                "channel": "assistant",
                                "type": "suggestion",
                                "text": full,
                                "delta": delta,
                                "done": False,
                            }
                        )
        if self._broadcast:
            await self._broadcast(
                {
                    "channel": "assistant",
                    "type": "suggestion",
                    "text": full,
                    "delta": "",
                    "done": True,
                }
            )
        return full

    async def on_transcript(self, transcript: str) -> None:
        if not self.enabled:
            return
        self._pending_transcript = transcript
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._debounced_run())

    async def _debounced_run(self) -> None:
        try:
            await asyncio.sleep(1.2)
            text = self._pending_transcript
            if not text.strip():
                return
            async with self._lock:
                await self.stream_suggestion(text)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            await emitter.status("assistant", "error", f"Assistant failed: {exc}")

    async def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        action = msg.get("action")

        # Remote compute for assistant means "talk to LM Studio on remote host"
        # which is already handled via _base_url — no separate microservice needed.
        # If a custom remote microservice is configured on assistant.port, proxy.
        if (
            self.cfg.assistant.location == ComputeLocation.REMOTE
            and self.cfg.assistant.port
            and action == "proxy"
        ):
            try:
                remote = client_for(self.cfg, "assistant")
                return await remote.post_json("/assistant", msg)
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        if action == "toggle":
            self.enabled = bool(msg.get("enabled", not self.enabled))
            return {"ok": True, "enabled": self.enabled}
        if action == "set_style":
            style = str(msg.get("style", "rapport-building"))
            if style not in STYLE_PROMPTS:
                return {"ok": False, "error": f"unknown style: {style}"}
            self.style = style
            return {"ok": True, "style": self.style}
        if action == "suggest":
            transcript = str(msg.get("transcript", ""))
            if not transcript:
                return {"ok": False, "error": "transcript required"}
            try:
                text = await self.stream_suggestion(transcript)
                return {"ok": True, "text": text}
            except Exception as exc:
                await emitter.status("assistant", "error", str(exc))
                return {"ok": False, "error": str(exc)}
        if action == "status":
            reachable = False
            model = None
            try:
                model = await self.get_active_model()
                reachable = True
            except Exception:
                pass
            return {
                "ok": True,
                "enabled": self.enabled,
                "style": self.style,
                "lm_studio": reachable,
                "model": model,
                "compute": self.cfg.assistant.model_dump(mode="json"),
                "base_url": self._base_url(),
            }
        return {"ok": False, "error": f"unknown assistant action: {action}"}
