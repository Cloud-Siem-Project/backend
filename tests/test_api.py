#!/usr/bin/env python3
"""Functional tests for CloudSIEM master API."""

import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import HTTPServer

# Patch AUTH_TOKEN before importing handler
os.environ["CLOUDSIEM_TOKEN"] = "test-token"

# Add agents directory to path so we can import master
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))
import master

master.AUTH_TOKEN = "test-token"

# Use a temp DB for tests so we don't touch the real nodes.db
import tempfile
master.DB_PATH = os.path.join(tempfile.gettempdir(), "cloudsiem_test.db")
master.init_db()

PORT = 19800
BASE = f"http://127.0.0.1:{PORT}"

def req(method, path, body=None, token="test-token"):
    """Helper: send HTTP request, return (status_code, parsed_json)."""
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

passed = 0
failed = 0

def check(name, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")

# --- Start server ---
server = HTTPServer(("127.0.0.1", PORT), master.MasterAPIHandler)
t = threading.Thread(target=server.serve_forever, daemon=True)
t.start()
time.sleep(0.5)

print("\n=== CloudSIEM API Tests ===\n")

# Test 1: No auth header
try:
    r = urllib.request.Request(f"{BASE}/api/nodes", method="GET")
    urllib.request.urlopen(r, timeout=5)
    check("No auth -> 401", False)
except urllib.error.HTTPError as e:
    check("No auth -> 401", e.code == 401)

# Test 2: Wrong token
code, body = req("GET", "/api/nodes", token="wrong")
check("Wrong token -> 401", code == 401)

# Test 3: Register a node
code, body = req("POST", "/api/register", {
    "node_id": "node-01",
    "hostname": "server1",
    "ip": "10.0.0.1",
    "kernel": "5.15.0",
    "ports": [{"port": 22, "proto": "tcp", "process": "sshd", "pid": "100", "bind": "0.0.0.0"}],
})
check("Register node -> 200 ok", code == 200 and body.get("ok"))
check("Register action -> registered", body.get("action") == "registered")

# Test 4: Re-register same node
code, body = req("POST", "/api/register", {
    "node_id": "node-01",
    "hostname": "server1",
    "ip": "10.0.0.1",
    "kernel": "5.15.0",
    "ports": [],
})
check("Re-register -> re-registered", body.get("action") == "re-registered")

# Test 5: Register without node_id
code, body = req("POST", "/api/register", {"hostname": "bad"})
check("Register no node_id -> 400", code == 400)

# Test 6: Heartbeat
code, body = req("POST", "/api/heartbeat", {
    "node_id": "node-01",
    "hostname": "server1",
    "ip": "10.0.0.1",
    "kernel": "5.15.0",
    "ports": [],
})
check("Heartbeat -> 200 ok", code == 200 and body.get("ok"))

# Test 7: Heartbeat for unregistered node
code, body = req("POST", "/api/heartbeat", {"node_id": "ghost"})
check("Heartbeat unknown node -> 404", code == 404)

# Test 8: List nodes
code, body = req("GET", "/api/nodes")
check("List nodes -> 200", code == 200)
check("List nodes contains node-01", "node-01" in body)
check("Node status is UP", body["node-01"]["status"] == "UP")

# Test 9: Register second node
code, body = req("POST", "/api/register", {
    "node_id": "node-02",
    "hostname": "server2",
    "ip": "10.0.0.2",
    "kernel": "6.1.0",
    "ports": [],
})
check("Register second node -> 200", code == 200)

# Test 10: List shows both nodes
code, body = req("GET", "/api/nodes")
check("Both nodes listed", "node-01" in body and "node-02" in body)

# Test 11: 404 on unknown endpoint
code, body = req("GET", "/api/unknown")
check("Unknown endpoint -> 404", code == 404)

# Test 12: Invalid JSON
try:
    r = urllib.request.Request(
        f"{BASE}/api/register",
        data=b"not json",
        headers={"Content-Type": "application/json", "Authorization": "Bearer test-token"},
        method="POST",
    )
    urllib.request.urlopen(r, timeout=5)
    check("Invalid JSON -> 400", False)
except urllib.error.HTTPError as e:
    check("Invalid JSON -> 400", e.code == 400)

# Test 13: Payload too large
try:
    big = b"x" * 1_048_577
    r = urllib.request.Request(
        f"{BASE}/api/register",
        data=big,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer test-token",
            "Content-Length": str(len(big)),
        },
        method="POST",
    )
    urllib.request.urlopen(r, timeout=5)
    check("Payload too large -> 413", False)
except urllib.error.HTTPError as e:
    check("Payload too large -> 413", e.code == 413)

# --- Summary ---
print(f"\n=== Results: {passed} passed, {failed} failed ===\n")

server.shutdown()

exit(0 if failed == 0 else 1)
