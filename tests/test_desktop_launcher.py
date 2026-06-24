import os
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import patch

import desktop_launcher


class DesktopLauncherTests(unittest.TestCase):
    def test_resolve_app_path_uses_repository_root_during_development(self):
        app_path = desktop_launcher.resolve_app_path()

        self.assertEqual(app_path, Path(desktop_launcher.__file__).resolve().parent / "app.py")

    def test_resolve_app_path_prefers_meipass_when_frozen(self):
        # Patch _resource_root's inputs and let .exists() run for real against a
        # temp dir that actually contains app.py (no global Path.exists stub).
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "app.py").write_text("# stub", encoding="utf-8")
            with patch.object(desktop_launcher.sys, "frozen", True, create=True), patch.object(
                desktop_launcher.sys, "_MEIPASS", tmp, create=True
            ):
                app_path = desktop_launcher.resolve_app_path()

            self.assertEqual(app_path, Path(tmp) / "app.py")

    def test_resolve_app_path_raises_when_app_py_missing(self):
        with tempfile.TemporaryDirectory() as tmp:  # empty dir, no app.py
            with patch.object(desktop_launcher, "_resource_root", return_value=Path(tmp)):
                with self.assertRaises(FileNotFoundError):
                    desktop_launcher.resolve_app_path()

    def test_build_streamlit_flags_use_localhost_desktop_defaults(self):
        flags = desktop_launcher._build_streamlit_flags("127.0.0.1", 8510)

        self.assertEqual(flags["server_address"], "127.0.0.1")
        self.assertEqual(flags["browser_serverAddress"], "127.0.0.1")
        self.assertEqual(flags["server_port"], 8510)
        self.assertFalse(flags["server_headless"])
        self.assertEqual(flags["server_fileWatcherType"], "none")
        self.assertTrue(flags["server_enableCORS"])
        self.assertFalse(flags["browser_gatherUsageStats"])

    def test_resolve_port_honors_valid_environment_override(self):
        with patch.dict(os.environ, {"STOCK_SCREENER_PORT": "9000"}, clear=False):
            port = desktop_launcher._resolve_port("127.0.0.1")

        self.assertEqual(port, 9000)

    def test_resolve_port_rejects_invalid_environment_override(self):
        with patch.dict(os.environ, {"STOCK_SCREENER_PORT": "not-a-port"}, clear=False):
            with self.assertRaisesRegex(ValueError, "STOCK_SCREENER_PORT"):
                desktop_launcher._resolve_port("127.0.0.1")

    def test_resolve_idle_timeout_uses_default_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STOCK_SCREENER_IDLE_TIMEOUT_SECONDS", None)
            self.assertEqual(
                desktop_launcher._resolve_idle_timeout_seconds(),
                desktop_launcher.DEFAULT_IDLE_TIMEOUT_SECONDS,
            )

    def test_resolve_idle_timeout_honors_valid_override(self):
        with patch.dict(os.environ, {"STOCK_SCREENER_IDLE_TIMEOUT_SECONDS": "42.5"}, clear=False):
            self.assertEqual(desktop_launcher._resolve_idle_timeout_seconds(), 42.5)

    def test_resolve_idle_timeout_rejects_non_numeric(self):
        with patch.dict(os.environ, {"STOCK_SCREENER_IDLE_TIMEOUT_SECONDS": "soon"}, clear=False):
            with self.assertRaisesRegex(ValueError, "STOCK_SCREENER_IDLE_TIMEOUT_SECONDS"):
                desktop_launcher._resolve_idle_timeout_seconds()

    def test_resolve_idle_timeout_rejects_non_positive(self):
        with patch.dict(os.environ, {"STOCK_SCREENER_IDLE_TIMEOUT_SECONDS": "0"}, clear=False):
            with self.assertRaisesRegex(ValueError, "STOCK_SCREENER_IDLE_TIMEOUT_SECONDS"):
                desktop_launcher._resolve_idle_timeout_seconds()

    def test_idle_monitor_stops_server_after_browser_disconnects(self):
        class FakeServer:
            def __init__(self):
                self._connected = True
                self.stopped = threading.Event()

            @property
            def browser_is_connected(self):
                return self._connected

            def stop(self):
                self.stopped.set()

        server = FakeServer()
        with patch.object(desktop_launcher, "IDLE_POLL_INTERVAL_SECONDS", 0.01):
            desktop_launcher._start_idle_shutdown_monitor(server, idle_timeout_seconds=0.05)
            time.sleep(0.05)
            server._connected = False  # browser disconnects -> idle window starts
            self.assertTrue(server.stopped.wait(timeout=3.0))

    def test_idle_monitor_does_not_stop_before_any_connection(self):
        class NeverConnectedServer:
            def __init__(self):
                self.stopped = False

            @property
            def browser_is_connected(self):
                return False

            def stop(self):
                self.stopped = True

        server = NeverConnectedServer()
        with patch.object(desktop_launcher, "IDLE_POLL_INTERVAL_SECONDS", 0.01):
            desktop_launcher._start_idle_shutdown_monitor(server, idle_timeout_seconds=0.02)
            time.sleep(0.1)
        # Never connected -> must never auto-shutdown.
        self.assertFalse(server.stopped)

    def test_idle_monitor_exits_cleanly_when_connection_state_raises(self):
        class BrokenServer:
            def __init__(self):
                self.calls = 0
                self.stopped = False

            @property
            def browser_is_connected(self):
                self.calls += 1
                raise AttributeError("server torn down")

            def stop(self):
                self.stopped = True

        server = BrokenServer()
        with patch.object(desktop_launcher, "IDLE_POLL_INTERVAL_SECONDS", 0.01):
            desktop_launcher._start_idle_shutdown_monitor(server, idle_timeout_seconds=0.02)
            time.sleep(0.1)
        # On the first failed poll the monitor exits cleanly (no spin, no stop()).
        self.assertEqual(server.calls, 1)
        self.assertFalse(server.stopped)

    def test_main_runs_without_idle_monitor_when_on_server_start_absent(self):
        calls = {}

        class FakeBootstrap:
            def load_config_options(self, options):
                calls["loaded"] = True

            def run(self, *args, **kwargs):
                calls["ran"] = True

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "app.py").write_text("# stub", encoding="utf-8")
            with patch.object(desktop_launcher, "bootstrap", FakeBootstrap()), patch.object(
                desktop_launcher, "_resource_root", return_value=Path(tmp)
            ), patch.dict(os.environ, {"STOCK_SCREENER_PORT": "8599"}, clear=False):
                desktop_launcher.main()

        self.assertTrue(calls.get("ran"))


if __name__ == "__main__":
    unittest.main()
