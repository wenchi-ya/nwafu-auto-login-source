"""Offline Windows regression tests; no real credentials or login requests."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "campus_login.py"
spec = importlib.util.spec_from_file_location("campus_login", SOURCE)
campus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(campus)


@unittest.skipUnless(sys.platform == "win32", "Windows DPAPI and file locking required")
class CampusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.data_patch = patch.object(campus, "DATA", self.data)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        campus.write_json("config.json", {"network_name": "TEST-CAMPUS"})
        campus.write_json("state.json", {"failures": 0, "paused": False, "next_attempt": 0})
        self.secret = b'{"username":"test-only","password":"not-a-real-password"}'
        (self.data / "credentials.bin").write_bytes(campus.protect(self.secret))

    def test_dpapi_roundtrip(self):
        blob = (self.data / "credentials.bin").read_bytes()
        self.assertNotIn(self.secret, blob)
        self.assertEqual(campus.protect(blob, decrypt=True), self.secret)

    def test_origin_restriction(self):
        self.assertTrue(campus.trusted_url(campus.PORTAL))
        for url in ("http://portal.nwafu.edu.cn/", "https://portal.nwafu.edu.cn:444/",
                    "https://portal.nwafu.edu.cn.evil.example/", "https://user@portal.nwafu.edu.cn/"):
            self.assertFalse(campus.trusted_url(url))

    def test_online_does_not_login(self):
        with patch.object(campus, "probe", return_value=(True, False)), patch.object(campus, "login") as login:
            self.assertEqual(campus.run(), 0)
            login.assert_not_called()

    def test_other_network_does_not_login(self):
        with patch.object(campus, "probe", return_value=(False, False)), patch.object(campus, "profiles", return_value=["HOME"]), patch.object(campus, "login") as login:
            self.assertEqual(campus.run(), 0)
            login.assert_not_called()

    def test_named_campus_can_login(self):
        with patch.object(campus, "probe", return_value=(False, False)), patch.object(campus, "profiles", return_value=["TEST-CAMPUS"]), patch.object(campus, "login", return_value="success") as login:
            self.assertEqual(campus.run(), 0)
            login.assert_called_once()

    def test_failure_cooldown(self):
        with patch.object(campus, "probe", return_value=(False, True)), patch.object(campus, "login", return_value="failed") as login:
            self.assertEqual(campus.run(), 1)
            self.assertEqual(campus.run(), 0)
            login.assert_called_once()

    def test_bad_password_pauses(self):
        with patch.object(campus, "probe", return_value=(False, True)), patch.object(campus, "login", return_value="bad_credentials") as login:
            self.assertEqual(campus.run(), 1)
            self.assertEqual(campus.run(), 1)
            login.assert_called_once()

    def test_five_exceptions_pause_and_manual_success_recovers(self):
        campus.write_json("state.json", {"failures": 4, "next_attempt": 0})
        with patch.object(campus, "probe", return_value=(False, True)), patch.object(campus, "login", side_effect=RuntimeError("mock")) as login:
            self.assertEqual(campus.run(), 1)
            self.assertEqual(campus.run(), 1)
            login.assert_called_once()
        with patch.object(campus, "probe", return_value=(False, True)), patch.object(campus, "login", return_value="success"):
            self.assertEqual(campus.run(force=True), 0)
        state = json.loads((self.data / "state.json").read_text())
        self.assertFalse(state["paused"])
        self.assertEqual(state["failures"], 0)

    def test_cross_process_lock_on_empty_and_existing_file(self):
        code = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location('campus', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
with open(sys.argv[2], 'a+b') as f:
    print('ready', flush=True)
    print('acquired' if m.acquire_lock(f, float(sys.argv[3])) else 'busy', flush=True)
"""
        for content in (b"", b"0"):
            path = self.data / "run.lock"
            path.write_bytes(content)
            args = [sys.executable, "-c", code, str(SOURCE), str(path)]
            with path.open("a+b") as lock:
                self.assertTrue(campus.acquire_lock(lock))
                result = subprocess.run(args + ["0"], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines()[-1], "busy")
                waiting = subprocess.Popen(args + ["5"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.addCleanup(lambda p=waiting: p.kill() if p.poll() is None else None)
                self.assertEqual(waiting.stdout.readline().strip(), "ready")
                time.sleep(0.3)
            stdout, stderr = waiting.communicate(timeout=15)
            self.assertEqual(waiting.returncode, 0, stderr)
            self.assertEqual(stdout.strip(), "acquired")


if __name__ == "__main__":
    unittest.main()
