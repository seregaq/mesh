# Mesh Manager

Desktop app and console utility for managing mesh nodes through their REST API.

## Features

- Login at startup with local JSON account storage.
- Passwords are stored as PBKDF2-SHA256 hashes with per-user salt.
- Subnet scan of `/status` on all nodes.
- Node list with role labels (gateway/bridge/client).
- Topology graph from `/topology` with role-based colors.
- Node details view (IP/load/uptime/hostname when available).
- Reboot action via `/restart_point`.
- Auto-refresh every 2 seconds.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

GUI mode:

```bash
python -m mesh_manager.main --mode gui
```

### Default accounts

Accounts are loaded from `mesh_manager/accounts.json`.

- `admin` / `admin123` — role `admin` (full access)
- `operator` / `operator123` — role `viewer` (read-only mode)

Console scan mode:

```bash
python -m mesh_manager.main --mode console --subnet 192.168.199 --limit 50
```
