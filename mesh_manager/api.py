import requests
from typing import Any, Dict

class MeshApiError(Exception):
    pass

def parse_batctl_o(raw: str, mac_to_ip: dict[str, str]):
    links = []

    for line in raw.splitlines():
        line = line.strip()

        # пропускаем мусор
        if not line or "Originator" in line or "B.A.T.M.A.N" in line:
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        try:
            if parts[0] == "*":
                originator = parts[1]
                nexthop = parts[4]
            else:
                originator = parts[0]
                nexthop = parts[3]
        except IndexError:
            continue

        originator = originator.lower()
        nexthop = nexthop.lower()

        ip1 = mac_to_ip.get(originator)
        ip2 = mac_to_ip.get(nexthop)

        # не добавляем self-loop
        if ip1 and ip2 and ip1 != ip2:
            links.append({
                "source": ip1,
                "target": ip2
            })

    return links


def parse_batctl_tr(raw: str, mac_to_ip: dict[str, str]):
    paths = []

    for line in raw.splitlines():
        if "->" not in line:
            continue

        parts = line.split("->")

        path = []
        for part in parts:
            mac = part.strip().split()[0]
            ip = mac_to_ip.get(mac)
            if ip:
                path.append(ip)

        if len(path) >= 2:
            paths.append(path)

    return paths


def get_status(ip: str) -> Dict[str, Any]:
    """Получаем статус — используем твой текущий /status + /info"""
    try:
       
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


def get_topology(ip: str, timeout: float = 3.0) -> Dict[str, Any]:
    """Получаем реальные связи через batctl tr (с MAC → IP)"""
    try:
        # Запрашиваем у любого узла (лучше у gateway)
        r = requests.get(
            f"http://{ip}:5000/topology", 
            params={"gateway": "192.168.199.1"}, 
            timeout=timeout
        )
        if r.status_code == 200:
            data = r.json()
            return {
                "links": data.get("links", []),
                "paths": data.get("paths", []),
                "raw": data.get("raw", "")
            }
        return {"links": []}
    except:
        return {"links": []}

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