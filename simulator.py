#!/usr/bin/env python3
"""
Centinel Simulator
==================
A dedicated 'noisy node' that gives the pipeline live, realistic activity so the
console isn't empty between manual smoke tests:

  - resolves a mix of benign + DGA-style/suspicious-TLD domains
    -> Route 53 resolver query logs -> dns_detector -> dns.scored events
  - opens TCP connections to the seeded TEST-NET blacklist IPs
    -> VPC flow logs -> flow_detector -> flow.threat-intel-hit events
  - "downloads" content (benign files + the harmless EICAR test sample) and
    captures each artifact to the evidence S3 bucket, then emits an
    artifact.captured event -> visible in the alert log + drill-down

SAFETY: only resolves names and connects to TEST-NET-1 (192.0.2.0/24, RFC 5737,
unroutable) — it never touches a real malicious host. EICAR is the industry
standard antivirus *test* string, not malware.

Needs boto3 (installed in user-data). Runs on the dedicated simulator EC2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import socket
import string
import time
import urllib.request
from datetime import datetime, timezone

import boto3

BENIGN_DOMAINS = [
    "github.com", "cloudflare.com", "wikipedia.org", "amazon.com",
    "google.com", "debian.org", "ubuntu.com", "python.org",
]
SUSPICIOUS_TLDS = ["xyz", "top", "click", "tk", "gq", "cf", "loan"]
BENIGN_URLS = [
    "http://example.com/",
    "https://www.gnu.org/licenses/gpl-3.0.txt",
    "https://raw.githubusercontent.com/torvalds/linux/master/README",
]
# EICAR standard antivirus test string — harmless, flagged by every AV.
EICAR = r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

EB_SOURCE = "cloudguard-dns.simulator"


def rand_dga() -> str:
    label = "".join(random.choice(string.ascii_lowercase + string.digits)
                     for _ in range(random.randint(20, 38)))
    return f"{label}.{random.choice(SUSPICIOUS_TLDS)}"


def resolve(name: str) -> None:
    try:
        socket.getaddrinfo(name, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        pass  # NXDOMAIN/timeout still gets logged by the resolver


def connect(ip: str, port: int, timeout: float = 2.0) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
    except OSError:
        pass  # TEST-NET is unroutable; the SYN still shows in flow logs
    finally:
        s.close()


def capture(s3, events, bucket, bus, region, filename, content, source, severity):
    """Store a downloaded artifact in the evidence bucket + emit an event."""
    sha = hashlib.sha256(content).hexdigest()
    now = datetime.now(timezone.utc)
    key = (f"artifacts/year={now:%Y}/month={now:%m}/day={now:%d}/"
           f"{sha[:16]}-{filename}")
    s3.put_object(
        Bucket=bucket, Key=key, Body=content,
        ContentType="application/octet-stream",
        Metadata={"sha256": sha, "source": source[:1024]},
    )
    detail = {
        "severity": severity,
        "artifact": {
            "filename": filename,
            "sha256": sha,
            "size": len(content),
            "source": source,
            "s3_uri": f"s3://{bucket}/{key}",
            "s3_key": key,
        },
        "signals": [f"artifact:{filename}", f"sha256:{sha[:12]}"],
    }
    events.put_events(Entries=[{
        "Source": EB_SOURCE,
        "DetailType": "artifact.captured",
        "Detail": json.dumps(detail),
        "EventBusName": bus,
    }])
    print(f"  captured {filename} ({len(content)}B) sha={sha[:12]} -> {key}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Centinel traffic simulator")
    ap.add_argument("--bus", required=True, help="EventBridge bus name")
    ap.add_argument("--evidence-bucket", required=True)
    ap.add_argument("--region", default="eu-central-1")
    ap.add_argument("--blacklist-ips", default="192.0.2.66,192.0.2.123")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args()

    s3 = boto3.client("s3", region_name=args.region)
    events = boto3.client("events", region_name=args.region)
    bl_ips = [x.strip() for x in args.blacklist_ips.split(",") if x.strip()]

    print(f"  Centinel simulator up — bus={args.bus} evidence={args.evidence_bucket} "
          f"interval={args.interval}s", flush=True)

    n = 0
    while True:
        n += 1
        try:
            resolve(random.choice(BENIGN_DOMAINS))
            if random.random() < 0.7:
                resolve(rand_dga())                       # DGA-ish -> HIGH/MED
            connect(random.choice(bl_ips), random.choice([443, 80, 8080]))  # blacklist hit

            if n % 5 == 0:                                # periodic benign download
                url = random.choice(BENIGN_URLS)
                try:
                    with urllib.request.urlopen(url, timeout=8) as r:
                        content = r.read(65536)
                    fname = url.rstrip("/").split("/")[-1] or "index.html"
                    capture(s3, events, args.evidence_bucket, args.bus, args.region,
                            fname, content, url, "MED")
                except Exception as e:
                    print(f"  download failed: {e}", flush=True)

            if n % 12 == 0:                               # periodic 'malware' sample
                capture(s3, events, args.evidence_bucket, args.bus, args.region,
                        "eicar.com.txt", EICAR.encode(), "simulator/eicar-test", "HIGH")
        except Exception as e:
            print(f"  cycle error: {e}", flush=True)

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
