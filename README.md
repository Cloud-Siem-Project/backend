# CloudSIEM — Lightweight Master-Worker SIEM Prototype

A zero-dependency Python prototype demonstrating master-worker orchestration
for log collection and node monitoring.

## Architecture

```
┌─────────────────────────────────────────────┐
│                   MASTER                     │
│                                              │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│  │ HTTP API │  │  Health   │  │Interactive│  │
│  │ :9800    │  │  Checker  │  │   CLI     │  │
│  │          │  │ (30s loop)│  │           │  │
│  └────┬─────┘  └────┬─────┘  └───────────┘  │
│       │              │                       │
│       └──────┬───────┘                       │
│              ▼                               │
│       ┌────────────┐                         │
│       │ nodes{}    │  ◄── in-memory store    │
│       └────────────┘                         │
└──────────────▲───────────────────────────────┘
               │  HTTP POST
    ┌──────────┼──────────┐
    │          │          │
┌───┴───┐ ┌───┴───┐ ┌───┴───┐
│Worker │ │Worker │ │Worker │
│node-01│ │node-02│ │node-03│
└───────┘ └───────┘ └───────┘

Each worker collects:
  • hostname, main IP, kernel version
  • TCP listening ports + process names
  • periodic heartbeat → master
```

## Requirements

- Python 3.10+
- No external dependencies (stdlib only)
- Workers need `ss` or `netstat` for port enumeration

## Quick Start

### 1. Start the master

```bash
python3 master.py --port 9800
```

You'll drop into an interactive shell:

```
╔══════════════════════════════════════════════════╗
║          CloudSIEM Master · Interactive CLI      ║
╚══════════════════════════════════════════════════╝
 Type 'help' for available commands.

cloudsiem>
```

### 2. Start workers (on each node)

```bash
python3 worker.py --master http://<master-ip>:9800
```

Options:

| Flag          | Default       | Description                       |
|---------------|---------------|-----------------------------------|
| `--master`    | *(required)*  | Master URL                        |
| `--node-id`   | hostname      | Custom node identifier            |
| `--interval`  | 20            | Heartbeat interval in seconds     |

### 3. Use the CLI

| Command           | Description                              |
|-------------------|------------------------------------------|
| `status`          | Cluster overview (total / up / down)     |
| `nodes`           | List all nodes with status + last seen   |
| `node <node_id>`  | Detailed info for a single node          |
| `drop <node_id>`  | Unregister a node                        |
| `clear`           | Clear screen                             |
| `help`            | Show all commands                        |
| `exit` / Ctrl-D   | Shutdown master                          |

## API Endpoints

| Method | Path              | Description             |
|--------|-------------------|-------------------------|
| POST   | `/api/register`   | Worker registration     |
| POST   | `/api/heartbeat`  | Worker heartbeat        |
| GET    | `/api/nodes`      | Dump cluster state JSON |

### Register payload

```json
{
  "node_id": "web-01",
  "hostname": "web-01.prod",
  "ip": "10.0.1.12",
  "kernel": "6.1.0-18-amd64",
  "ports": [
    {"port": 22, "proto": "tcp", "process": "sshd", "pid": "812", "bind": "0.0.0.0"},
    {"port": 443, "proto": "tcp", "process": "nginx", "pid": "1501", "bind": "0.0.0.0"}
  ],
  "worker_version": "0.1.0"
}
```

## Health Check Logic

- Master sweeps every **30 seconds**
- If a node hasn't sent a heartbeat in **45 seconds**, it's marked `DOWN`
- Workers send heartbeats every **20 seconds** by default (configurable)
- If a heartbeat fails (master says "not registered"), the worker auto-re-registers
- Events (register, UP/DOWN transitions) appear live in the CLI

## Testing Locally

You can run everything on a single machine to test:

```bash
# terminal 1 — master
python3 master.py

# terminal 2 — worker A
python3 worker.py --master http://127.0.0.1:9800 --node-id node-alpha

# terminal 3 — worker B
python3 worker.py --master http://127.0.0.1:9800 --node-id node-beta

# in the master CLI:
cloudsiem> status
cloudsiem> nodes
cloudsiem> node node-alpha

# kill worker A (Ctrl-C), wait ~45s, then:
cloudsiem> nodes    # node-alpha should show DOWN
```

## Extending

Some natural next steps:

- **Persistence**: swap the in-memory `nodes{}` dict for SQLite or Redis
- **Log forwarding**: have workers tail syslog / journald and POST log lines
- **TLS + auth**: add token-based auth to the API, run behind nginx with TLS
- **Alert rules**: define threshold rules on the master (e.g., port 22 exposed → alert)
- **Web dashboard**: small Flask/FastAPI frontend to visualize cluster state
