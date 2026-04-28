from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_network_log_payload(
    *,
    nodes: dict[str, Any],           # полные данные узлов
    links: list[dict[str, Any]],
    paths: list[Any],
    topology_mode: str,
    subnet: str,
    limit: int,
    source_node: str | None = None,          # от какого IP брали топологию
    raw_batctl_n: str = "",
    raw_batctl_tr: str = "",
    scan_duration_ms: int = 0,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """Полный и удобный для отладки снимок состояния mesh-сети."""

    if errors is None:
        errors = []

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scan": {
            "subnet": subnet,
            "limit": limit,
            "duration_ms": scan_duration_ms,
        },
        "topology_mode": topology_mode,
        "source_node": source_node,          # ← очень важно!
        
        "nodes": nodes,                      # уже содержит role, hostname, uptime и т.д.
        
        "links": links,
        "paths": paths,
        
        "raw_topology": {
            "batctl_n": raw_batctl_n.strip(),
            "batctl_tr": raw_batctl_tr.strip(),
        },
        
        "errors": errors,
        
        # Дополнительно (можно расширять)
        "summary": {
            "total_nodes": len(nodes),
            "total_links": len(links),
            "total_paths": len(paths),
            "gateway_present": any(n.get("role") == "gateway" for n in nodes.values()),
        }
    }


def save_network_logs_json(path: str | Path, payload: dict[str, Any]) -> Path:
    """Сохраняет логи в красивый JSON."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return target