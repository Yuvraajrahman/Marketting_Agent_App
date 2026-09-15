"""Thin local/remote client wrappers for each compute service."""

from __future__ import annotations

from typing import Any

import httpx

from backend.config import AppConfig, ComputeLocation, ServiceCompute


class RemoteClient:
    """HTTP bridge used when a service is offloaded to another host."""

    def __init__(self, compute: ServiceCompute, path_prefix: str = "") -> None:
        self.compute = compute
        self.path_prefix = path_prefix.rstrip("/")

    @property
    def base_url(self) -> str:
        return f"http://{self.compute.host}:{self.compute.port}{self.path_prefix}"

    def is_remote(self) -> bool:
        return self.compute.location == ComputeLocation.REMOTE

    async def post_json(self, path: str, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            return r.json()

    async def get_json(self, path: str, timeout: float = 10.0) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.json()


def client_for(cfg: AppConfig, service: str) -> RemoteClient:
    sc: ServiceCompute = getattr(cfg, service)
    return RemoteClient(sc)
