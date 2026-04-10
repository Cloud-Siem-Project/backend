#!/usr/bin/env python3
"""
CloudSIEM Master Server
=======================
Orchestrates worker registration, monitors health via periodic checks,
and exposes an interactive CLI shell for cluster management.

Usage:
    python3 master.py [--host 0.0.0.0] [--port 9800]
"""

import argparse
import cmd
import copy
import json
import os
import pathlib
import secrets
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─── .env File Loader ─────────────────────────────────────────────────────────
# Reads key=value pairs from a .env file and loads them into os.environ.
# No external dependency required (no python-dotenv needed).

def load_dotenv(path: str = ".env") -> None:
    """Load environment variables from a .env file (if it exists)."""
    env_path = pathlib.Path(path)
    if not env_path.is_file():
        return
    with env_path.open() as f:
        for line in f:
            line = line.strip()
            # skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # remove surrounding quotes if present
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            os.environ.setdefault(key, value)

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

# ─── Cluster State ────────────────────────────────────────────────────────────

nodes: dict[str, dict] = {}        # node_id -> node record
nodes_lock = threading.Lock()

HEALTH_INTERVAL = 30               # seconds between health sweeps
HEARTBEAT_TIMEOUT = 45             # mark DOWN after this many seconds w/o heartbeat

# ─── Persistent Storage (SQLite) ──────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nodes.db")


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the nodes table if it doesn't exist."""
    with _db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                node_id         TEXT PRIMARY KEY,
                hostname        TEXT,
                ip              TEXT,
                kernel          TEXT,
                ports           TEXT,
                status          TEXT,
                registered_at   TEXT,
                last_heartbeat  REAL,
                worker_version  TEXT
            )
        """)


def load_nodes_from_db() -> None:
    """Load persisted nodes into the in-memory dict on startup."""
    with _db_connect() as conn:
        rows = conn.execute("SELECT * FROM nodes").fetchall()
    with nodes_lock:
        for row in rows:
            node = dict(row)
            node["ports"] = json.loads(node["ports"] or "[]")
            nodes[node["node_id"]] = node
    if rows:
        print(f"  Loaded {len(rows)} node(s) from persistent storage.")


def persist_node(node_id: str) -> None:
    """Write the current in-memory state of a node to SQLite."""
    with nodes_lock:
        rec = copy.deepcopy(nodes.get(node_id))
    if rec is None:
        return
    rec["ports"] = json.dumps(rec["ports"])
    with _db_connect() as conn:
        conn.execute("""
            INSERT INTO nodes (node_id, hostname, ip, kernel, ports, status,
                               registered_at, last_heartbeat, worker_version)
            VALUES (:node_id, :hostname, :ip, :kernel, :ports, :status,
                    :registered_at, :last_heartbeat, :worker_version)
            ON CONFLICT(node_id) DO UPDATE SET
                hostname       = excluded.hostname,
                ip             = excluded.ip,
                kernel         = excluded.kernel,
                ports          = excluded.ports,
                status         = excluded.status,
                registered_at  = excluded.registered_at,
                last_heartbeat = excluded.last_heartbeat,
                worker_version = excluded.worker_version
        """, rec)


def remove_node_from_db(node_id: str) -> None:
    """Delete a node record from SQLite."""
    with _db_connect() as conn:
        conn.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))

# ─── Authentication ───────────────────────────────────────────────────────────
# Token is loaded from .env file, CLOUDSIEM_TOKEN env var, or --token flag.
# If none is set, a random token is generated and printed at startup.

AUTH_TOKEN: str = os.environ.get("CLOUDSIEM_TOKEN", "")

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
        POST /api/register      -- worker registers itself
        POST /api/heartbeat     -- worker heartbeat + system info update
        GET  /api/nodes         -- dump cluster state (JSON)
    """

    def log_message(self, format, *args):
        """Suppress default HTTP logs to keep the CLI clean."""
        pass

    # ── routing ───────────────────────────────────────────────────────────

    def _authenticate(self) -> bool:
        """
        Validate the Authorization header against the shared secret token.
        Expected format: 'Bearer <token>'
        Returns True if valid, False (and sends 401) if not.
        """
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._respond(401, {"error": "missing or malformed Authorization header"})
            _cli_event("[!] Rejected request -- no Authorization header")
            return False

        provided_token = auth_header[7:]  # strip 'Bearer ' prefix

        # constant-time comparison to prevent timing attacks
        if not secrets.compare_digest(provided_token, AUTH_TOKEN):
            self._respond(401, {"error": "invalid token"})
            _cli_event(f"[!] Rejected request -- invalid token from {self.client_address[0]}")
            return False

        return True

    def do_POST(self):
        if not self._authenticate():
            return
        if self.path == "/api/register":
            self._handle_register()
        elif self.path == "/api/heartbeat":
            self._handle_heartbeat()
        else:
            self._respond(404, {"error": "not found"})

    def do_GET(self):
        if not self._authenticate():
            return
        if self.path == "/api/nodes":
            self._handle_list_nodes()
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
            }

        action = "re-registered" if already else "registered"
        persist_node(node_id)
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
            })

        persist_node(node_id)
        if was_down:
            _cli_event(f"[^] Node '{node_id}' came back UP")

        self._respond(200, {"ok": True})

    def _handle_list_nodes(self):
        with nodes_lock:
            snapshot = copy.deepcopy(nodes)
        self._respond(200, snapshot)

    # ── plumbing ──────────────────────────────────────────────────────────

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 1_048_576:  # 1 MB limit
                # drain the socket so the client gets a clean response
                while length > 0:
                    chunk = min(length, 65536)
                    self.rfile.read(chunk)
                    length -= chunk
                self._respond(413, {"error": "payload too large"})
                return None
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
        self.end_headers()
        self.wfile.write(body)


# ─── Health Checker Thread ────────────────────────────────────────────────────

def health_checker():
    """Periodically sweep nodes and mark stale ones as DOWN."""
    while True:
        time.sleep(HEALTH_INTERVAL)
        cutoff = now_ts() - HEARTBEAT_TIMEOUT
        downed = []
        with nodes_lock:
            for nid, rec in nodes.items():
                if rec["status"] == "UP" and rec["last_heartbeat"] < cutoff:
                    rec["status"] = "DOWN"
                    downed.append(nid)
        for nid in downed:
            _cli_event(f"[X] Node '{nid}' marked DOWN (no heartbeat for >{HEARTBEAT_TIMEOUT}s)")
            persist_node(nid)


# ─── Interactive CLI Shell ────────────────────────────────────────────────────

_cli_ref = None          # will hold a ref to the Cmd instance for event printing
_cli_lock = threading.Lock()

def _cli_event(msg: str):
    """Print an async event into the CLI prompt cleanly."""
    ts = datetime.now().strftime("%H:%M:%S")
    with _cli_lock:
        # write above prompt so it doesn't mangle the line
        sys.stdout.write(f"\r\033[K{ts}  {msg}\n")
        if _cli_ref:
            sys.stdout.write(_cli_ref.prompt)
        sys.stdout.flush()


class MasterCLI(cmd.Cmd):
    intro = (
        "\n"
        "+==================================================+\n"
        "|          CloudSIEM Master - Interactive CLI       |\n"
        "+==================================================+\n"
        " Type 'help' for available commands.\n"
    )
    prompt = "\033[1;36mcloudsiem\033[0m> "

    # ── commands ──────────────────────────────────────────────────────────

    def do_nodes(self, _arg):
        """List all registered nodes and their status."""
        with nodes_lock:
            snapshot = [copy.deepcopy(n) for n in nodes.values()]

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
            rec = copy.deepcopy(nodes.get(node_id))

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
                remove_node_from_db(node_id)
                print(f"  Node '{node_id}' removed.")
            else:
                print(f"  Node '{node_id}' not found.")

    def do_clear(self, _arg):
        """Clear screen."""
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()

    def do_exit(self, _arg):
        """Exit the CLI (shuts down master)."""
        print("  Shutting down master ...")
        return True

    do_quit = do_exit
    do_EOF = do_exit        # Ctrl-D

    def emptyline(self):
        pass

    def default(self, line):
        print(f"  Unknown command: '{line}'. Type 'help' for available commands.")


# ─── Bootstrap ────────────────────────────────────────────────────────────────

def main():
    global AUTH_TOKEN

    parser = argparse.ArgumentParser(description="CloudSIEM Master Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=9800, help="API listen port (default: 9800)")
    parser.add_argument("--token", default=None,
                        help="Shared auth token (overrides CLOUDSIEM_TOKEN env var). "
                             "If neither is set, a random token is generated.")
    args = parser.parse_args()

    # resolve auth token: CLI flag > env var > auto-generate
    if args.token:
        AUTH_TOKEN = args.token
    elif not AUTH_TOKEN:
        AUTH_TOKEN = secrets.token_urlsafe(32)
        print(f"\n  [!] No token provided -- generated random token:")
        print(f"     {AUTH_TOKEN}")
        print(f"     Pass this to your workers via --token or CLOUDSIEM_TOKEN env var.\n")

    # initialize persistent storage and restore prior cluster state
    init_db()
    load_nodes_from_db()

    # start HTTP API in background
    server = HTTPServer((args.host, args.port), MasterAPIHandler)
    api_thread = threading.Thread(target=server.serve_forever, daemon=True)
    api_thread.start()
    print(f"  API listening on {args.host}:{args.port}")

    # start health checker in background
    hc_thread = threading.Thread(target=health_checker, daemon=True)
    hc_thread.start()

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
