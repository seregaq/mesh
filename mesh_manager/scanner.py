from __future__ import annotations

import subprocess
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from api import MeshApiError, get_status


def _ping_host(ip: str) -> bool:
    """Кросс-платформенный пинг: работает и на Windows, и на Linux"""
    import platform
    
    system = platform.system().lower()
    
    if system == "windows":
        # Windows версия
        cmd = ['ping', '-n', '1', '-w', '1000', ip]   # 1 пакет, таймаут 1000 мс
    else:
        # Linux / Raspberry Pi версия
        cmd = ['ping', '-c', '1', '-W', '1', ip]      # 1 пакет, таймаут 1 секунда

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3          # общий таймаут на всю команду
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


def _query_node(ip: str) -> dict[str, Any] | None:
    try:
        payload = get_status(ip)

        raw_ip = payload.get("ip", ip)

        # превращаем в список
        if isinstance(raw_ip, str):
            ip_list = raw_ip.split()
        else:
            ip_list = [raw_ip]

        payload["ip"] = ip_list[0]  # основной IP
        payload["all_ips"] = ip_list

        payload["configured"] = True
        return payload

    except MeshApiError:
        return None


def scan(subnet: str = "192.168.0", limit: int = 254) -> list[dict[str, Any]]:
    """Гибридное сканирование: пинг + проверка API"""
    print(f"🔍 Сканирую подсеть {subnet}.0/24 (до .{limit})...")

    # Генерируем все IP-адреса
    ips = [f"{subnet}.{i}" for i in range(1, limit + 1)]

    # 1. Пинг-сканирование
    alive_ips = []
    print("   Запускаю пинг всех устройств...")
    
    with ThreadPoolExecutor(max_workers=60) as executor:
        future_map = {executor.submit(_ping_host, ip): ip for ip in ips}
        
        for future in as_completed(future_map):
            ip = future_map[future]
            if future.result():
                alive_ips.append(ip)
                print(f"   ✓ Живой: {ip}")

    print(f"\nНайдено живых устройств: {len(alive_ips)}")

    # 2. Проверяем наличие API только у живых устройств
    nodes: list[dict[str, Any]] = []
    print("   Проверяю API на живых устройствах...")

    with ThreadPoolExecutor(max_workers=30) as executor:
        future_map = {executor.submit(_query_node, ip): ip for ip in alive_ips}
        
        for future in as_completed(future_map):
            ip = future_map[future]
            result = future.result()
            
            if result:
                # Полноценный mesh-узел
                nodes.append(result)
            else:
                # Новый узел без API
                nodes.append({
                    "ip": ip,
                    "configured": False,
                    "role": "new",
                    "hostname": "Новый узел (без API)",
                    "status": "запустите скрипт настройки"
                })

    # Сортируем по IP
    for n in nodes:
        ip = n.get("ip", "")
        print("DEBUG IP:", repr(ip))
    
    return sorted(nodes, key=lambda n: tuple(map(int, n.get("ip", "0.0.0.0").split('.'))))