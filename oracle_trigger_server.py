import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
STATUS_PATH = Path(os.environ.get("ORACLE_TRIGGER_STATUS_FILE", "/tmp/stock-scanner-trigger-status.json"))
LOG_PATH = Path(os.environ.get("ORACLE_TRIGGER_LOG_FILE", "/tmp/stock-scanner-trigger.log"))
MAX_CLOCK_SKEW_SECONDS = 300

load_dotenv(PROJECT_DIR / ".env")

STATE_LOCK = threading.Lock()
ACTIVE_PROCESS = None
USED_NONCES = {}


def now_iso():
    return datetime.now(KST).isoformat(timespec="seconds")


def read_status():
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "state": "idle",
            "progress": 0,
            "message": "실행 대기 중",
            "updated_at": now_iso(),
        }


def write_status(**values):
    status = read_status()
    status.update(values)
    status["updated_at"] = now_iso()
    temporary_path = STATUS_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    temporary_path.replace(STATUS_PATH)
    return status


def signature_payload(method, path, timestamp, nonce, body):
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}".encode("utf-8")


def is_authorized(handler, body):
    secret = os.environ.get("ORACLE_TRIGGER_SECRET", "")
    timestamp = handler.headers.get("X-Trigger-Timestamp", "")
    nonce = handler.headers.get("X-Trigger-Nonce", "")
    supplied = handler.headers.get("X-Trigger-Signature", "")
    if not secret or not timestamp or not nonce or not supplied:
        return False

    try:
        request_time = int(timestamp)
    except ValueError:
        return False
    current_time = int(time.time())
    if abs(current_time - request_time) > MAX_CLOCK_SKEW_SECONDS:
        return False

    with STATE_LOCK:
        expired = [key for key, used_at in USED_NONCES.items() if current_time - used_at > MAX_CLOCK_SKEW_SECONDS]
        for key in expired:
            USED_NONCES.pop(key, None)
        if nonce in USED_NONCES:
            return False

        expected = hmac.new(
            secret.encode("utf-8"),
            signature_payload(handler.command, handler.path, timestamp, nonce, body),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            return False
        USED_NONCES[nonce] = current_time
    return True


def watch_process(process, log_file, run_id):
    global ACTIVE_PROCESS
    return_code = process.wait()
    log_file.close()
    with STATE_LOCK:
        if return_code == 0:
            write_status(
                run_id=run_id,
                state="success",
                progress=100,
                message="분석과 웹 데이터 반영이 완료되었습니다.",
                finished_at=now_iso(),
            )
        else:
            write_status(
                run_id=run_id,
                state="failed",
                progress=100,
                message=f"분석 실행이 실패했습니다(종료 코드 {return_code}).",
                finished_at=now_iso(),
            )
        ACTIVE_PROCESS = None


def start_analysis():
    global ACTIVE_PROCESS
    with STATE_LOCK:
        if ACTIVE_PROCESS is not None and ACTIVE_PROCESS.poll() is None:
            return False, read_status()

        run_id = uuid.uuid4().hex
        log_file = LOG_PATH.open("a", encoding="utf-8")
        log_file.write(f"\n[{now_iso()}] manual run {run_id}\n")
        log_file.flush()
        process = subprocess.Popen(
            [sys.executable, str(PROJECT_DIR / "cloud_job.py"), "full-analysis"],
            cwd=PROJECT_DIR,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        ACTIVE_PROCESS = process
        status = write_status(
            run_id=run_id,
            state="running",
            progress=20,
            message="현재 시각 기준으로 주가 데이터 수집과 분석을 진행 중입니다.",
            requested_at=now_iso(),
            started_at=now_iso(),
            finished_at=None,
        )
        threading.Thread(
            target=watch_process,
            args=(process, log_file, run_id),
            daemon=True,
        ).start()
        return True, status


class TriggerHandler(BaseHTTPRequestHandler):
    server_version = "StockScannerTrigger/1.0"

    def send_json(self, status_code, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path != "/run":
            self.send_json(404, {"error": "not_found"})
            return
        if not is_authorized(self, body):
            self.send_json(401, {"error": "unauthorized"})
            return
        started, status = start_analysis()
        self.send_json(202 if started else 409, status)

    def do_GET(self):
        body = b""
        if self.path != "/status":
            self.send_json(404, {"error": "not_found"})
            return
        if not is_authorized(self, body):
            self.send_json(401, {"error": "unauthorized"})
            return
        self.send_json(200, read_status())

    def log_message(self, message_format, *args):
        print(f"[{now_iso()}] {self.client_address[0]} {message_format % args}")


def main():
    if not os.environ.get("ORACLE_TRIGGER_SECRET"):
        raise RuntimeError("ORACLE_TRIGGER_SECRET is not configured in .env")
    host = os.environ.get("ORACLE_TRIGGER_HOST", "0.0.0.0")
    port = int(os.environ.get("ORACLE_TRIGGER_PORT", "8765"))
    print(f"Oracle trigger server listening on {host}:{port}")
    ThreadingHTTPServer((host, port), TriggerHandler).serve_forever()


if __name__ == "__main__":
    main()
