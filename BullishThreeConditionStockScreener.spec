# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

APP_NAME = "BullishThreeConditionStockScreener"
BUNDLE_IDENTIFIER = "com.masonw0711.bullish-three-condition-stock-screener"
PROJECT_ROOT = Path(SPEC).resolve().parent

datas = [(str(PROJECT_ROOT / "app.py"), ".")]
for package_name in ("streamlit", "certifi"):
    datas += collect_data_files(package_name)

for distribution_name in (
    "streamlit",
    "certifi",
    "yfinance",
    "plotly",
    "openpyxl",
    "beautifulsoup4",
    "lxml",
):
    datas += copy_metadata(distribution_name)

hiddenimports = sorted(
    {
        "app",
        "chart_engine",
        "config",
        "data_loader",
        "export_engine",
        "openpyxl",
        "signal_engine",
        "lxml.etree",
        "lxml._elementpath",
        *collect_submodules("streamlit"),
    }
)

a = Analysis(
    ["desktop_launcher.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=sys.platform == "win32",
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        bundle_identifier=BUNDLE_IDENTIFIER,
    )
