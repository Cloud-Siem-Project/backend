#!/usr/bin/env python3
"""
CloudSIEM Worker Agent
======================
Runs on each monitored Linux node. Collects system info, registers with
the master, and sends periodic heartbeats with updated telemetry.

Usage:
    python3 worker.py --master http://10.0.0.1:9800 [--node-id mynode01] [--interval 20]
"""

import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

WORKER_VERSION = "0.1.0"

# ─── System Info Collection ───────────────────────────────────────────────────

def get_hostname() -> str:
    return socket.gethostname()


def get_main_ip() -> str:
    """
    Get the IP of the default-route interface.
    Falls back to hostname resolution if /proc/net isn't available.
    """
    try:
        # create a UDP socket and "connect" to an external address
        # this reveals which interface the kernel would route through
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
    except Exception:
        pass

    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def get_kernel_version() -> str:
    return platform.release()


def get_exposed_ports() -> list[dict]:
    """
    Parse `ss -tlnp` to find TCP LISTEN ports and their owning processes.
    Returns list of {port, proto, process, pid, bind}.
    """
    ports = []
    try:
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True, text=True, timeout=10
        )
        # example line:
        # LISTEN  0  4096  0.0.0.0:22  0.0.0.0:*  users:(("sshd",pid=812,fd=3))
        for line in result.stdout.splitlines()[1:]:   # skip header
            parts = line.split()
            if len(parts) < 5:
                continue

            local = parts[3]                          # e.g. 0.0.0.0:22 or [::]:443
            # extract port — last colon-separated segment
            port_str = local.rsplit(":", 1)[-1]
            if not port_str.isdigit():
                continue

            bind_addr = local.rsplit(":", 1)[0]

            # extract process name from users:(...) if present
            proc = "unknown"
            pid = ""
            user_field = parts[-1] if "users:" in parts[-1] else ""
            m = re.search(r'"([^"]+)",pid=(\d+)', user_field)
            if m:
                proc = m.group(1)
                pid = m.group(2)

            ports.append({
                "port":    int(port_str),
                "proto":   "tcp",
                "process": proc,
                "pid":     pid,
                "bind":    bind_addr,
            })
    except FileNotFoundError:
        # ss not available — try netstat as fallback
        try:
            result = subprocess.run(
                ["netstat", "-tlnp"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if "LISTEN" not in line:
                    continue
                parts = line.split()
                local = parts[3]
                port_str = local.rsplit(":", 1)[-1]
                if not port_str.isdigit():
                    continue
                proc = parts[-1] if len(parts) > 6 else "unknown"
                ports.append({
                    "port":    int(port_str),
                    "proto":   "tcp",
                    "process": proc,
                    "pid":     "",
                    "bind":    local.rsplit(":", 1)[0],
                })
        except Exception:
            pass
    except Exception:
        pass

    # deduplicate by port number (IPv4 and IPv6 often both show)
    seen = set()
    unique = []
    for p in ports:
        if p["port"] not in seen:
            seen.add(p["port"])
            unique.append(p)
    return sorted(unique, key=lambda x: x["port"])


def collect_system_info() -> dict:
    """Gather all telemetry into a single dict."""
    return {
        "hostname": get_hostname(),
        "ip":       get_main_ip(),
        "kernel":   get_kernel_version(),
        "ports":    get_exposed_ports(),
    }


# ─── HTTP Helpers (stdlib only, no requests dependency) ───────────────────────

def post_json(url: str, payload: dict, timeout: int = 10) -> dict | None:
    """POST JSON to a URL and return the parsed response, or None on error."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"  [!] HTTP {e.code}: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [!] Request failed: {e}", file=sys.stderr)
        return None


# ─── Main Loop ────────────────────────────────────────────────────────────────

def generate_node_id() -> str:
    """Default node id: hostname-short."""
    return socket.gethostname().split(".")[0]


def register(master_url: str, node_id: str, info: dict) -> bool:
    payload = {"node_id": node_id, "worker_version": WORKER_VERSION, **info}
    resp = post_json(f"{master_url}/api/register", payload)
    if resp and resp.get("ok"):
        print(f"  [✓] Registered with master as '{node_id}' ({resp.get('action','')})")
        return True
    print(f"  [✗] Registration failed")
    return False


def heartbeat(master_url: str, node_id: str, info: dict) -> bool:
    payload = {"node_id": node_id, **info}
    resp = post_json(f"{master_url}/api/heartbeat", payload)
    if resp and resp.get("ok"):
        return True
    # if master says we're not registered, re-register
    return False


def main():
    parser = argparse.ArgumentParser(description="CloudSIEM Worker Agent")
    parser.add_argument("--master", required=True,
                        help="Master URL, e.g. http://10.0.0.1:9800")
    parser.add_argument("--node-id", default=None,
                        help="Node identifier (default: hostname)")
    parser.add_argument("--interval", type=int, default=20,
                        help="Heartbeat interval in seconds (default: 20)")
    args = parser.parse_args()

    node_id = args.node_id or generate_node_id()
    master_url = args.master.rstrip("/")

    print(f"  CloudSIEM Worker v{WORKER_VERSION}")
    print(f"  Node ID  : {node_id}")
    print(f"  Master   : {master_url}")
    print(f"  Interval : {args.interval}s")
    print()

    # initial system info + register
    info = collect_system_info()
    print(f"  Hostname : {info['hostname']}")
    print(f"  IP       : {info['ip']}")
    print(f"  Kernel   : {info['kernel']}")
    print(f"  Ports    : {len(info['ports'])} listening")
    print()

    # retry registration until success
    while True:
        if register(master_url, node_id, info):
            break
        print(f"  Retrying in 5s …")
        time.sleep(5)

    # heartbeat loop
    print(f"  Entering heartbeat loop (every {args.interval}s) — Ctrl-C to stop\n")
    try:
        while True:
            time.sleep(args.interval)
            info = collect_system_info()       # refresh telemetry each cycle
            ok = heartbeat(master_url, node_id, info)
            ts = time.strftime("%H:%M:%S")
            if ok:
                print(f"  {ts}  heartbeat OK  (ports={len(info['ports'])})")
            else:
                print(f"  {ts}  heartbeat FAILED — attempting re-register")
                register(master_url, node_id, info)
    except KeyboardInterrupt:
        print("\n  Worker stopped.")


if __name__ == "__main__":
    main()
