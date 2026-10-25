"""Temporary smoke test for the SIM800C SMS Manager (auto-detect + docs page)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def request(method: str, url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            raw = res.read().decode()
            return res.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw
        return exc.code, parsed


def fetch_text(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=15) as res:
        return res.status, res.read().decode()


def start_server(port: int, mock: bool) -> subprocess.Popen:
    env = {**os.environ, "HOST": "127.0.0.1", "PORT": str(port),
           "SIM800C_MOCK": "true" if mock else "false"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=BASE_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    for _ in range(60):
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early:\n{proc.stdout.read()}")
        try:
            status, _ = request("GET", f"http://127.0.0.1:{port}/api/health")
            if status == 200:
                return proc
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise RuntimeError("server did not become healthy in time")


def check(label: str, ok: bool) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


def main() -> int:
    results: list[bool] = []

    # --- real-hardware mode: no SIM800C plugged in (Windows box, COM1 only) ---
    print("== real mode (no modem attached) ==")
    proc = start_server(8055, mock=False)
    try:
        status, html = fetch_text("http://127.0.0.1:8055/")
        results.append(check("UI loads", status == 200))
        results.append(check("UI has auto-detect button", "btn-detect" in html))
        results.append(check("UI has nav link to docs", "/documentation" in html))

        docs_status, docs = fetch_text("http://127.0.0.1:8055/documentation")
        results.append(check("/documentation returns 200", docs_status == 200))
        results.append(check("docs: port auto-detection section", "Port auto-detection" in docs))
        results.append(check("docs: shows SIM800C_PORT=auto", "SIM800C_PORT=auto" in docs))
        results.append(check("docs: API reference table", "/api/modem/detect" in docs))
        results.append(check("docs: integration examples", "Integration examples" in docs))
        results.append(check("docs: configuration table", "SIM800C_AUTO_RECONNECT" in docs))
        results.append(check("docs: active nav highlight", "nav-link active" in docs))

        status, ports = request("GET", "http://127.0.0.1:8055/api/modem/ports")
        results.append(check(
            "ports endpoint scores ports",
            status == 200 and isinstance(ports, list)
            and all({"device", "score", "match", "reason"} <= set(p) for p in ports),
        ))

        status, det = request("POST", "http://127.0.0.1:8055/api/modem/detect")
        results.append(check(
            "detect returns 404 with helpful detail when no modem",
            status == 404 and isinstance(det, dict)
            and "No SIM800C detected" in det.get("detail", ""),
        ))

        status, st = request("GET", "http://127.0.0.1:8055/api/modem/status")
        results.append(check(
            "status reports detection failure",
            status == 200 and st["connected"] is False
            and "No SIM800C detected" in st["last_error"],
        ))

        schema = json.loads(fetch_text("http://127.0.0.1:8055/openapi.json")[1])
        results.append(check(
            "openapi exposes /api/modem/detect",
            "/api/modem/detect" in schema.get("paths", {}),
        ))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    # --- mock mode: full message flows ---
    print("== mock mode ==")
    proc = start_server(8056, mock=True)
    try:
        status, st = request("GET", "http://127.0.0.1:8056/api/modem/status")
        results.append(check("mock modem connects", status == 200 and st["connected"] and st["mock"]))

        status, sent = request("POST", "http://127.0.0.1:8056/api/sms/send",
                               {"phone": "+15551234567", "message": "smoke test"})
        results.append(check("send SMS", status == 200 and sent["status"] == "sent"))

        status, inj = request("POST", "http://127.0.0.1:8056/api/sms/inject",
                              {"phone": "+15550001111", "message": "inbound smoke"})
        results.append(check("inject inbound SMS", status == 200 and inj["direction"] == "inbound"))

        status, listing = request("GET", "http://127.0.0.1:8056/api/sms")
        results.append(check("list messages", status == 200 and listing["total"] >= 4))

        status, stats = request("GET", "http://127.0.0.1:8056/api/stats")
        results.append(check("stats", status == 200 and stats["total"] == listing["total"]))

        status, _ = request("POST", "http://127.0.0.1:8056/api/sms/sync")
        results.append(check("sync inbox", status == 200))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())