"""Build native desktop bundles with PyInstaller."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

APP_NAME = "BullishThreeConditionStockScreener"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PROJECT_ROOT / f"{APP_NAME}.spec"
BUILD_PATH = PROJECT_ROOT / "build"
DIST_PATH = PROJECT_ROOT / "dist"


def _expected_output_path() -> Path:
    if sys.platform == "darwin":
        return DIST_PATH / f"{APP_NAME}.app"
    return DIST_PATH / APP_NAME


def main() -> None:
    if not SPEC_PATH.exists():
        raise FileNotFoundError(f"找不到 PyInstaller spec 檔：{SPEC_PATH}")

    for output_path in (BUILD_PATH, DIST_PATH):
        if output_path.exists():
            shutil.rmtree(output_path)

    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(SPEC_PATH),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )

    built_output = _expected_output_path()
    if not built_output.exists():
        raise FileNotFoundError(f"打包完成但找不到預期產物：{built_output}")

    print(f"Built desktop bundle: {built_output}")


if __name__ == "__main__":
    main()
