"""Host-wide Kiwoom request gate, shared by collectors and audit clients.

Serializes the same app key across processes on this host. Hold the SQLite
write lock during HTTP and wait 0.4 seconds before *every* request, including
OAuth and failures. This leaves headroom below three requests per second.
Other computers using the same credentials need their own coordination.
"""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import time

MIN_REQUEST_INTERVAL = 0.4


@contextmanager
def kiwoom_request_slot(app_key):
    fingerprint = hashlib.sha256(app_key.encode("utf-8")).hexdigest()[:24]
    path = Path(tempfile.gettempdir()) / ("stock-scanner-kiwoom-" + fingerprint + ".sqlite3")
    connection = sqlite3.connect(str(path), timeout=120)
    try:
        # Fail closed if the lock cannot be obtained; never send unthrottled.
        connection.execute("BEGIN IMMEDIATE")
        time.sleep(MIN_REQUEST_INTERVAL)
        yield
        connection.commit()
    finally:
        connection.close()

