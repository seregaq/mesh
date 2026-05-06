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
        cmd = ['ping', '-n', '1', '-w', '1200', ip]   # 1 пакет, таймаут 1000 мс
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
        payload["mac"] = payload.get("mac") or payload.get("mac_address")

        return payload

    except MeshApiError:
        return None


# def scan(subnet: str = "192.168.0", limit: int = 254) -> list[dict[str, Any]]:
#     """Гибридное сканирование: пинг + проверка API"""
#     print(f"🔍 Сканирую подсеть {subnet}.0/24 (до .{limit})...")

#     # Генерируем все IP-адреса
#     ips = [f"{subnet}.{i}" for i in range(1, limit + 1)]

#     # 1. Пинг-сканирование
#     alive_ips = []
#     print("   Запускаю пинг всех устройств...")
    
#     with ThreadPoolExecutor(max_workers=60) as executor:
#         future_map = {executor.submit(_ping_host, ip): ip for ip in ips}
        
#         for future in as_completed(future_map):
#             ip = future_map[future]
#             if future.result():
#                 alive_ips.append(ip)
#                 print(f"   ✓ Живой: {ip}")

#     print(f"\nНайдено живых устройств: {len(alive_ips)}")

#     # 2. Проверяем наличие API только у живых устройств
#     nodes: list[dict[str, Any]] = []
#     print("   Проверяю API на живых устройствах...")

#     with ThreadPoolExecutor(max_workers=30) as executor:
#         future_map = {executor.submit(_query_node, ip): ip for ip in alive_ips}
        
#         for future in as_completed(future_map):
#             ip = future_map[future]
#             result = future.result()
            
#             if result:
#                 # Полноценный mesh-узел
#                 nodes.append(result)
#             else:
#                 # Новый узел без API
#                 nodes.append({
#                     "ip": ip,
#                     "configured": False,
#                     "role": "new",
#                     "hostname": "Новый узел (без API)",
#                     "status": "запустите скрипт настройки"
#                 })

#     # Сортируем по IP
#     for n in nodes:
#         ip = n.get("ip", "")
#         print("DEBUG IP:", repr(ip))
    
#     return sorted(nodes, key=lambda n: tuple(map(int, n.get("ip", "0.0.0.0").split('.'))))


def scan(subnet: str = "192.168.0", limit: int = 254, exclude_self: bool = True, exclude_gateway: bool = True) -> list[dict[str, Any]]:
    """Гибридное сканирование: пинг + проверка API
    
    Args:
        subnet: подсеть (первые 3 октета)
        limit: максимальный номер хоста
        exclude_self: исключить свой IP
        exclude_gateway: исключить шлюз (обычно .1)
    """
    print(f"🔍 Сканирую подсеть {subnet}.0/24 (до .{limit})...")

    # Получаем свой IP-адрес для исключения
    my_ip = None
    if exclude_self:
        try:
            # Получаем IP через сокет (более надежный способ)
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            my_ip = s.getsockname()[0]
            s.close()
        except Exception:
            # Fallback: пытаемся получить через hostname
            try:
                my_ip = socket.gethostbyname(socket.gethostname())
            except Exception:
                my_ip = None
    
    # Получаем шлюз по умолчанию (для исключения)
    gateway_ip = None
    if exclude_gateway:
        gateway_ip = f"{subnet}.42"  # Обычно роутер на .1
    
    # Генерируем все IP-адреса, исключая ненужные
    ips = []
    for i in range(1, limit + 1):
        ip = f"{subnet}.{i}"
        
        # Исключаем свой IP
        if exclude_self and my_ip and ip == my_ip:
            print(f"   ⏭️ Исключаю свой IP: {ip}")
            continue
            
        # Исключаем шлюз
        if exclude_gateway and gateway_ip and ip == gateway_ip:
            print(f"   ⏭️ Исключаю шлюз: {ip}")
            continue
            
        ips.append(ip)

    # 1. Пинг-сканирование
    alive_ips = []
    print("   Запускаю пинг устройств...")
    
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
    return sorted(nodes, key=lambda n: tuple(map(int, n.get("ip", "0.0.0.0").split('.'))))