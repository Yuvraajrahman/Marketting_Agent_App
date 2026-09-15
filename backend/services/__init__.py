"""Shared status / broadcast helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


class StatusEmitter:
    def __init__(self) -> None:
        self._broadcast: BroadcastFn | None = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def attach(self, broadcast: BroadcastFn) -> None:
        self._broadcast = broadcast

    async def emit(self, message: dict[str, Any]) -> None:
        if self._broadcast:
            await self._broadcast(message)
        else:
            await self._queue.put(message)

    async def status(
        self,
        service: str,
        level: str,
        text: str,
        **extra: Any,
    ) -> None:
        await self.emit(
            {
                "channel": "system",
                "type": "status",
                "service": service,
                "level": level,
                "text": text,
                **extra,
            }
        )


emitter = StatusEmitter()


def dumps(obj: dict[str, Any]) -> str:
    return json.dumps(obj)
