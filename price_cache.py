"""On-disk snapshot cache for whole OHLCV download results.

The downloader uses ``auto_adjust=True`` (data_loader), so yfinance retroactively
re-adjusts the ENTIRE price history whenever a stock goes ex-dividend or splits.
That makes incremental "fetch only the missing tail and append" caching unsafe:
appending new bars (new adjustment basis) onto cached old bars (old basis) mixes
two bases and manufactures phantom breakouts in the close-vs-line comparisons.

So this cache never merges. It stores the WHOLE result of one download as an
atomic snapshot keyed to (codes, start, end), and only ever replaces whole
snapshots. Each snapshot is internally consistent by construction. Everything is
fail-open: any read/write/parse error silently falls back to a live download, so
the cache can never break a screening run.

Freshness: a window that ends in the past is final and reused for up to
``HISTORICAL_MAX_AGE_DAYS`` (bounded so late provider corrections eventually
refresh). A window that includes today still has an evolving last bar, so it is
only reused within ``RECENT_END_TTL_SECONDS`` — matching the in-memory
st.cache_data TTL, so behavior is unchanged for the current day but now survives
the frequent desktop restarts (the bundled app idle-shuts-down after 15s).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

RECENT_END_TTL_SECONDS = 1800  # evolving current-day data: match the 30-min in-memory TTL
HISTORICAL_MAX_AGE_DAYS = 7  # final historical windows: refresh weekly for late corrections

_SNAPSHOT_RESULT = tuple  # (daily_data: DataFrame, success: list, failed: list, errors: list)


def default_cache_dir() -> Path:
    """Per-user cache directory (mirrors desktop_launcher's runtime-dir choice)."""
    home = Path.home()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache"))
    return base / "BullishThreeConditionStockScreener" / "price_snapshots"


def _snapshot_key(codes, start_date, end_date) -> str:
    payload = "|".join(sorted(str(code) for code in codes)) + f"@{start_date}~{end_date}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _paths(cache_dir: Path, key: str) -> tuple[Path, Path]:
    return cache_dir / f"{key}.parquet", cache_dir / f"{key}.json"


def _as_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return pd.to_datetime(value).date()


def save_snapshot(cache_dir, codes, start_date, end_date, result, now: Optional[datetime] = None) -> None:
    """Persist a whole download result. Fail-open: any error is logged and ignored."""
    try:
        now = now or datetime.now()
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        daily_data, success_list, failed_list, download_errors = result
        parquet_path, meta_path = _paths(cache_dir, _snapshot_key(codes, start_date, end_date))
        # Atomic parquet write so a crash mid-write never leaves a torn file that
        # a later read would trust.
        tmp_path = parquet_path.with_suffix(".parquet.tmp")
        daily_data.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, parquet_path)
        meta_path.write_text(
            json.dumps(
                {
                    "captured_at": now.isoformat(),
                    "end": str(end_date),
                    "success_list": list(success_list),
                    "failed_list": list(failed_list),
                    "download_errors": list(download_errors),
                }
            ),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001 - cache is best-effort
        logger.warning("價格快照寫入失敗（略過快取）：%s", exc)


def load_snapshot(cache_dir, codes, start_date, end_date, now: Optional[datetime] = None):
    """Return a cached (daily_data, success, failed, errors) tuple, or None.

    Returns None on any miss, staleness, or error so the caller downloads live.
    """
    try:
        now = now or datetime.now()
        cache_dir = Path(cache_dir)
        parquet_path, meta_path = _paths(cache_dir, _snapshot_key(codes, start_date, end_date))
        if not parquet_path.exists() or not meta_path.exists():
            return None
        info = json.loads(meta_path.read_text(encoding="utf-8"))
        captured_at = datetime.fromisoformat(info["captured_at"])
        age_seconds = (now - captured_at).total_seconds()
        if age_seconds < 0:
            return None  # clock moved backwards; distrust the snapshot
        if _as_date(end_date) >= now.date():
            # Window includes the still-evolving current day: short TTL only.
            if age_seconds > RECENT_END_TTL_SECONDS:
                return None
        elif age_seconds > HISTORICAL_MAX_AGE_DAYS * 86400:
            return None
        daily_data = pd.read_parquet(parquet_path)
        return daily_data, list(info["success_list"]), list(info["failed_list"]), list(info["download_errors"])
    except Exception as exc:  # noqa: BLE001 - cache is best-effort
        logger.warning("價格快照讀取失敗（改為重新下載）：%s", exc)
        return None
