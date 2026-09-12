import gzip
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import web_snapshot_client as client


class SnapshotClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime.db"
        self.source = self.root / "source.db"
        conn = sqlite3.connect(self.source)
        conn.execute("CREATE TABLE sample (value)")
        conn.execute("INSERT INTO sample VALUES (42)")
        conn.commit()
        conn.close()
        self.payload = gzip.compress(self.source.read_bytes())
        client._VERSIONS.clear()

    def tearDown(self):
        self.temp.cleanup()

    def refresh(self):
        return client.refresh_snapshot("http://test", "secret", self.runtime, self.root / "missing.gz")

    def response(self, status=200, payload=None):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = status
        response.headers = {"ETag": 'W/"test"'}
        response.iter_content.return_value = [self.payload if payload is None else payload]
        return response

    def test_download_then_304_skips_decompression(self):
        with patch.object(client.requests, "get", return_value=self.response()):
            self.refresh()
        before = self.runtime.stat().st_mtime_ns
        response = self.response(304)
        with patch.object(client.requests, "get", return_value=response) as get:
            with patch.object(client, "decompress_web_database") as decompress:
                self.assertIn("변경 없음", self.refresh()[1])
                decompress.assert_not_called()
            self.assertEqual(get.call_args.kwargs["headers"]["If-None-Match"], 'W/"test"')
        self.assertEqual(self.runtime.stat().st_mtime_ns, before)

    def test_failure_and_corruption_preserve_last_good_database(self):
        with patch.object(client.requests, "get", return_value=self.response()):
            self.refresh()
        expected = self.runtime.read_bytes()
        for response in (self.response(payload=b"broken"), self.response(payload=gzip.compress(b"SQLite format 3\0" + b"broken"))):
            with patch.object(client.requests, "get", return_value=response):
                self.assertIn("갱신 실패", self.refresh()[1])
                self.assertEqual(self.runtime.read_bytes(), expected)
        with patch.object(client.requests, "get", side_effect=TimeoutError):
            self.assertIn("갱신 실패", self.refresh()[1])
            self.assertEqual(self.runtime.read_bytes(), expected)

    def test_missing_local_file_does_not_send_etag(self):
        client._VERSIONS[("http://test", str(self.runtime))] = "old"
        with patch.object(client.requests, "get", return_value=self.response()) as get:
            self.refresh()
            self.assertNotIn("If-None-Match", get.call_args.kwargs["headers"])
