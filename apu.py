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

def parse_batctl_o(raw):
    entries = []

    for line in raw.splitlines():
        line = line.strip()

        if not line or line.startswith("Originator") or line.startswith("["):
            continue

        parts = re.split(r"\s+", line)

        if len(parts) < 5:
            continue

        originator = parts[0].lower()
        tq_part = parts[2]  # (255)
        nexthop = parts[3].lower()

        tq = int(re.findall(r"\d+", tq_part)[0]) if re.findall(r"\d+", tq_part) else 0

        entries.append({
            "originator": originator,
            "nexthop": nexthop,
            "tq": tq
        })

    return entries


@app.route("/topology")
def topology():
    try:
        raw = subprocess.getoutput("sudo batctl o")
        self_mac = subprocess.getoutput("cat /sys/class/net/wlan0/address").strip().lower()

        # MAC → IP
        arp_output = subprocess.getoutput("sudo ip neigh")
        mac_to_ip = {}

        for line in arp_output.splitlines():
            parts = line.split()
            if len(parts) >= 5:
                mac_to_ip[parts[4].lower()] = parts[0]

        entries = parse_batctl_o(raw)

        links = []

        for e in entries:
            nexthop = e["nexthop"]
            tq = e["tq"]

            if nexthop in mac_to_ip and self_mac in mac_to_ip:
                links.append({
                    "source": mac_to_ip[self_mac],
                    "target": mac_to_ip[nexthop],
                    "tq": tq
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
    return jsonify({
        "hostname": socket.gethostname(),
        "ip": get_ip(),
        "role": "unknown",
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
