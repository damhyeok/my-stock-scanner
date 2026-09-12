import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import cloud_bootstrap


class BootstrapTests(unittest.TestCase):
    def test_restart_consumes_marker_without_pulling(self):
        with patch.dict(os.environ, {cloud_bootstrap.REEXEC_MARKER: str(os.getpid())}):
            with patch.object(cloud_bootstrap.subprocess, "run") as run:
                self.assertTrue(cloud_bootstrap.prepare_job("unused"))
                run.assert_not_called()
                self.assertNotIn(cloud_bootstrap.REEXEC_MARKER, os.environ)

    def test_help_does_not_update(self):
        with patch.object(cloud_bootstrap.sys, "argv", ["cloud_job.py", "--help"]):
            with patch.dict(os.environ, {}, clear=True):
                with patch.object(cloud_bootstrap.subprocess, "run") as run:
                    self.assertFalse(cloud_bootstrap.prepare_job("unused"))
                    run.assert_not_called()

    def test_pull_precedes_exec_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(cloud_bootstrap.sys, "argv", ["cloud_job.py", "sector-flow"]):
                with patch.dict(os.environ, {cloud_bootstrap.REEXEC_MARKER: "other-process"}):
                    events = []
                    def run(*args, **kwargs):
                        events.append("pull")
                        self.assertTrue(kwargs["check"])
                    def execute(executable, args, env):
                        events.append("exec")
                        self.assertEqual(args[-1], "sector-flow")
                        self.assertEqual(env[cloud_bootstrap.REEXEC_MARKER], str(os.getpid()))
                        raise SystemExit(0)
                    with patch.object(cloud_bootstrap.subprocess, "run", side_effect=run):
                        with patch.object(cloud_bootstrap.os, "execve", side_effect=execute):
                            with self.assertRaises(SystemExit):
                                cloud_bootstrap.prepare_job(directory)
                    self.assertEqual(events, ["pull", "exec"])

    def test_failed_update_never_executes_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True):
                with patch.object(cloud_bootstrap.sys, "argv", ["cloud_job.py", "full-analysis"]):
                    with patch.object(cloud_bootstrap.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "git")):
                        with patch.object(cloud_bootstrap.os, "execve") as execute:
                            with self.assertRaises(subprocess.CalledProcessError):
                                cloud_bootstrap.prepare_job(directory)
                            execute.assert_not_called()

    def test_bootstrap_is_before_analysis_imports(self):
        source = (Path(__file__).resolve().parents[1] / "cloud_job.py").read_text(encoding="utf-8")
        self.assertLess(source.index("_BOOTSTRAPPED = prepare_job"), source.index("from dotenv"))
        self.assertLess(source.index("_BOOTSTRAPPED = prepare_job"), source.index("from market_strength"))


if __name__ == "__main__":
    unittest.main()
