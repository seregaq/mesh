from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import paramiko
import os
import matplotlib.pyplot as plt
from pathlib import Path
from PySide6.QtWidgets import QInputDialog
from datetime import datetime
import networkx as nx
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
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
    QFileDialog,
    QVBoxLayout,
    QWidget,
    QInputDialog, QDialog, QFormLayout, QDialogButtonBox)

from api import MeshApiError, get_topology, reboot_node, parse_batctl_o, parse_batctl_tr
from auth import AVAILABLE_PERMISSIONS, LoginDialog, create_account
from scanner import scan
from network_logs import build_network_log_payload, save_network_logs_json

ROLE_COLORS = {
    "gateway": "#8acc2e",
    "bridge": "#134bac",
    "client": "#81bbc9",
    "ap": "#f39c12",
    "unknown": "#ff0000",
}

@dataclass
class MeshNode:
    ip: str
    status: dict[str, Any]
    mac: str | None = None



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
        except Exception as exc:  
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

            self.progress.emit(100, "done")
            self.finished.emit(True, "Сеть успешно создана")

            

        except Exception as e:
            self.finished.emit(False, str(e))


class MeshManagerWindow(QMainWindow):
    def __init__(self, username: str, role: str, permissions: list[str] | None = None) -> None:
        super().__init__()
        self.current_username = username
        self.current_role = role
        self.is_admin = self.current_role == "admin"
        self.current_permissions = set(permissions or [])
        self.setWindowTitle(f"Mesh Manager — {self.current_username} ({self.current_role})")
        self.resize(1200, 720)

        self.nodes: dict[str, MeshNode] = {}
        self.links: list[dict[str, str]] = []
        self.paths: list[dict[str, str]] = []
        self.raw_topology_log = ""
        self._scan_thread: QThread | None = None
        self._scan_worker: ScanWorker | None = None
        self._scan_in_progress = False
        self.topology_mode = "all"

        root = QWidget(self)
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        controls = QHBoxLayout()

        button_style = """
QPushButton {
    background-color: #e0e0e0;
    color: #333333;
    border: 1px solid #b0b0b0;
    border-radius: 4px;
    padding: 6px;
    font-weight: normal;
}
QPushButton:hover {
    background-color: #d0d0d0;
    border-color: #909090;
}
QPushButton:pressed {
    background-color: #c0c0c0;
}
"""

        self.subnet_input = QLineEdit("192.168.199")
        self.limit_input = QLineEdit("50")
        self.scan_btn = QPushButton("Сканировать")
        self.save_logs_btn = QPushButton("💾 Сохранить логи (JSON)")
        self.auto_refresh = QCheckBox("Авто обновление (2с)")

        self.topology_mode_combo = QComboBox()
        self.topology_mode_combo.addItems(["Все связи", "Пути до Gateway"])
        self.topology_mode_combo.setCurrentIndex(0)  # по умолчанию "Все связи"
        self.topology_mode_combo.currentIndexChanged.connect(self._on_topology_mode_changed)
        controls.addWidget(QLabel("Топология:"))
        controls.addWidget(self.topology_mode_combo)

        controls.addWidget(QLabel("Подсеть:"))
        controls.addWidget(self.subnet_input)
        controls.addWidget(QLabel("Лимит:"))
        controls.addWidget(self.limit_input)
        controls.addWidget(self.scan_btn)
        controls.addWidget(self.save_logs_btn)
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
        self.node_list.setSelectionMode(QListWidget.ExtendedSelection)  
        self.reboot_btn = QPushButton("Перезагрузить выбранный узел")
        self.details = QLabel("Выберите узел, чтобы увидеть больше информации")
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
        self.save_logs_btn.clicked.connect(self._save_network_logs)
        self.auto_refresh.stateChanged.connect(self._toggle_auto_refresh)
        self.node_list.currentItemChanged.connect(self._show_selected_node)
        self.node_list.itemSelectionChanged.connect(self._sync_current_item)
        self.reboot_btn.clicked.connect(self._reboot_selected_node)
        self.ssh_add_btn = QPushButton("➕ Добавить в Mesh по SSH")
        self.ssh_add_btn.clicked.connect(self._add_node_via_ssh)
        self.new_network_btn = QPushButton("🌐 Создать новую сеть")
        self.new_network_btn.clicked.connect(self._create_new_network)
        self.orange_ap_btn = QPushButton("🍊 Добавить Orange в роли AP")
        self.orange_ap_btn.clicked.connect(self._add_orange_as_ap)
        self.create_user_btn = QPushButton("👤 Создать пользователя")
        self.create_user_btn.clicked.connect(self._create_user)

        self.orange_ap_btn.setStyleSheet(button_style)
        self.new_network_btn.setStyleSheet(button_style)
        self.ssh_add_btn.setStyleSheet(button_style)
        self.create_user_btn.setStyleSheet(button_style)
        self.reboot_btn.setStyleSheet(button_style)

        left_layout.addWidget(self.orange_ap_btn)
        left_layout.addWidget(self.new_network_btn)
        left_layout.addWidget(self.ssh_add_btn)
        left_layout.addWidget(self.create_user_btn)

        self._progress_reset_timer = QTimer(self)
        self._progress_reset_timer.setSingleShot(True)
        self._progress_reset_timer.timeout.connect(self._reset_progress_bar)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.start_scan)
        self._apply_permissions()

    def _apply_permissions(self) -> None:
        if self.is_admin:
            self.current_permissions = set(AVAILABLE_PERMISSIONS)

        self.reboot_btn.setEnabled("reboot_nodes" in self.current_permissions)
        self.ssh_add_btn.setEnabled("add_nodes_ssh" in self.current_permissions)
        self.new_network_btn.setEnabled("create_network" in self.current_permissions)
        self.orange_ap_btn.setEnabled("add_orange_ap" in self.current_permissions)
        self.create_user_btn.setEnabled("manage_users" in self.current_permissions)
        if not self.current_permissions:
            self.setStatusTip("Режим только чтение: изменение конфигурации ограничено.")

    def _ensure_permission(self, permission: str) -> bool:
        if permission in self.current_permissions:
            return True
        QMessageBox.warning(self, "Недостаточно прав", "Это действие доступно только для admin.")
        return False

    def _create_user(self) -> None:
        if not self._ensure_permission("manage_users"):
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Создание пользователя")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        username_edit = QLineEdit()
        password_edit = QLineEdit()
        password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        role_combo = QComboBox()
        role_combo.addItems(["viewer", "admin"])
        form.addRow("Логин:", username_edit)
        form.addRow("Пароль:", password_edit)
        form.addRow("Роль:", role_combo)
        layout.addLayout(form)

        permission_boxes: dict[str, QCheckBox] = {}
        labels = {
            "reboot_nodes": "Перезагрузка узлов",
            "add_nodes_ssh": "Добавление узлов по SSH",
            "create_network": "Создание новой сети",
            "add_orange_ap": "Добавление Orange в роли AP",
            "manage_users": "Управление пользователями",
        }
        layout.addWidget(QLabel("Права доступа:"))
        for perm in AVAILABLE_PERMISSIONS:
            box = QCheckBox(labels.get(perm, perm))
            permission_boxes[perm] = box
            layout.addWidget(box)

        def _sync_permissions_for_role() -> None:
            is_admin_role = role_combo.currentText() == "admin"
            for box in permission_boxes.values():
                box.setChecked(is_admin_role)
                box.setEnabled(not is_admin_role)

        role_combo.currentTextChanged.connect(_sync_permissions_for_role)
        _sync_permissions_for_role()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        username = username_edit.text().strip()
        password = password_edit.text()
        role = role_combo.currentText()
        if not username or not password:
            QMessageBox.warning(self, "Ошибка", "Логин и пароль обязательны.")
            return

        permissions = [perm for perm, box in permission_boxes.items() if box.isChecked()]
        try:
            create_account(username=username, password=password, role=role, permissions=permissions)
        except ValueError as exc:
            QMessageBox.warning(self, "Ошибка", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать пользователя: {exc}")
            return

        QMessageBox.information(self, "Готово", f"Пользователь {username} успешно создан.")

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



            # === DEBUG MODE ===
        # self.nodes = {
        #     "192.168.199.1": MeshNode("192.168.199.1", {"role": "gateway"}),
        #     "192.168.199.2": MeshNode("192.168.199.2", {"role": "client"}),
        #     "192.168.199.3": MeshNode("192.168.199.3", {"role": "client"}),
        #     "192.168.199.4": MeshNode("192.168.199.4", {"role": "bridge"}),
        #     "192.168.199.5": MeshNode("192.168.199.5", {"role": "ap"}),
        # }

        # self.links = [
        #     {"source": "192.168.199.1", "target": "192.168.199.2", "tq": 255},
        #     {"source": "192.168.199.1", "target": "192.168.199.3", "tq": 180},
        #     {"source": "192.168.199.2", "target": "192.168.199.4", "tq": 120},
        #     {"source": "192.168.199.3", "target": "192.168.199.4", "tq": 200},
        #     {"source": "192.168.199.4", "target": "192.168.199.5", "tq": 90},
        # ]

        # self._refresh_node_list()
        # self._draw_graph()
        # return
    
    
        if self._scan_in_progress:
            return

        subnet, limit = self._get_scan_params()
        self._scan_in_progress = True
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Сканирование...")

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
        self.scan_btn.setText("Сканировать")
        if error:
            QMessageBox.critical(self, "Ошибка сканирования", error)
            return

        self.nodes = {
            node["ip"]: MeshNode(
                ip=node["ip"],
                status=node,
                mac=node.get("mac")
            )
            for node in data if "ip" in node
        }
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
            role = str(node.status.get("role", "unknown"))

            if configured:
                text = f"{ip}  ({role})"
                item = QListWidgetItem(text)
                item.setBackground(QBrush(QColor(255, 255, 255)))  
            else:
                text = f"🔴 [NEW] {ip}  — запустите скрипт настройки"
                item = QListWidgetItem(text)
                item.setBackground(QBrush(QColor(255, 220, 220)))   

            item.setData(1, ip)        
            self.node_list.addItem(item)

        
        if current_ip:
            for i in range(self.node_list.count()):
                item = self.node_list.item(i)
                if item.data(1) == current_ip:
                    self.node_list.setCurrentItem(item)
                    break

    def _refresh_topology(self) -> None:
        self.links = []
        self.paths = []
        self.raw_topology_log = ""

        gateway_ip = None
        for ip, node in self.nodes.items():
            if node.status.get("role") == "gateway":
                gateway_ip = ip
                break

        if not gateway_ip:
            self._draw_graph()
            return

        try:
            topo = get_topology(gateway_ip)
            self.raw_topology_log = str(topo.get("raw", ""))

            raw = topo.get("raw", "")

            
            mac_to_ip = {
                node.mac: node.ip
                for node in self.nodes.values()
                if node.mac
            }

            if self.topology_mode == "all":
                self.links = parse_batctl_o(raw, mac_to_ip)
                self.paths = []

            elif self.topology_mode == "trace":
                self.paths = parse_batctl_tr(raw, mac_to_ip)
                self.links = []

        except Exception as e:
            print("Topology error:", e)

        self._draw_graph()

    def _on_topology_mode_changed(self) -> None:
        current_text = self.topology_mode_combo.currentText()
        self.topology_mode = "trace" if "Gateway" in current_text else "all"
        self._refresh_topology()   # сразу обновляем граф

    def _draw_graph(self) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)

        graph = nx.Graph()

        # Добавляем все узлы
        for ip in self.nodes:
            graph.add_node(ip)

        # Добавляем рёбра
        edges = []

# режим связей
        if self.links:
            for link in self.links:
                source = link.get("source")
                target = link.get("target")
                if source in self.nodes and target in self.nodes:
                    edges.append((source, target))

        # режим трассировки
        elif self.paths:
            for path in self.paths:
                for i in range(len(path) - 1):
                    a = path[i]
                    b = path[i + 1]
                    if a in self.nodes and b in self.nodes:
                        edges.append((a, b))

        # === Цвета узлов ===
        colors = []
        for node in graph.nodes:
            role = str(self.nodes.get(node, MeshNode(node, {})).status.get("role", "unknown"))
            colors.append(ROLE_COLORS.get(role, "#95a5a6"))

        n = graph.number_of_nodes()

        if n == 0:
            ax.text(0.5, 0.5, "No nodes found", ha='center', va='center', fontsize=16, color='gray')
            ax.axis("off")
            self.canvas.draw_idle()
            return

        # === Выбор layout ===
        if n <= 6:
            pos = nx.circular_layout(graph)
        elif n <= 15:
            pos = nx.kamada_kawai_layout(graph)
        else:
            pos = nx.spring_layout(graph, seed=42, k=1.8, iterations=120, scale=2.5)

        # === Рисуем узлы ===
        nx.draw_networkx_nodes(
            graph, pos,
            node_color=colors,
            node_size=7600,          
            edgecolors="black",
            linewidths=2.5,
            ax=ax,
        )

        # === Подписи узлов ===
        nx.draw_networkx_labels(
            graph, pos,
            font_size=9,
            font_weight="bold",
            ax=ax
        )

        # === Рисуем рёбра ===
        if edges:
            nx.draw_networkx_edges(
                graph, pos,
                edgelist=edges,
                edge_color="#2ecc71",      # ← ЗЕЛЁНЫЙ для всех существующих связей
                width=3.0,
                alpha=0.95,
                ax=ax
            )
        else:
            # Если связей нет — можно показать красным пунктиром (опционально)
            pass

        ax.set_title(f"Mesh topology — {n} узлов", fontsize=16, pad=20)
        ax.axis("off")
        ax.margins(0.3)


        legend_elements = []

        for role, color in ROLE_COLORS.items():
            legend_elements.append(
                Line2D(
                    [0], [0],
                    marker='o',
                    color='w',
                    label=role,
                    markerfacecolor=color,
                    markersize=10
                )
            )

        ax.legend(
            handles=legend_elements,
            loc='lower right',
            fontsize=9,
            frameon=True
        )

        self.figure.tight_layout(pad=2.0)
        self.canvas.draw_idle()

    def _save_network_logs(self) -> None:
        subnet, limit = self._get_scan_params()
        default_name = f"mesh-log-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить логи mesh-сети",
            default_name,
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_path:
            return

        # Собираем полные данные узлов
        nodes_dump = {ip: node.status for ip, node in self.nodes.items()}

        # Определяем source_node (откуда брали топологию)
        source_node = None
        for ip, node in self.nodes.items():
            if node.status.get("role") == "gateway":
                source_node = ip
                break

        payload = build_network_log_payload(
            nodes=nodes_dump,
            links=self.links,
            paths=self.paths,
            topology_mode=self.topology_mode,
            subnet=subnet,
            limit=limit,
            source_node=source_node,
            raw_batctl_n=getattr(self, 'raw_batctl_n', ''),
            raw_batctl_tr=getattr(self, 'raw_batctl_tr', ''),
            scan_duration_ms=getattr(self, 'last_scan_duration_ms', 0),
            errors=getattr(self, 'last_scan_errors', []),
        )

        try:
            saved_path = save_network_logs_json(file_path, payload)
            QMessageBox.information(self, "✅ Логи сохранены", 
                                  f"Файл сохранён:\n{saved_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка сохранения", str(exc))

    def _selected_ip(self) -> str | None:
        item = self.node_list.currentItem()
        return item.data(1) if item else None

    def _sync_current_item(self):
        items = self.node_list.selectedItems()
        if items:
            # всегда делаем текущим последний выбранный
            self.node_list.setCurrentItem(items[-1])

    def _show_selected_node(self) -> None:
        items = self.node_list.selectedItems()

        if not items:
            self.details.setText("Выберите узел")
            return

        # === один узел ===
        if len(items) == 1:
            item = items[0]
            ip = item.data(1)

            node = self.nodes.get(ip)
            if not node:
                return

            data = node.status

            lines = [f"IP: {ip}"]
            for key in ("role", "load", "uptime", "hostname"):
                if key in data:
                    lines.append(f"{key}: {data[key]}")

            self.details.setText("\n".join(lines))
            return

        # === несколько узлов ===
        ips = [item.data(1) for item in items if item.data(1) in self.nodes]
        roles = [self.nodes[ip].status.get("role", "unknown") for ip in ips]

        summary = (
            f"Выбрано узлов: {len(ips)}\n"
            f"Gateway: {roles.count('gateway')}\n"
            f"Client: {roles.count('client')}\n"
            f"Bridge: {roles.count('bridge')}\n"
            f"AP: {roles.count('ap')}"
        )

        self.details.setText(summary)

    def _reboot_selected_node(self) -> None:
        if not self._ensure_permission("reboot_nodes"):
            return
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
        if not self._ensure_permission("add_nodes_ssh"):
            return
        temp_ip = self._selected_ip()
        if not temp_ip:
            QMessageBox.warning(self, "Ошибка", "Сначала выбери [NEW] узел в списке")
            return

        node = self.nodes.get(temp_ip)
        if not node or node.status.get("configured", True):
            QMessageBox.warning(self, "Ошибка", "Выбранный узел уже настроен")
            return

        # Единое диалоговое окно для ввода всех данных
        dialog = QDialog(self)
        dialog.setWindowTitle("SSH подключение и настройка узла")
        dialog.resize(400, 300)

        layout = QVBoxLayout(dialog)

        # Поля ввода
        form_layout = QFormLayout()

        username_edit = QLineEdit("admin")
        password_edit = QLineEdit("admin")
        password_edit.setEchoMode(QLineEdit.Password)
        role_combo = QComboBox()
        role_combo.addItems(["client", "gateway", "bridge"])

        form_layout.addRow("Имя пользователя:", username_edit)
        form_layout.addRow("Пароль:", password_edit)
        form_layout.addRow("Роль узла:", role_combo)

        layout.addLayout(form_layout)

        # Кнопки
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() != QDialog.Accepted:
            return

        username = username_edit.text().strip()
        password = password_edit.text()
        role = role_combo.currentText()

        if not username or not password:
            QMessageBox.warning(self, "Ошибка", "Логин и пароль обязательны")
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
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(temp_ip, username=username, password=password, timeout=10)

                stdin, stdout, stderr = client.exec_command("iwconfig 2>/dev/null && ip -o link show")
                output = stdout.read().decode() + stderr.read().decode()
                client.close()

                interfaces = []
                for line in output.splitlines():
                    if 'wlan' in line or 'eth' in line or 'end' in line:
                        iface = line.split()[0].replace(':', '')
                        if iface != 'wlan0' and iface != 'bat0' and iface != 'lo':
                            interfaces.append(iface)

                interfaces = list(dict.fromkeys(interfaces))  # убираем дубли

                if not interfaces:
                    interfaces = ["wlan1", "eth0", "end0"]


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


        client_mesh_script = f"""#!/bin/bash

# IP: {mesh_ip}

sudo systemctl disable NetworkManager
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
sudo systemctl stop NetworkManager
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
sudo systemctl disable NetworkManager
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

            sftp.chmod(setup_path, 0o755)
            sftp.chmod(service_path, 0o755)

            # удаляем временные файлы
            os.unlink(tmp_setup_path)
            os.unlink(tmp_service_path)
            
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

    def _add_orange_as_ap(self) -> None:
        if not self._ensure_permission("add_orange_ap"):
            return

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

        dialog = QDialog(self)
        dialog.setWindowTitle("Настройка Access Point (hostapd)")
        dialog.resize(480, 360)

        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        interface_edit = QLineEdit("wlan0")
        ssid_edit = QLineEdit("Orange-Mesh")
        channel_edit = QLineEdit("6")
        passphrase_edit = QLineEdit("12345678")

        form.addRow("Wireless Interface:", interface_edit)
        form.addRow("SSID точки доступа:", ssid_edit)
        form.addRow("Channel:", channel_edit)
        form.addRow("Пароль Wi-Fi (WPA2):", passphrase_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        config = {
            "interface": interface_edit.text().strip() or "wlan0",
            "ssid": ssid_edit.text().strip() or "Orange-Mesh",
            "channel": channel_edit.text().strip() or "6",
            "wpa_passphrase": passphrase_edit.text().strip() or "12345678"
        }

        hostapd_path = "/etc/hostapd/hostapd.conf"

        hostapd_content = f"""interface={config['interface']}
    driver=nl80211
    ssid={config['ssid']}
    hw_mode=g
    channel={config['channel']}
    wpa=2
    wpa_passphrase={config['wpa_passphrase']}
    wpa_key_mgmt=WPA-PSK
    rsn_pairwise=CCMP"""
        
        dnsmasq_append = f"""
interface={config['interface']}
dhcp-range=10.0.0.10,10.0.0.100,255.255.255.0,24h
dhcp-option=6,8.8.8.8,1.1.1.1
"""

        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(temp_ip, username=username, password=password, timeout=15)

            cmd_write_config = f"sudo tee {hostapd_path}"
            stdin, stdout, stderr = client.exec_command(cmd_write_config)
            stdin.write(hostapd_content)
            stdin.close()

            cmd_set_daemon = """
    sudo sed -i 's|#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
    """
            stdin, stdout, stderr = client.exec_command(cmd_set_daemon)
            stdin.write(password + "\n")
            stdin.flush()

            cmd_kill_conflicts = """
    sudo systemctl stop wpa_supplicant
    sudo systemctl stop NetworkManager
    """
            stdin, stdout, stderr = client.exec_command(cmd_kill_conflicts)
            stdin.write(password + "\n")
            stdin.flush()

            cmd_restart = f"""
    sudo systemctl stop hostapd
    echo '{dnsmasq_append}' | sudo tee -a /etc/dnsmasq.conf
    sudo systemctl restart hostapd
    sudo systemctl unmask hostapd
    sudo systemctl enable hostapd
    sudo systemctl restart hostapd
    sudo mkdir -p /etc/mesh
    echo "ap" | sudo tee /etc/mesh/role
    """
            stdin, stdout, stderr = client.exec_command(cmd_restart)
            stdin.write(password + "\n")
            stdin.flush()

            client.close()

            QMessageBox.information(
                self,
                "✅ Успех!",
                f"Orange Pi настроен как Access Point!\n\n"
                f"IP: {temp_ip}\n"
                f"SSID: {config['ssid']}\n"
                f"Channel: {config['channel']}\n"
                f"Пароль: {config['wpa_passphrase']}\n\n"
                f"hostapd перезапущен"
            )

            self.start_scan()

        except Exception as e:
            QMessageBox.critical(self, "Ошибка SSH", str(e))

    def _create_new_network(self) -> None:
        if not self._ensure_permission("create_network"):
            return
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

        self._progress_reset_timer.start(6000)

def run_app() -> None:
    app = QApplication.instance() or QApplication([])
    auth_dialog = LoginDialog()
    if auth_dialog.exec() != QDialog.Accepted or not auth_dialog.account:
        return

    window = MeshManagerWindow(
        username=auth_dialog.account["username"],
        role=auth_dialog.account.get("role", "viewer"),
        permissions=auth_dialog.account.get("permissions", []),
    )
    window.show()
    window.start_scan()
    app.exec()
