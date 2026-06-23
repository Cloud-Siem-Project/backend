#!/usr/bin/env python3
"""
CloudSIEM Master Server
=======================
Orchestrates worker registration, monitors health via periodic checks,
and exposes an interactive CLI shell for cluster management.

Usage:
    python3 master.py [--host 0.0.0.0] [--port 9800]
"""

from __future__ import annotations  # lazy annotations — keeps PEP 604 syntax (`dict | None`) parseable on Python 3.9 (AL2023 default).

import argparse
import cmd
import json
import os
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote

# ─── Cluster State ────────────────────────────────────────────────────────────

nodes: dict[str, dict] = {}        # node_id -> node record
nodes_lock = threading.Lock()

HEALTH_INTERVAL = 30               # seconds between health sweeps
HEARTBEAT_TIMEOUT = 45             # mark DOWN after this many seconds w/o heartbeat

DB_PATH = os.environ.get("CLOUDSIEM_DB", "/opt/cloudsiem/master.db")

# ─── Database ────────────────────────────────────────────────────────────────

def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id         TEXT PRIMARY KEY,
            hostname        TEXT NOT NULL DEFAULT 'unknown',
            ip              TEXT NOT NULL DEFAULT 'unknown',
            kernel          TEXT NOT NULL DEFAULT 'unknown',
            ports           TEXT NOT NULL DEFAULT '[]',
            status          TEXT NOT NULL DEFAULT 'UP',
            registered_at   TEXT NOT NULL,
            last_heartbeat  REAL NOT NULL,
            worker_version  TEXT NOT NULL DEFAULT 'unknown'
        )
    """)
    conn.commit()
    return conn

def _db_load_nodes():
    conn = _db_connect()
    cursor = conn.execute("SELECT node_id, hostname, ip, kernel, ports, status, registered_at, last_heartbeat, worker_version FROM nodes")
    for row in cursor:
        nodes[row[0]] = {
            "node_id": row[0],
            "hostname": row[1],
            "ip": row[2],
            "kernel": row[3],
            "ports": json.loads(row[4]),
            "status": row[5],
            "registered_at": row[6],
            "last_heartbeat": row[7],
            "worker_version": row[8],
        }
    conn.close()

def _db_upsert_node(rec: dict):
    conn = _db_connect()
    conn.execute("""
        INSERT INTO nodes (node_id, hostname, ip, kernel, ports, status, registered_at, last_heartbeat, worker_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(node_id) DO UPDATE SET
            hostname=excluded.hostname,
            ip=excluded.ip,
            kernel=excluded.kernel,
            ports=excluded.ports,
            status=excluded.status,
            registered_at=excluded.registered_at,
            last_heartbeat=excluded.last_heartbeat,
            worker_version=excluded.worker_version
    """, (
        rec["node_id"], rec["hostname"], rec["ip"], rec["kernel"],
        json.dumps(rec["ports"]), rec["status"], rec["registered_at"],
        rec["last_heartbeat"], rec["worker_version"],
    ))
    conn.commit()
    conn.close()

def _db_update_status(node_id: str, status: str):
    conn = _db_connect()
    conn.execute("UPDATE nodes SET status = ? WHERE node_id = ?", (status, node_id))
    conn.commit()
    conn.close()

def _db_delete_node(node_id: str):
    conn = _db_connect()
    conn.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
    conn.commit()
    conn.close()

# ─── Helpers ──────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def now_ts() -> float:
    return time.time()

def pretty_ago(ts: float) -> str:
    """Human-readable 'X seconds ago' from a unix timestamp."""
    delta = int(time.time() - ts)
    if delta < 60:
        return f"{delta}s ago"
    elif delta < 3600:
        return f"{delta // 60}m {delta % 60}s ago"
    else:
        return f"{delta // 3600}h {(delta % 3600) // 60}m ago"

# ─── API Handler ──────────────────────────────────────────────────────────────

class MasterAPIHandler(BaseHTTPRequestHandler):
    """
    Endpoints:
        POST   /api/register        — worker registers itself
        POST   /api/heartbeat       — worker heartbeat + system info update
        GET    /api/nodes           — dump cluster state (JSON)
        DELETE /api/nodes/<node_id> — deregister (remove) a node
        GET    /api/worker.py       — serve the worker agent script (for registration)
    """

    def log_message(self, format, *args):
        """Suppress default HTTP logs to keep the CLI clean."""
        pass

    # ── CORS ──────────────────────────────────────────────────────────────

    def _add_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self._add_cors_headers()
        self.end_headers()

    # ── routing ───────────────────────────────────────────────────────────

    def do_POST(self):
        if self.path == "/api/register":
            self._handle_register()
        elif self.path == "/api/heartbeat":
            self._handle_heartbeat()
        else:
            self._respond(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/api/nodes":
            self._handle_list_nodes()
        elif self.path == "/api/worker.py" or self.path.startswith("/api/worker.py?"):
            self._handle_serve_worker()
        else:
            self._respond(404, {"error": "not found"})

    def do_DELETE(self):
        # DELETE /api/nodes/<node_id> — deregister a node
        if self.path.startswith("/api/nodes/"):
            node_id = unquote(self.path[len("/api/nodes/"):]).strip("/")
            self._handle_deregister(node_id)
        else:
            self._respond(404, {"error": "not found"})

    # ── handlers ──────────────────────────────────────────────────────────

    def _handle_register(self):
        body = self._read_json()
        if not body:
            return

        node_id = body.get("node_id")
        if not node_id:
            self._respond(400, {"error": "node_id required"})
            return

        with nodes_lock:
            already = node_id in nodes
            nodes[node_id] = {
                "node_id":          node_id,
                "hostname":         body.get("hostname", "unknown"),
                "ip":               body.get("ip", "unknown"),
                "kernel":           body.get("kernel", "unknown"),
                "ports":            body.get("ports", []),
                "status":           "UP",
                "registered_at":    now_iso(),
                "last_heartbeat":   now_ts(),
                "worker_version":   body.get("worker_version", "unknown"),
                "metrics":          body.get("metrics", {}),
            }
            _db_upsert_node(nodes[node_id])

        action = "re-registered" if already else "registered"
        _cli_event(f"[+] Node '{node_id}' ({body.get('ip','?')}) {action}")
        self._respond(200, {"ok": True, "action": action})

    def _handle_heartbeat(self):
        body = self._read_json()
        if not body:
            return

        node_id = body.get("node_id")
        if not node_id:
            self._respond(400, {"error": "node_id required"})
            return

        with nodes_lock:
            if node_id not in nodes:
                self._respond(404, {"error": "node not registered, send /api/register first"})
                return

            was_down = nodes[node_id]["status"] == "DOWN"
            nodes[node_id].update({
                "status":         "UP",
                "last_heartbeat": now_ts(),
                "hostname":       body.get("hostname", nodes[node_id]["hostname"]),
                "ip":             body.get("ip", nodes[node_id]["ip"]),
                "kernel":         body.get("kernel", nodes[node_id]["kernel"]),
                "ports":          body.get("ports", nodes[node_id]["ports"]),
                "metrics":        body.get("metrics", nodes[node_id].get("metrics", {})),
            })
            _db_upsert_node(nodes[node_id])

        if was_down:
            _cli_event(f"[↑] Node '{node_id}' came back UP")

        self._respond(200, {"ok": True})

    def _handle_list_nodes(self):
        with nodes_lock:
            snapshot = dict(nodes)
        self._respond(200, snapshot)

    def _handle_deregister(self, node_id: str):
        if not node_id:
            self._respond(400, {"error": "node_id required"})
            return
        with nodes_lock:
            existed = node_id in nodes
            if existed:
                del nodes[node_id]
        _db_delete_node(node_id)
        if existed:
            _cli_event(f"[-] Node '{node_id}' deregistered via API")
            self._respond(200, {"ok": True, "action": "deregistered", "node_id": node_id})
        else:
            self._respond(404, {"error": "node not found", "node_id": node_id})

    def _handle_serve_worker(self):
        """Serve worker.py so a fresh node can bootstrap itself (Wazuh-style)."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.py")
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self._respond(404, {"error": "worker.py not available on master"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/x-python; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    # ── plumbing ──────────────────────────────────────────────────────────

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            return json.loads(raw)
        except Exception:
            self._respond(400, {"error": "invalid JSON"})
            return None

    def _respond(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(body)


# ─── Health Checker Thread ────────────────────────────────────────────────────

def health_checker():
    """Periodically sweep nodes and mark stale ones as DOWN."""
    while True:
        time.sleep(HEALTH_INTERVAL)
        cutoff = now_ts() - HEARTBEAT_TIMEOUT
        with nodes_lock:
            for nid, rec in nodes.items():
                if rec["status"] == "UP" and rec["last_heartbeat"] < cutoff:
                    rec["status"] = "DOWN"
                    _db_update_status(nid, "DOWN")
                    _cli_event(f"[✗] Node '{nid}' marked DOWN (no heartbeat for >{HEARTBEAT_TIMEOUT}s)")


# ─── Interactive CLI Shell ────────────────────────────────────────────────────

_cli_ref = None          # will hold a ref to the Cmd instance for event printing

def _cli_event(msg: str):
    """Print an async event into the CLI prompt cleanly."""
    ts = datetime.now().strftime("%H:%M:%S")
    # write above prompt so it doesn't mangle the line
    sys.stdout.write(f"\r\033[K{ts}  {msg}\n")
    sys.stdout.flush()
    if _cli_ref:
        sys.stdout.write(_cli_ref.prompt)
        sys.stdout.flush()


class MasterCLI(cmd.Cmd):
    intro = (
        "\n"
        "╔══════════════════════════════════════════════════╗\n"
        "║          CloudSIEM Master · Interactive CLI      ║\n"
        "╚══════════════════════════════════════════════════╝\n"
        " Type 'help' for available commands.\n"
    )
    prompt = "\033[1;36mcloudsiem\033[0m> "

    # ── commands ──────────────────────────────────────────────────────────

    def do_nodes(self, _arg):
        """List all registered nodes and their status."""
        with nodes_lock:
            snapshot = list(nodes.values())

        if not snapshot:
            print("  (no nodes registered)")
            return

        hdr = f"  {'NODE ID':<20} {'HOSTNAME':<18} {'IP':<16} {'STATUS':<8} {'LAST SEEN'}"
        print(hdr)
        print("  " + "─" * (len(hdr) - 2))
        for n in sorted(snapshot, key=lambda x: x["node_id"]):
            status_color = "\033[1;32m" if n["status"] == "UP" else "\033[1;31m"
            print(
                f"  {n['node_id']:<20} {n['hostname']:<18} {n['ip']:<16} "
                f"{status_color}{n['status']:<8}\033[0m {pretty_ago(n['last_heartbeat'])}"
            )
        print()

    def do_status(self, _arg):
        """Show cluster overview: total, up, down counts + last sweep."""
        with nodes_lock:
            total = len(nodes)
            up = sum(1 for n in nodes.values() if n["status"] == "UP")
            down = total - up

        print(f"  Cluster nodes : {total}")
        print(f"  UP            : \033[1;32m{up}\033[0m")
        print(f"  DOWN          : \033[1;31m{down}\033[0m")
        print(f"  Health sweep  : every {HEALTH_INTERVAL}s  (timeout {HEARTBEAT_TIMEOUT}s)")
        print()

    def do_node(self, node_id: str):
        """Show detailed info for a specific node.  Usage: node <node_id>"""
        node_id = node_id.strip()
        if not node_id:
            print("  Usage: node <node_id>")
            return

        with nodes_lock:
            rec = nodes.get(node_id)

        if not rec:
            print(f"  Node '{node_id}' not found.")
            return

        status_color = "\033[1;32m" if rec["status"] == "UP" else "\033[1;31m"
        print(f"  Node ID       : {rec['node_id']}")
        print(f"  Hostname      : {rec['hostname']}")
        print(f"  IP            : {rec['ip']}")
        print(f"  Kernel        : {rec['kernel']}")
        print(f"  Status        : {status_color}{rec['status']}\033[0m")
        print(f"  Registered    : {rec['registered_at']}")
        print(f"  Last heartbeat: {pretty_ago(rec['last_heartbeat'])}")
        print(f"  Worker version: {rec['worker_version']}")
        if rec["ports"]:
            print(f"  Exposed ports :")
            for p in rec["ports"]:
                print(f"    :{p['port']:<6} {p['proto']:<5} {p['process']}")
        else:
            print(f"  Exposed ports : (none reported)")
        print()

    def do_drop(self, node_id: str):
        """Unregister a node from the cluster.  Usage: drop <node_id>"""
        node_id = node_id.strip()
        if not node_id:
            print("  Usage: drop <node_id>")
            return
        with nodes_lock:
            if node_id in nodes:
                del nodes[node_id]
                _db_delete_node(node_id)
                print(f"  Node '{node_id}' removed.")
            else:
                print(f"  Node '{node_id}' not found.")

    def do_clear(self, _arg):
        """Clear screen."""
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()

    def do_exit(self, _arg):
        """Exit the CLI (shuts down master)."""
        print("  Shutting down master …")
        return True

    do_quit = do_exit
    do_EOF = do_exit        # Ctrl-D

    def emptyline(self):
        pass

    def default(self, line):
        print(f"  Unknown command: '{line}'. Type 'help' for available commands.")


# ─── Bootstrap ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CloudSIEM Master Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=9800, help="API listen port (default: 9800)")
    parser.add_argument("--api-only", action="store_true",
                        help="Run HTTP API only without the interactive CLI "
                             "(use for containers, systemd services, EC2 user-data).")
    parser.add_argument("--db", default=None,
                        help="Path to SQLite database (default: /opt/cloudsiem/master.db)")
    args = parser.parse_args()

    if args.db:
        global DB_PATH
        DB_PATH = args.db

    # ensure db directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    # load persisted nodes
    _db_load_nodes()
    loaded = len(nodes)
    if loaded:
        print(f"  Loaded {loaded} node(s) from database", flush=True)

    # start HTTP API in background
    server = HTTPServer((args.host, args.port), MasterAPIHandler)
    api_thread = threading.Thread(target=server.serve_forever, daemon=True)
    api_thread.start()
    print(f"  API listening on {args.host}:{args.port}", flush=True)

    # start health checker in background
    hc_thread = threading.Thread(target=health_checker, daemon=True)
    hc_thread.start()

    # API-only mode: no interactive shell, just block until killed.
    # This is the path used by the EC2 systemd unit.
    if args.api_only:
        print("  Running in --api-only mode (no CLI). Send SIGTERM to stop.", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            print("  Master stopped.")
        return

    # run interactive CLI on main thread
    global _cli_ref
    cli = MasterCLI()
    _cli_ref = cli

    def _sigint(sig, frame):
        print("\n  Use 'exit' or Ctrl-D to quit.")

    signal.signal(signal.SIGINT, _sigint)

    try:
        cli.cmdloop()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        print("  Master stopped.")


if __name__ == "__main__":
    main()
