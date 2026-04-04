import requests
from typing import Any, Dict

class MeshApiError(Exception):
    pass


def get_status(ip: str) -> Dict[str, Any]:
    """Получаем статус — используем твой текущий /status + /info"""
    try:
        # Сначала пробуем /info (у тебя он богаче)
        r = requests.get(f"http://{ip}:5000/info", timeout=3)
        if r.status_code == 200:
            data = r.json()
            data.setdefault("ip", ip)
            data.setdefault("role", "client")
            data.setdefault("configured", True)
            return data

        # Если /info не сработал — пробуем /status
        r = requests.get(f"http://{ip}:5000/status", timeout=2)
        r.raise_for_status()
        data = r.json()
        data.setdefault("ip", ip)
        data.setdefault("role", "client")
        data.setdefault("configured", True)
        return data

    except Exception as e:
        # Даже если API не отвечает — показываем как "Новый узел"
        raise MeshApiError(f"Нет ответа от {ip}: {e}")


def get_topology(ip: str, timeout: float = 1.0) -> Dict[str, Any]:
    """Твоего /topology пока нет — возвращаем пустую топологию"""
    return {
        "nodes": [ip],
        "links": []
    }


def reboot_node(ip: str) -> Dict[str, Any]:
    """Перезагрузка — используем твой /restart_point"""
    try:
        r = requests.get(f"http://{ip}:5000/restart_point", timeout=5)  # ты используешь GET
        # Если захочешь POST позже — поменяй на requests.post
        if r.status_code in (200, 204):
            return {"status": "ok"}
        else:
            raise MeshApiError(f"Reboot failed: HTTP {r.status_code}")
    except Exception as e:
        raise MeshApiError(f"Не удалось перезагрузить {ip}: {e}")


# Дополнительно (можно использовать в деталях)
def get_neighbors(ip: str):
    try:
        r = requests.get(f"http://{ip}:5000/neighbors", timeout=2)
        return r.json() if r.status_code == 200 else {}
    except:
        return {}