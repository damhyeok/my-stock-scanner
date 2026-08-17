import gzip
import hashlib
import hmac
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub

import oracle_trigger_server


class OracleWebDataEndpointTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.payload = b"SQLite format 3\000test-payload"
        with gzip.open(self.root / "web_data.db.gz", "wb") as output:
            output.write(self.payload)
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), oracle_trigger_server.TriggerHandler
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.project_patch = patch.object(oracle_trigger_server, "PROJECT_DIR", self.root)
        self.environment_patch = patch.dict(
            os.environ, {"ORACLE_TRIGGER_SECRET": "test-secret"}
        )
        self.project_patch.start()
        self.environment_patch.start()
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.environment_patch.stop()
        self.project_patch.stop()
        self.temp_dir.cleanup()

    def _request(self, authorized=True):
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        body_hash = hashlib.sha256(b"").hexdigest()
        signature = hmac.new(
            b"test-secret",
            f"GET\n/web-data\n{timestamp}\n{nonce}\n{body_hash}".encode(),
            hashlib.sha256,
        ).hexdigest()
        headers = {}
        if authorized:
            headers = {
                "X-Trigger-Timestamp": timestamp,
                "X-Trigger-Nonce": nonce,
                "X-Trigger-Signature": signature,
            }
        return urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{self.server.server_port}/web-data",
                headers=headers,
            ),
            timeout=3,
        )

    def test_serves_authenticated_compressed_database(self):
        with self._request() as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "application/gzip")
            self.assertEqual(gzip.decompress(response.read()), self.payload)

    def test_rejects_unauthenticated_download(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self._request(authorized=False)
        self.assertEqual(context.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
