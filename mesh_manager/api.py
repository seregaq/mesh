from __future__ import annotations

from typing import Any

import requests

DEFAULT_PORT = 5000
DEFAULT_TIMEOUT = 0.4


class MeshApiError(RuntimeError):
    """Raised when a mesh node API call fails."""



def _url(ip: str, endpoint: str, port: int = DEFAULT_PORT) -> str:
    endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    return f"http://{ip}:{port}{endpoint}"



def get_json(ip: str, endpoint: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Perform GET request and return JSON body."""
    try:
        response = requests.get(_url(ip, endpoint), timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001
        raise MeshApiError(f"GET {endpoint} for {ip} failed: {exc}") from exc



def post_json(
    ip: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Perform POST request and return JSON body if available."""
    try:
        response = requests.post(_url(ip, endpoint), json=payload, timeout=timeout)
        response.raise_for_status()
        if response.content:
            return response.json()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        raise MeshApiError(f"POST {endpoint} for {ip} failed: {exc}") from exc



def get_status(ip: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    return get_json(ip, "/status", timeout=timeout)



def get_topology(ip: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    return get_json(ip, "/topology", timeout=timeout)



def reboot_node(ip: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    return post_json(ip, "/restart_point", timeout=timeout)
