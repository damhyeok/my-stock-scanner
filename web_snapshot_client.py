"""Conditional downloads with a last-known-good, atomically restored snapshot."""

import hashlib
import hmac
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

import requests

from web_database import decompress_web_database

_LOCK = threading.Lock()
_VERSIONS = {}


def refresh_snapshot(base_url, secret, runtime_path, bootstrap_path):
    runtime = Path(runtime_path)
    with _LOCK:
        key = (base_url, str(runtime))
        if secret:
            timestamp, nonce = str(int(time.time())), uuid.uuid4().hex
            payload = f"GET\n/web-data\n{timestamp}\n{nonce}\n{hashlib.sha256(b'').hexdigest()}"
            headers = {
                "X-Trigger-Timestamp": timestamp,
                "X-Trigger-Nonce": nonce,
                "X-Trigger-Signature": hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest(),
            }
            if runtime.is_file() and key in _VERSIONS:
                headers["If-None-Match"] = _VERSIONS[key]
            download = None
            try:
                with requests.get(f"{base_url}/web-data", headers=headers, stream=True, timeout=(5, 30)) as response:
                    if response.status_code == 304:
                        if not runtime.is_file():
                            raise ValueError("304 without a local snapshot")
                        return str(runtime), "Oracle 최신 DB (변경 없음)"
                    response.raise_for_status()
                    fd, download = tempfile.mkstemp(suffix=".db.gz")
                    os.close(fd)
                    with open(download, "wb") as output:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                output.write(chunk)
                    decompress_web_database(download, runtime)
                    _VERSIONS.pop(key, None)
                    if response.headers.get("ETag"):
                        _VERSIONS[key] = response.headers["ETag"]
                    return str(runtime), "Oracle 최신 DB"
            except Exception:
                if runtime.is_file():
                    return str(runtime), "마지막 정상 DB (Oracle 갱신 실패)"
            finally:
                if download and os.path.exists(download):
                    os.remove(download)
        if runtime.is_file():
            return str(runtime), "저장된 DB"
        try:
            decompress_web_database(bootstrap_path, runtime)
            return str(runtime), "내장 압축 DB"
        except Exception:
            return "web_data.db", "로컬 DB"
