from flask import Flask, jsonify
import os
import socket
import subprocess

app = Flask(__name__)

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
