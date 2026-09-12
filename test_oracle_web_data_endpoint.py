import gzip
import hashlib
import hmac
import json
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

    def _request(self, authorized=True, path="/web-data", payload=None, extra_headers=None):
        method = "POST" if payload is not None else "GET"
        body = json.dumps(payload).encode() if payload is not None else b""
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        body_hash = hashlib.sha256(body).hexdigest()
        signature = hmac.new(
            b"test-secret",
            f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}".encode(),
            hashlib.sha256,
        ).hexdigest()
        headers = {}
        if authorized:
            headers = {
                "X-Trigger-Timestamp": timestamp,
                "X-Trigger-Nonce": nonce,
                "X-Trigger-Signature": signature,
            }
        headers.update(extra_headers or {})
        return urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{self.server.server_port}{path}",
                data=body if payload is not None else None,
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

    def test_unchanged_version_returns_no_body(self):
        with self._request() as response:
            etag = response.headers["ETag"]
            response.read()
        with self.assertRaises(urllib.error.HTTPError) as context:
            self._request(extra_headers={"If-None-Match": etag})
        self.assertEqual(context.exception.code, 304)
        self.assertEqual(context.exception.read(), b"")
        with gzip.open(self.root / "replacement.gz", "wb") as output:
            output.write(b"new snapshot")
        os.replace(self.root / "replacement.gz", self.root / "web_data.db.gz")
        with self._request(extra_headers={"If-None-Match": etag}) as response:
            self.assertEqual(response.status, 200)
            self.assertNotEqual(response.headers["ETag"], etag)

    def test_watchlist_edits_do_not_start_analysis_or_export_jobs(self):
        with patch.object(oracle_trigger_server, "start_job") as start_job:
            with self._request(path="/watchlist", payload={"action": "add", "ticker": "005930", "name": "삼성전자"}) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response)["state"], "saved")
            with self._request(path="/watchlist") as response:
                self.assertEqual(json.load(response)["items"][0]["ticker"], "005930")
            with self._request(path="/watchlist", payload={"action": "remove", "ticker": "005930"}) as response:
                self.assertEqual(response.status, 200)
            with self._request(path="/watchlist") as response:
                self.assertEqual(json.load(response)["items"], [])
            start_job.assert_not_called()

    def test_watchlist_requires_authentication(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self._request(authorized=False, path="/watchlist", payload={"action": "remove", "ticker": "005930"})
        self.assertEqual(context.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
