"""Desktop launcher used by bundled macOS and Windows builds."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback

import certifi
from streamlit.web import bootstrap

APP_NAME = "BullishThreeConditionStockScreener"
APP_SCRIPT_NAME = "app.py"
LOCALHOST = "127.0.0.1"
DEFAULT_IDLE_TIMEOUT_SECONDS = 15.0
IDLE_POLL_INTERVAL_SECONDS = 1.0
LOG_FILE_NAME = "launcher-error.log"


def _resource_root() -> Path:
    if getattr(sys, "frozen", False):
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resolve_app_path() -> Path:
    app_path = _resource_root() / APP_SCRIPT_NAME
    if not app_path.exists():
        raise FileNotFoundError(f"找不到必要的應用程式檔案：{app_path}")
    return app_path


def _resolve_port(host: str) -> int:
    configured_port = os.environ.get("STOCK_SCREENER_PORT", "").strip()
    if configured_port:
        try:
            port = int(configured_port)
        except ValueError as exc:
            raise ValueError("STOCK_SCREENER_PORT 必須是 1 到 65535 的整數。") from exc
        if not 1 <= port <= 65535:
            raise ValueError("STOCK_SCREENER_PORT 必須是 1 到 65535 的整數。")
        return port
    return _find_available_port(host)


def _resolve_idle_timeout_seconds() -> float:
    configured_timeout = os.environ.get("STOCK_SCREENER_IDLE_TIMEOUT_SECONDS", "").strip()
    if not configured_timeout:
        return DEFAULT_IDLE_TIMEOUT_SECONDS
    try:
        timeout = float(configured_timeout)
    except ValueError as exc:
        raise ValueError("STOCK_SCREENER_IDLE_TIMEOUT_SECONDS 必須是大於 0 的數值。") from exc
    if timeout <= 0:
        raise ValueError("STOCK_SCREENER_IDLE_TIMEOUT_SECONDS 必須是大於 0 的數值。")
    return timeout


def _find_available_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _build_streamlit_flags(host: str, port: int) -> dict[str, object]:
    return {
        "server_address": host,
        "browser_serverAddress": host,
        "server_port": port,
        "server_headless": False,
        "server_fileWatcherType": "none",
        "server_enableCORS": True,
        "browser_gatherUsageStats": False,
        "global_developmentMode": False,
    }


def _start_idle_shutdown_monitor(server, idle_timeout_seconds: float) -> None:
    def _monitor() -> None:
        has_seen_browser_connection = False
        last_connected_at = time.monotonic()

        while True:
            browser_connected = bool(server.browser_is_connected)
            if browser_connected:
                has_seen_browser_connection = True
                last_connected_at = time.monotonic()
            elif has_seen_browser_connection and time.monotonic() - last_connected_at >= idle_timeout_seconds:
                server.stop()
                return

            time.sleep(IDLE_POLL_INTERVAL_SECONDS)

    thread = threading.Thread(
        target=_monitor,
        name="streamlit-idle-shutdown-monitor",
        daemon=True,
    )
    thread.start()


def _runtime_log_dir() -> Path:
    home = Path.home()
    if sys.platform == "win32":
        base_dir = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base_dir = home / "Library" / "Logs"
    else:
        base_dir = Path(os.environ.get("XDG_STATE_HOME", home / ".local" / "state"))
    return base_dir / APP_NAME


def _write_error_log(error_text: str) -> Path:
    log_dir = _runtime_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILE_NAME
    log_path.write_text(error_text, encoding="utf-8")
    return log_path


def _show_error_dialog(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, message)
        root.destroy()
    except Exception:
        pass


def main() -> None:
    try:
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

        host = LOCALHOST
        port = _resolve_port(host)
        idle_timeout_seconds = _resolve_idle_timeout_seconds()
        app_path = resolve_app_path()
        flag_options = _build_streamlit_flags(host, port)
        bootstrap.load_config_options(flag_options)
        original_on_server_start = bootstrap._on_server_start

        def _patched_on_server_start(server) -> None:
            original_on_server_start(server)
            _start_idle_shutdown_monitor(server, idle_timeout_seconds)

        bootstrap._on_server_start = _patched_on_server_start
        try:
            bootstrap.run(
                str(app_path),
                is_hello=False,
                args=[],
                flag_options=flag_options,
            )
        finally:
            bootstrap._on_server_start = original_on_server_start
    except Exception:
        log_path = _write_error_log(traceback.format_exc())
        _show_error_dialog(f"應用程式啟動失敗，詳細錯誤已寫入：\n{log_path}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
