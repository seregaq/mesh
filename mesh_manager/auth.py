from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

ACCOUNTS_FILE = Path(__file__).with_name("accounts.json")
PBKDF2_ITERATIONS = 200_000


def hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return base64.b64encode(digest).decode("utf-8")


def load_accounts() -> list[dict[str, str]]:
    with ACCOUNTS_FILE.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data.get("accounts", [])


def authenticate_user(username: str, password: str) -> dict[str, str] | None:
    for account in load_accounts():
        if account.get("username") != username:
            continue
        expected_hash = account.get("password_hash", "")
        salt = account.get("salt", "")
        calculated_hash = hash_password(password, salt)
        if hmac.compare_digest(calculated_hash, expected_hash):
            return account
    return None


class LoginDialog(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Авторизация")
        self.setModal(True)
        self.resize(320, 150)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.username_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("Логин:", self.username_input)
        form.addRow("Пароль:", self.password_input)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #b00020;")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._try_login)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.account: dict[str, str] | None = None

    def _try_login(self) -> None:
        username = self.username_input.text().strip()
        password = self.password_input.text()
        account = authenticate_user(username=username, password=password)
        if not account:
            self.error_label.setText("Неверный логин или пароль.")
            return
        self.account = account
        self.accept()
