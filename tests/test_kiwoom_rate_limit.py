"""No live credentials or API requests are used in these tests."""
from contextlib import contextmanager
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch
import uuid

from kiwoom_rate_limit import MIN_REQUEST_INTERVAL, kiwoom_request_slot
from market_betting_engine.api_probe import KiwoomReadOnlyClient


class KiwoomRateLimitTests(unittest.TestCase):
    def test_same_key_is_serialized_across_processes(self):
        key = "test-" + uuid.uuid4().hex
        code = (
            "import time; from kiwoom_rate_limit import kiwoom_request_slot; "
            "\nwith kiwoom_request_slot(" + repr(key) + "): print(time.monotonic(), flush=True)"
        )
        children = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True) for _ in range(4)]
        stamps = []
        for child in children:
            out, err = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, err)
            stamps.append(float(out.strip()))
        stamps.sort()
        self.assertTrue(all(b-a >= MIN_REQUEST_INTERVAL - .015 for a,b in zip(stamps, stamps[1:])))
        self.assertTrue(all(sum(t <= x < t+1 for x in stamps) <= 3 for t in stamps))

    def test_failure_releases_lock_and_next_request_still_waits(self):
        key = "test-" + uuid.uuid4().hex
        with self.assertRaisesRegex(RuntimeError, "network"):
            with kiwoom_request_slot(key):
                raise RuntimeError("network")
        started = time.monotonic()
        with kiwoom_request_slot(key):
            self.assertGreaterEqual(time.monotonic()-started, MIN_REQUEST_INTERVAL-.01)

    def test_audit_client_gates_oauth_and_queries(self):
        client = KiwoomReadOnlyClient.__new__(KiwoomReadOnlyClient)
        client.app_key = "no-live-key"
        client.secret_key = "no-live-secret"
        client.base_url = "https://invalid.example"
        client.timeout = 1
        client._access_token = None
        gated = []
        @contextmanager
        def gate(key):
            gated.append(key)
            yield
        response = Mock(status_code=200)
        response.json.side_effect = [{"return_code":0,"token":"fake"}, {"return_code":0}]
        with patch("market_betting_engine.api_probe.kiwoom_request_slot", gate), patch(
            "market_betting_engine.api_probe.requests.post", return_value=response
        ) as post:
            client.query("/api/dostk/chart", "ka10080", {})
        self.assertEqual(gated, ["no-live-key", "no-live-key"])
        self.assertEqual(post.call_count, 2)


if __name__ == "__main__":
    unittest.main()

