from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import networkx as nx
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
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
)

from .api import MeshApiError, get_topology, reboot_node
from .scanner import scan

ROLE_COLORS = {
    "gateway": "#2ecc71",
    "bridge": "#3498db",
    "client": "#ecf0f1",
}


@dataclass
class MeshNode:
    ip: str
    status: dict[str, Any]


class MeshManagerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Mesh Manager")
        self.resize(1200, 720)

        self.nodes: dict[str, MeshNode] = {}
        self.links: list[dict[str, str]] = []

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

        splitter = QSplitter()
        outer.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.node_list = QListWidget()
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

        self.scan_btn.clicked.connect(self.perform_scan)
        self.auto_refresh.stateChanged.connect(self._toggle_auto_refresh)
        self.node_list.currentItemChanged.connect(self._show_selected_node)
        self.reboot_btn.clicked.connect(self._reboot_selected_node)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.perform_scan)

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

    def perform_scan(self) -> None:
        subnet, limit = self._get_scan_params()
        data = scan(subnet=subnet, limit=limit)
        self.nodes = {node["ip"]: MeshNode(ip=node["ip"], status=node) for node in data if "ip" in node}
        self._refresh_node_list()
        self._refresh_topology()

    def _refresh_node_list(self) -> None:
        current_ip = self._selected_ip()
        self.node_list.clear()

        for ip, node in sorted(self.nodes.items()):
            role = str(node.status.get("role", "client"))
            item = QListWidgetItem(f"{ip} ({role})")
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
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        graph = nx.Graph()

        for ip in self.nodes:
            graph.add_node(ip)

        for link in self.links:
            source = link.get("source")
            target = link.get("target")
            if source and target:
                graph.add_edge(source, target)

        colors = []
        for node in graph.nodes:
            role = str(self.nodes.get(node, MeshNode(node, {})).status.get("role", "client"))
            colors.append(ROLE_COLORS.get(role, "#95a5a6"))

        if graph.number_of_nodes() > 0:
            pos = nx.spring_layout(graph, seed=4)
            nx.draw_networkx(
                graph,
                pos=pos,
                node_color=colors,
                with_labels=True,
                edge_color="#7f8c8d",
                ax=ax,
                font_size=8,
            )

        ax.set_title("Mesh topology")
        ax.axis("off")
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


def run_app() -> None:
    app = QApplication.instance() or QApplication([])
    window = MeshManagerWindow()
    window.show()
    window.perform_scan()
    app.exec()
