from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import paramiko
import os
from pathlib import Path
from PySide6.QtWidgets import QInputDialog
import networkx as nx
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QProgressBar,
    QComboBox,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QInputDialog, QDialog, QFormLayout, QDialogButtonBox)

from api import MeshApiError, get_topology, reboot_node
from scanner import scan

ROLE_COLORS = {
    "gateway": "#8acc2e",
    "bridge": "#134bac",
    "client": "#81bbc9",
    "unknown": "#ff0000",
}


@dataclass
class MeshNode:
    ip: str
    status: dict[str, Any]


class ScanWorker(QObject):
    finished = Signal(object, object)

    def __init__(self, subnet: str, limit: int) -> None:
        super().__init__()
        self.subnet = subnet
        self.limit = limit

    def run(self) -> None:
        try:
            data = scan(subnet=self.subnet, limit=self.limit)
            self.finished.emit(data, None)
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.finished.emit([], str(exc))

class NetworkCreateWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(bool, str)

    def __init__(self, ips, roles, essid, channel, subnet, username, password):
        super().__init__()
        self.ips = ips
        self.roles = roles
        self.essid = essid
        self.channel = channel
        self.subnet = subnet
        self.username = username
        self.password = password

    def run(self):
        total = len(self.ips)

        try:
            for i, ip in enumerate(self.ips):
                role = self.roles[ip]

                percent = int((i / total) * 100)
                self.progress.emit(percent, ip)

                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(ip, username=self.username, password=self.password, timeout=10)

                cmd = f"""
            echo "{role}" | sudo tee /etc/mesh/role
            echo "{self.essid}" | sudo tee /etc/mesh/essid
            echo "{self.channel}" | sudo tee /etc/mesh/channel
            echo "{self.subnet}" | sudo tee /etc/mesh/subnet
            sudo systemctl restart mesh-setup.service
            """

                stdin, stdout, stderr = client.exec_command(cmd)
                stdin.write(self.password + "\n")
                stdin.flush()

                if stdout.channel.recv_exit_status() != 0:
                    raise Exception(f"{ip}: " + stderr.read().decode())

                client.close()

            # ✅ только если ВСЁ прошло успешно
            self.progress.emit(100, "done")
            self.finished.emit(True, "Сеть успешно создана")

            

        except Exception as e:
            self.finished.emit(False, str(e))


class MeshManagerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Mesh Manager")
        self.resize(1200, 720)

        self.nodes: dict[str, MeshNode] = {}
        self.links: list[dict[str, str]] = []
        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None
        self._scan_in_progress = False

        root = QWidget(self)
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        controls = QHBoxLayout()

        self.subnet_input = QLineEdit("192.168.199")
        self.limit_input = QLineEdit("50")
        self.scan_btn = QPushButton("Scan")
        self.auto_refresh = QCheckBox("Auto refresh (2s)")

        controls.addWidget(QLabel("Subnet:"))
        controls.addWidget(self.subnet_input)
        controls.addWidget(QLabel("Limit:"))
        controls.addWidget(self.limit_input)
        controls.addWidget(self.scan_btn)
        controls.addWidget(self.auto_refresh)
        outer.addLayout(controls)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        outer.addWidget(self.progress_bar)

        splitter = QSplitter()
        outer.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.node_list = QListWidget()
        self.node_list.setSelectionMode(QListWidget.ExtendedSelection)  # разрешает выбирать несколько
        self.reboot_btn = QPushButton("Reboot selected node")
        self.details = QLabel("Select a node to see details")
        self.details.setWordWrap(True)

        left_layout.addWidget(self.node_list)
        left_layout.addWidget(self.reboot_btn)
        left_layout.addWidget(self.details)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.figure = Figure(figsize=(7, 6))
        self.canvas = FigureCanvasQTAgg(self.figure)
        right_layout.addWidget(self.canvas)
        splitter.addWidget(right)
        splitter.setSizes([380, 820])

        self.scan_btn.clicked.connect(self.start_scan)
        self.auto_refresh.stateChanged.connect(self._toggle_auto_refresh)
        self.node_list.currentItemChanged.connect(self._show_selected_node)
        self.reboot_btn.clicked.connect(self._reboot_selected_node)
        self.ssh_add_btn = QPushButton("➕ Добавить в Mesh по SSH")
        self.ssh_add_btn.clicked.connect(self._add_node_via_ssh)
        self.new_network_btn = QPushButton("🌐 Создать новую сеть")
        self.new_network_btn.clicked.connect(self._create_new_network)
        left_layout.addWidget(self.new_network_btn)
        left_layout.addWidget(self.ssh_add_btn)

        self._progress_reset_timer = QTimer(self)
        self._progress_reset_timer.setSingleShot(True)
        self._progress_reset_timer.timeout.connect(self._reset_progress_bar)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.start_scan)

    def _toggle_auto_refresh(self) -> None:
        if self.auto_refresh.isChecked():
            self.timer.start(2000)
        else:
            self.timer.stop()

    def _get_scan_params(self) -> tuple[str, int]:
        subnet = self.subnet_input.text().strip() or "192.168.199"
        try:
            limit = max(2, int(self.limit_input.text()))
        except ValueError:
            limit = 50
            self.limit_input.setText(str(limit))
        return subnet, limit

    def start_scan(self) -> None:
        if self._scan_in_progress:
            return

        subnet, limit = self._get_scan_params()
        self._scan_in_progress = True
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Scanning...")

        self._scan_thread = QThread(self)
        self._scan_worker = ScanWorker(subnet=subnet, limit=limit)
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_thread.finished.connect(self._cleanup_scan_worker)
        self._scan_thread.start()

    def _on_scan_finished(self, data: list[dict[str, Any]], error: str | None) -> None:
        self._scan_in_progress = False
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Scan")
        if error:
            QMessageBox.critical(self, "Scan failed", error)
            return

        self.nodes = {node["ip"]: MeshNode(ip=node["ip"], status=node) for node in data if "ip" in node}
        self._refresh_node_list()
        self._refresh_topology()

    def _cleanup_scan_worker(self) -> None:
        if self._scan_worker:
            self._scan_worker.deleteLater()
            self._scan_worker = None
        if self._scan_thread:
            self._scan_thread.deleteLater()
            self._scan_thread = None

    def _refresh_node_list(self) -> None:
        """Обновляет список узлов в GUI"""
        current_ip = self._selected_ip()
        self.node_list.clear()


        for ip, node in sorted(self.nodes.items()):
            configured = node.status.get("configured", True)
            role = str(node.status.get("role", "client"))

            if configured:
                text = f"{ip}  ({role})"
                item = QListWidgetItem(text)
                item.setBackground(QBrush(QColor(255, 255, 255)))  # белый фон
            else:
                text = f"🔴 [NEW] {ip}  — запустите скрипт настройки"
                item = QListWidgetItem(text)
                # Красный фон для новых узлов
                item.setBackground(QBrush(QColor(255, 220, 220)))   # светло-красный

            item.setData(1, ip)        # храним IP в данных элемента
            self.node_list.addItem(item)

        # Восстанавливаем предыдущий выбор
        if current_ip:
            for i in range(self.node_list.count()):
                item = self.node_list.item(i)
                if item.data(1) == current_ip:
                    self.node_list.setCurrentItem(item)
                    break

    def _refresh_topology(self) -> None:
        self.links = []
        for ip in self.nodes:
            try:
                topo = get_topology(ip, timeout=0.35)
                if isinstance(topo.get("links"), list):
                    self.links.extend(topo["links"])
                if self.links:
                    break
            except MeshApiError:
                continue

        self._draw_graph()

    def _draw_graph(self) -> None:
        """Улучшенная отрисовка графа — красиво даже при 30–50 узлах"""
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        graph = nx.Graph()

        for ip in self.nodes:
            graph.add_node(ip)

        # Добавляем реальные связи
        for link in self.links:
            source = link.get("source")
            target = link.get("target")
            if source and target:
                graph.add_edge(source, target)

        # Цвета по роли
        colors = []
        for node in graph.nodes:
            role = str(self.nodes.get(node, MeshNode(node, {})).status.get("role", "client"))
            colors.append(ROLE_COLORS.get(role, "#95a5a6"))

        n = graph.number_of_nodes()

        if n == 0:
            ax.text(0.5, 0.5, "No nodes found", ha='center', va='center', fontsize=16, color='gray')
            ax.axis("off")
            self.canvas.draw_idle()
            return

        # === УМНЫЙ LAYOUT ===
        if n <= 6:
            pos = nx.circular_layout(graph)                    # идеальный круг
        elif n <= 15:
            pos = nx.kamada_kawai_layout(graph)                # красивые правильные фигуры
        else:
            # Для большого количества — сильно разнесённый spring
            pos = nx.spring_layout(graph, seed=42, k=1.8, iterations=120, scale=2.5)

        # Рисуем
        nx.draw_networkx(
            graph,
            pos=pos,
            node_color=colors,
            with_labels=True,
            edge_color="#2c3e50",
            node_size=2400,
            font_size=9,
            font_weight="bold",
            linewidths=3,
            edgecolors="black",
            ax=ax,
        )

        # Дополнительно подчёркиваем реальные связи (если они есть)
        if len(self.links) > 0:
            nx.draw_networkx_edges(
                graph, pos, 
                edgelist=[(link.get("source"), link.get("target")) for link in self.links if link.get("source") and link.get("target")],
                edge_color="#e74c3c",
                width=2.5,
                alpha=0.9
            )

        ax.set_title(f"Mesh topology — {n} узлов", fontsize=16, pad=20)
        ax.axis("off")

        # Динамические отступы
        margin = 0.25 if n <= 12 else 0.4
        ax.margins(margin)

        self.figure.tight_layout(pad=2.0)
        self.canvas.draw_idle()

    def _selected_ip(self) -> str | None:
        item = self.node_list.currentItem()
        return item.data(1) if item else None

    def _show_selected_node(self) -> None:
        ip = self._selected_ip()
        if not ip:
            self.details.setText("Select a node to see details")
            return

        node = self.nodes.get(ip)
        if not node:
            self.details.setText("Node no longer available")
            return

        data = node.status
        lines = [f"IP: {ip}"]
        for key in ("role", "load", "uptime", "hostname"):
            if key in data:
                lines.append(f"{key}: {data[key]}")
        self.details.setText("\n".join(lines))

    def _reboot_selected_node(self) -> None:
        ip = self._selected_ip()
        if not ip:
            QMessageBox.warning(self, "No node selected", "Choose a node first")
            return

        try:
            reboot_node(ip)
        except MeshApiError as exc:
            QMessageBox.critical(self, "Reboot failed", str(exc))
            return

        QMessageBox.information(self, "Done", f"Restart command sent to {ip}")

    def _add_node_via_ssh(self) -> None:
        """Отправляем скрипт + apu.py. Mesh-настройка запускается каждый раз, сервисы — один раз."""
        temp_ip = self._selected_ip()
        if not temp_ip:
            QMessageBox.warning(self, "Ошибка", "Сначала выбери [NEW] узел в списке")
            return

        node = self.nodes.get(temp_ip)
        if not node or node.status.get("configured", True):
            QMessageBox.warning(self, "Ошибка", "Выбранный узел уже настроен")
            return

        username, ok = QInputDialog.getText(self, "SSH", "Имя пользователя:", text="pi")
        if not ok or not username:
            return

        password, ok = QInputDialog.getText(
            self, "SSH", "Пароль:", text="admin", echo=QLineEdit.EchoMode.Password
        )
        if not ok or not password:
            return
        
        role, ok = QInputDialog.getItem(
            self,
            "Выбор роли",
            "Выберите роль узла:",
            ["client", "gateway", "bridge"],
            0,
            False
        )

        if not ok:
            return
    

        hostapd_config = {
            "interface": "wlan1",
            "bridge": "br0",
            "ssid": "PI-Mesh",
            "channel": "6",
            "wpa_passphrase": "12345678"
        }

        if role == "bridge":
            try:
                # Получаем список доступных интерфейсов
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(temp_ip, username=username, password=password, timeout=10)

                stdin, stdout, stderr = client.exec_command("iwconfig 2>/dev/null && ip -o link show")
                output = stdout.read().decode() + stderr.read().decode()
                client.close()

                # Парсим интерфейсы
                interfaces = []
                for line in output.splitlines():
                    if 'wlan' in line or 'eth' in line or 'end' in line:
                        iface = line.split()[0].replace(':', '')
                        if iface != 'wlan0' and iface != 'bat0' and iface != 'lo':
                            interfaces.append(iface)

                interfaces = list(dict.fromkeys(interfaces))  # убираем дубли

                if not interfaces:
                    interfaces = ["wlan1", "eth0", "end0"]

                # Окно выбора интерфейса
                iface, ok = QInputDialog.getItem(
                    self,
                    "Выбор интерфейса для Bridge",
                    "Какой интерфейс использовать для Access Point?\n(wlan0 использовать нельзя)",
                    interfaces,
                    0,
                    False
                )
                if not ok:
                    return

                hostapd_config["interface"] = iface

                dialog = QDialog(self)
                dialog.setWindowTitle("Настройка Bridge + Access Point")
                dialog.resize(460, 340)

                layout = QVBoxLayout(dialog)
                form = QFormLayout()

                interface_edit = QLineEdit(bridge_config["interface"])
                bridge_edit = QLineEdit(bridge_config["bridge"])
                ssid_edit = QLineEdit(bridge_config["ssid"])
                channel_edit = QLineEdit(bridge_config["channel"])
                passphrase_edit = QLineEdit(bridge_config["wpa_passphrase"])

                form.addRow("Wireless Interface:", interface_edit)
                form.addRow("Bridge name:", bridge_edit)
                form.addRow("SSID точки доступа:", ssid_edit)
                form.addRow("Channel:", channel_edit)
                form.addRow("Пароль Wi-Fi:", passphrase_edit)

                layout.addLayout(form)

                buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
                buttons.accepted.connect(dialog.accept)
                buttons.rejected.connect(dialog.reject)
                layout.addWidget(buttons)

                if dialog.exec() == QDialog.Accepted:
                    bridge_config = {
                        "interface": interface_edit.text().strip() or "wlan1",
                        "bridge": bridge_edit.text().strip() or "br0",
                        "ssid": ssid_edit.text().strip() or "PI-Mesh",
                        "channel": channel_edit.text().strip() or "6",
                        "wpa_passphrase": passphrase_edit.text().strip() or "12345678"
                    }


            except Exception as e:
                QMessageBox.warning(self, "Предупреждение", 
                                f"Не удалось получить список интерфейсов:\n{str(e)}\n\n"
                                "Используем wlan1 по умолчанию.")  

        if role == "gateway":
            mesh_ip = "192.168.199.1"
        else:
            mesh_subnet = "192.168.199"
            used = [int(n.split(".")[-1]) for n in self.nodes if n.startswith(mesh_subnet)]
            next_octet = max(used, default=4) + 1
            if next_octet > 254:
                QMessageBox.critical(self, "Ошибка", "Нет свободных IP в mesh-сети")
                return
            mesh_ip = f"{mesh_subnet}.{next_octet}"

        
        home = f"/home/{username}"
        setup_path = f"{home}/setup-mesh.sh"
        service_path = f"{home}/install-services.sh"
        apu_path = f"{home}/apu.py"
        hostapd_path = f"{home}/hostapd.conf"

        # ==================== 1. MESH SCRIPT (запускается каждый раз) ====================
        client_mesh_script = f"""#!/bin/bash

# IP: {mesh_ip}


sudo systemctl stop NetworkManager
sudo systemctl stop wpa_supplicant
sudo rfkill unblock wifi

sudo batctl if add wlan0
sudo ifconfig bat0 mtu 1468
sudo batctl gw_mode client

sudo ip link set wlan0 up

sudo iwconfig wlan0 mode ad-hoc
sudo iwconfig wlan0 channel 8
sudo iwconfig wlan0 essid call-code-mesh

sudo ip addr flush dev bat0
sudo ip link set bat0 up
sudo ip addr add {mesh_ip}/24 dev bat0
sudo ip route add default via 192.168.199.1 dev bat0

sudo mkdir -p /etc/mesh
sudo echo "client" > /etc/mesh/role
sudo echo "8" > /etc/mesh/channel
sudo echo "call-code-mesh" > /etc/mesh/essid


"""
        gateway_mesh_script = f"""

#!/bin/bash
sudo systemctl disable NetworkManager
sudo systemctl disable wpa_supplicant
sudo rfkill unblock wifi
sudo batctl if add wlan0
sudo ifconfig bat0 mtu 1468

sudo batctl gw_mode server

sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
sudo iptables -A FORWARD -i eth0 -o bat0 -m state --state RELATED,ESTABLISHED -j ACCEPT
sudo iptables -A FORWARD -i bat0 -o eth0 -j ACCEPT

sudo ip link set wlan0 up
sudo iwconfig wlan0 mode ad-hoc
sudo iwconfig wlan0 channel 8
sudo iwconfig wlan0 essid call-code-mesh

sudo ip link set bat0 up
sleep 5
sudo ip addr add 192.168.199.1/24 dev bat0
sudo ip route add default via 192.168.199.1/24 dev bat0

sudo mkdir -p /etc/mesh
sudo echo "gateway" > /etc/mesh/role
sudo echo "8" > /etc/mesh/channel
sudo echo "call-code-mesh" > /etc/mesh/essid
"""
        bridge_mesh_script = f"""

#!/bin/bash
sleep 15

sudo apt-get update -qq
sudo apt-get install -y hostapd bridge-utils

sudo rfkill unblock wifi
sudo systemctl stop hostapd
sudo systemctl stop NetworkManager
sudo systemctl stop wpa_supplicant

sudo batctl if add wlan0
sudo ip link set wlan0 up
sudo iwconfig wlan0 mode ad-hoc
sudo iwconfig wlan0 channel 8
sudo iwconfig wlan0 essid call-code-mesh

sudo ip link set bat0 up
sudo ifconfig bat0 mtu 1468

sudo brctl addbr br0
sudo ip link set eth0 up
sudo brctl addif br0 eth0 bat0


sudo ip link set br0 up

sudo ip addr add {mesh_ip}/24 dev br0
sudo ip route add default via 192.168.199.1 dev br0

sudo batctl gw_mode client

sleep 10

sudo systemctl disable hostapd
sudo systemctl restart dnsmasq
sudo hostapd /home/{username}/hostapd.conf

sudo mkdir -p /etc/mesh
sudo echo "bridge" > /etc/mesh/role
sudo echo "8" > /etc/mesh/channel
sudo echo "call-code-mesh" > /etc/mesh/essid

"""
        service_script = f"""

sudo tee /etc/systemd/system/mesh-setup.service > /dev/null <<'EOF'
[Unit]
Description=Call-Code Mesh Network Setup
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/bash {setup_path}
RemainAfterExit=yes
User=root

[Install]
WantedBy=multi-user.target
EOF


sudo tee /etc/systemd/system/mesh-api.service > /dev/null <<'EOF'
[Unit]
Description=Call-Code Mesh API Server
After=network.target mesh-setup.service

[Service]
Type=simple
User={username}
WorkingDirectory={home}
ExecStart=/usr/bin/python3 {apu_path}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable mesh-setup.service
sudo systemctl enable mesh-api.service
"""
        hostapd_content = f"""interface={hostapd_config['interface']}
bridge={hostapd_config['bridge']}
driver=nl80211
ssid={hostapd_config['ssid']}
channel={hostapd_config['channel']}
wpa=2
wpa_passphrase={hostapd_config['wpa_passphrase']}
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
"""


        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(temp_ip, username=username, password=password, timeout=15)
            
            if role=='client':
                mesh_script = client_mesh_script
            elif role=='gateway':
                mesh_script = gateway_mesh_script
            elif role== 'bridge':
                mesh_script = bridge_mesh_script
            sftp = client.open_sftp()

            
            import tempfile

            # === setup script ===
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.sh', delete=False, encoding='utf-8', newline='\n'
            ) as tmp_setup:
                tmp_setup.write(mesh_script)
                tmp_setup_path = tmp_setup.name

            # === service script ===
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.sh', delete=False, encoding='utf-8', newline='\n'
            ) as tmp_service:
                tmp_service.write(service_script)
                tmp_service_path = tmp_service.name

            # === upload ===
            sftp.put(tmp_setup_path, setup_path)
            sftp.put(tmp_service_path, service_path)
            if role == "bridge":
                sftp.put(tmp_service_path, service_path)


            # права
            sftp.chmod(setup_path, 0o755)
            sftp.chmod(service_path, 0o755)

            # удаляем временные файлы
            os.unlink(tmp_setup_path)
            os.unlink(tmp_service_path)
            
            

            # Загружаем apu.py
            project_root = Path(__file__).parent.parent
            local_apu_path = project_root / "apu.py"

            if local_apu_path.exists():
                sftp.put(str(local_apu_path), apu_path)
            else:
                QMessageBox.warning(self, "Предупреждение", "Файл apu.py не найден в корне проекта!")

            if role=='bridge':
                with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False, encoding='utf-8', newline='\n') as tmp:
                    tmp.write(hostapd_content)
                    tmp_path = tmp.name
                sftp.put(tmp_path, hostapd_path)
                os.unlink(tmp_path)

            sftp.close()

            # Запускаем скрипт (он настроит mesh + создаст сервисы)
            stdin, stdout, stderr = client.exec_command(f"sudo -S bash {service_path}")
            stdin.write(password + "\n")
            stdin.flush()

            output = stdout.read().decode()
            error = stderr.read().decode()

            exit_status = stdout.channel.recv_exit_status()  

            if exit_status != 0:
                raise Exception("Ошибка выполнения setup-mesh.sh")

            stdin, stdout, stderr = client.exec_command("sudo reboot")
            stdin.write(password + "\n")
            stdin.flush()

            exit_status = stdout.channel.recv_exit_status()
            client.close()

            if exit_status == 0:
                QMessageBox.information(
                    self,
                    "✅ Успех!",
                    f"Узел успешно настроен!\n\n"
                    f"Временный IP: {temp_ip}\n"
                    f"Mesh IP: {mesh_ip}\n\n"
                    f"✔ setup-mesh.sh создан и настроен на автозапуск\n"
                    f"✔ apu.py загружен\n"
                    f"✔ mesh-api.service запущен\n\n"
                    f"Pi сейчас перезагружается..."
                )
            else:
                QMessageBox.warning(self, "Предупреждение", f"Скрипт завершился с кодом {exit_status}")

            self.start_scan()

        except Exception as e:
            QMessageBox.critical(self, "Ошибка SSH", f"{str(e)}")


    def _create_new_network(self) -> None:
        selected_items = self.node_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Ошибка", "Выберите узлы")
            return

        selected_ips = [item.data(1) for item in selected_items]

        # === диалог ===
        dialog = QDialog(self)
        dialog.setWindowTitle("Новая mesh сеть")

        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        essid_edit = QLineEdit("mesh-new")
        channel_edit = QLineEdit("1")
        subnet_edit = QLineEdit("192.168.199")

        form.addRow("ESSID:", essid_edit)
        form.addRow("Channel:", channel_edit)
        form.addRow("Подсеть:", subnet_edit)

        layout.addLayout(form)

        role_widgets = {}

        for ip in selected_ips:
            combo = QComboBox()
            combo.addItems(["client", "gateway", "bridge"])
            role_widgets[ip] = combo

            layout.addWidget(QLabel(ip))
            layout.addWidget(combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        essid = essid_edit.text().strip()
        channel = channel_edit.text().strip()
        subnet = subnet_edit.text().strip()

        # === подтверждение ===
        msg = "\n".join(selected_ips)
        reply = QMessageBox.question(
            self,
            "Подтверждение",
            f"Подключение по SSH к:\n\n{msg}\n\nПродолжить?",
        )
        if reply != QMessageBox.Yes:
            return

        username, ok = QInputDialog.getText(self, "SSH", "User:", text="pi")
        if not ok:
            return

        password, ok = QInputDialog.getText(
            self, "SSH", "Password:", echo=QLineEdit.Password
        )
        if not ok:
            return

        roles = {ip: role_widgets[ip].currentText() for ip in selected_ips}

        # === поток ===
        self._net_thread = QThread()
        self._net_worker = NetworkCreateWorker(
            selected_ips, roles, essid, channel, subnet, username, password
        )

        self._net_worker.moveToThread(self._net_thread)

        self._net_thread.started.connect(self._net_worker.run)
        self._net_worker.progress.connect(self._on_net_progress)
        self._net_worker.finished.connect(self._on_net_finished)

        self._net_worker.finished.connect(self._net_thread.quit)
        self._net_thread.finished.connect(self._net_thread.deleteLater)

        self.progress_bar.setValue(0)
        self._net_thread.start()

    def _on_net_progress(self, percent, ip):
        self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(f"{ip} ({percent}%)")

    def _reset_progress_bar(self):
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")

    def _on_net_finished(self, success, message):
        if success:
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat("Готово (100%)")
            QMessageBox.information(self, "Готово", message)
            self.start_scan()
        else:
            self.progress_bar.setFormat("Ошибка")
            QMessageBox.critical(self, "Ошибка", message)

        # ✅ запускаем авто-сброс через 6 секунд
        self._progress_reset_timer.start(6000)

def run_app() -> None:
    app = QApplication.instance() or QApplication([])
    window = MeshManagerWindow()
    window.show()
    window.start_scan()
    app.exec()
