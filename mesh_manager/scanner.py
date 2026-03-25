from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .api import MeshApiError, get_status



def _query_node(ip: str, timeout: float) -> dict[str, Any] | None:
    try:
        payload = get_status(ip, timeout=timeout)
        payload.setdefault("ip", ip)
        return payload
    except MeshApiError:
        return None



def scan(subnet: str = "192.168.199", limit: int = 50, timeout: float = 0.25) -> list[dict[str, Any]]:
    """Scan mesh subnet and return status payloads for live nodes."""
    nodes: list[dict[str, Any]] = []
    ips = [f"{subnet}.{i}" for i in range(1, limit)]

    with ThreadPoolExecutor(max_workers=32) as executor:
        future_map = {executor.submit(_query_node, ip, timeout): ip for ip in ips}
        for future in as_completed(future_map):
            result = future.result()
            if result:
                nodes.append(result)

    return sorted(nodes, key=lambda n: n.get("ip", ""))
