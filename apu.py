from flask import Flask, jsonify
import os
import re
import socket
import uuid
import subprocess

app = Flask(__name__)

def get_mac():
    mac = ':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff)
                   for ele in range(0, 8*6, 8)][::-1])
    return mac

def get_ip():
    return subprocess.getoutput("hostname -I").strip()

def get_neighbors():
    out = subprocess.getoutput("batctl n")
    return out

def get(name):
    try:
        return open(f"/etc/mesh/{name}").read().strip()
    except:
        return "unknown"

def parse_batctl_n(raw):
    entries = []

    for line in raw.splitlines():
        line = line.strip()

        if (
            not line
            or line.startswith("[")
            or line.startswith("IF")
        ):
            continue

        parts = re.split(r"\s+", line)

        if len(parts) < 3:
            continue

        iface = parts[0]
        neighbor = parts[1].lower()

        entries.append({
            "iface": iface,
            "neighbor": neighbor
        })

    return entries


@app.route("/topology")
def topology():
    try:
        raw = subprocess.getoutput("sudo batctl n")
        self_mac = subprocess.getoutput("cat /sys/class/net/wlan0/address").strip().lower()

        # MAC → IP
        arp_output = subprocess.getoutput("sudo ip neigh")
        mac_to_ip = {}

        for line in arp_output.splitlines():
            parts = line.split()
            if len(parts) >= 5:
                mac_to_ip[parts[4].lower()] = parts[0]

        entries = parse_batctl_n(raw)

        links = []

        for e in entries:
            neighbor_mac = e["neighbor"]

            if neighbor_mac in mac_to_ip and self_mac in mac_to_ip:
                links.append({
                    "source": mac_to_ip[self_mac],
                    "target": mac_to_ip[neighbor_mac]
                })

            return jsonify({
                "links": links,
                "raw": raw
            })

    except Exception as e:
        return jsonify({
            "links": [],
            "error": str(e)
        })


@app.route("/status")
def status():
    mac = subprocess.check_output(
    ["cat", "/sys/class/net/wlan0/address"]
).decode().strip()
    return jsonify({
        "hostname": socket.gethostname(),
        "ip": get_ip(),
        "role": get("role"),
        "mac" : mac
    })

@app.route("/neighbors")
def neighbors():
    return jsonify({
        "neighbors_raw": get_neighbors()
    })

@app.route("/info")
def info():
    return {
        "hostname": socket.gethostname(),
        "ip": get_ip(),
        "mac": get_mac(),
        "role": get("role"),
        "uptime": subprocess.getoutput("uptime -p"),
        "load": subprocess.getoutput("cat /proc/loadavg").split()[0],
        "cpu_temp": subprocess.getoutput("vcgencmd measure_temp"),
    }

@app.route("/restart_point", methods=["GET","POST"])
def restart_point():
    os.system("sudo /sbin/reboot")
    return jsonify({"status": "ok"})

@app.route("/role")
def role():
    return jsonify({
        "Role": get("role"),
    })

@app.route("/channel")
def channel():
    return jsonify({
        "Number of channel": get("channel")
    })

@app.route("/essid")
def essid():
    return jsonify({
        "ESSID": get("essid")
    })

app.run(host="0.0.0.0", port=5000)
