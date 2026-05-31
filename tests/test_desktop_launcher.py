import os
from pathlib import Path
import unittest
from unittest.mock import patch

import desktop_launcher


class DesktopLauncherTests(unittest.TestCase):
    def test_resolve_app_path_uses_repository_root_during_development(self):
        app_path = desktop_launcher.resolve_app_path()

        self.assertEqual(app_path, Path(desktop_launcher.__file__).resolve().parent / "app.py")

    def test_resolve_app_path_prefers_meipass_when_frozen(self):
        with patch.object(desktop_launcher.sys, "frozen", True, create=True), patch.object(
            desktop_launcher.sys,
            "_MEIPASS",
            "/tmp/bundled-app",
            create=True,
        ), patch.object(Path, "exists", return_value=True):
            app_path = desktop_launcher.resolve_app_path()

        self.assertEqual(app_path, Path("/tmp/bundled-app/app.py"))

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


if __name__ == "__main__":
    unittest.main()
