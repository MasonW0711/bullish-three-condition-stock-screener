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
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

RECENT_END_TTL_SECONDS = 1800  # evolving current-day data: match the 30-min in-memory TTL
# Historical windows are "final", but yfinance retro-adjusts the whole series —
# INCLUDING Volume — on any split / stock dividend. A snapshot older than one
# adjustment cycle can therefore carry a pre-split volume basis, which flips the
# absolute min_volume gate and changes the reported signal set. One day still
# delivers the entire desktop win (the bundle idle-shuts-down every 15s, so a
# day's use is many cold starts) while bounding that drift to a single session.
HISTORICAL_MAX_AGE_DAYS = 1
_KEY_FIELD_SEPARATOR = "\x1f"  # unit separator: cannot occur in a stock code
_SNAPSHOT_FILE_MODE = 0o600  # cache holds the user's watchlist; keep it private


def _detect_parquet_engine() -> bool:
    """Is a parquet engine importable? Probed once, at import."""
    for module in ("pyarrow", "fastparquet"):
        try:
            __import__(module)
            return True
        except ImportError:
            continue
    return False


# This is normally True EVERYWHERE, including Streamlit Cloud: streamlit itself
# hard-requires pyarrow across our whole pinned range (1.36.0 -> "pyarrow>=7.0",
# 1.60.0 -> "pyarrow<25,>=7.0", no marker), so the engine is always installed.
# The guard exists only so an exotic environment without an engine degrades to
# "cache inactive" instead of crashing — it is NOT a mechanism for disabling the
# cache on Cloud, and the cache must therefore be self-bounding (see _prune).
#
# Post-mortem of the v3.2.0 outage, corrected in v3.2.2: the break was NOT caused
# by declaring pyarrow. It was caused by declaring it with an upper bound —
# "pyarrow>=15,<22" pinned 21.0.0, which ships no cp314 wheel, so pip fell back
# to a source build that needs cmake (absent on Cloud). Without our line, pip
# resolves streamlit's own ">=7.0" to a release that does have cp314 wheels.
# Lesson: never cap a native dependency below its newest wheel-bearing release.
_PARQUET_AVAILABLE = _detect_parquet_engine()


def _writer_stamp() -> str:
    """Unique per-writer suffix so concurrent savers never share a temp file."""
    return f"{os.getpid()}.{uuid.uuid4().hex}"


def _prune(cache_dir: Path, now: datetime) -> None:
    """Delete snapshots that can never be served again, plus stale temp files.

    The cache is live on every platform (see _PARQUET_AVAILABLE) and its key
    includes the date window, which moves every day for the rolling default
    range — so without this the directory would grow without bound forever.
    Anything older than the longest TTL is unreachable by load_snapshot, so
    deleting it loses nothing. Best-effort: never raises into the caller.
    """
    cutoff = now.timestamp() - HISTORICAL_MAX_AGE_DAYS * 86400
    for path in cache_dir.glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue  # another process may have removed it already


def default_cache_dir() -> Path:
    """Per-user cache directory.

    ``%LOCALAPPDATA%`` on Windows, ``~/Library/Caches`` on macOS, otherwise
    ``$XDG_CACHE_HOME`` or ``~/.cache``. Env overrides are only honoured when
    ABSOLUTE — an empty or relative value would otherwise put the cache in
    whatever the current working directory happens to be.
    """
    home = Path.home()
    if os.name == "nt":
        base = _absolute_env_dir("LOCALAPPDATA", home / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = home / "Library" / "Caches"
    else:
        base = _absolute_env_dir("XDG_CACHE_HOME", home / ".cache")
    return base / "BullishThreeConditionStockScreener" / "price_snapshots"


def _absolute_env_dir(name: str, fallback: Path) -> Path:
    raw = (os.environ.get(name) or "").strip()
    candidate = Path(raw) if raw else None
    return candidate if candidate is not None and candidate.is_absolute() else fallback


def _snapshot_key(codes, start_date, end_date) -> str:
    # Sorted + deduped: the downloaded frame is order-independent and each symbol
    # appears once, so [A, A, B] and [B, A] must map to the same snapshot. The
    # separator is a control character that cannot appear in a stock code, so no
    # code content can forge a different code set's key.
    joined = _KEY_FIELD_SEPARATOR.join(sorted({str(code) for code in codes}))
    payload = _KEY_FIELD_SEPARATOR.join((joined, str(start_date), str(end_date)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _paths(cache_dir: Path, key: str) -> tuple[Path, Path]:
    return cache_dir / f"{key}.parquet", cache_dir / f"{key}.json"


def _as_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return pd.to_datetime(value).date()


def _as_utc(value: datetime | None) -> datetime:
    """Normalize to aware UTC so ages are wall-clock-jump proof.

    Freshness was compared with naive local ``datetime.now()``: across a DST
    fall-back (or a machine timezone change) the computed age undercounts the
    real elapsed time by an hour, extending a TTL that exists to bound staleness.
    """
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.astimezone(timezone.utc)  # interpret naive input as local
    return value.astimezone(timezone.utc)


def _restrict(path: Path) -> None:
    """Make a cache file owner-only; the sidecar lists the user's watchlist."""
    try:
        os.chmod(path, _SNAPSHOT_FILE_MODE)
    except OSError:
        pass  # best-effort (e.g. filesystems without POSIX modes)


def save_snapshot(
    cache_dir: Path | str,
    codes,
    start_date,
    end_date,
    result: tuple[pd.DataFrame, list[str], list[str], list[str]],
    now: datetime | None = None,
) -> None:
    """Persist a whole download result. Fail-open: any error is logged and ignored."""
    if not _PARQUET_AVAILABLE:
        return  # no engine: cache stays inactive, callers just download live
    try:
        now = _as_utc(now)
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        daily_data, success_list, failed_list, download_errors = result
        parquet_path, meta_path = _paths(cache_dir, _snapshot_key(codes, start_date, end_date))
        # Atomic parquet write so a crash mid-write never leaves a torn file that
        # a later read would trust. The temp name MUST be unique per writer: a
        # shared "<key>.tmp" lets two processes saving the same key interleave
        # into one file, and because parquet is COLUMNAR the survivor is usually a
        # perfectly VALID file whose column chunks come from two different
        # downloads — read_parquet succeeds, nothing raises, fail-open never
        # triggers, and the mixed prices produce phantom breakout signals.
        tmp_path = parquet_path.with_name(f"{parquet_path.name}.{_writer_stamp()}.tmp")
        try:
            daily_data.to_parquet(tmp_path, index=False)
            os.replace(tmp_path, parquet_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)  # no orphan on failure
        meta_payload = json.dumps(
            {
                "captured_at": now.isoformat(),
                "end": str(end_date),
                # Whether the window still included the (evolving, possibly partial)
                # current day AT CAPTURE. This is fixed here, not recomputed at load,
                # so a snapshot taken intraday keeps its short TTL even after the
                # calendar rolls the end_date into the past.
                "end_was_current": _as_date(end_date) >= now.date(),
                "success_list": list(success_list),
                "failed_list": list(failed_list),
                "download_errors": list(download_errors),
            }
        )
        _restrict(parquet_path)
        # Atomic sidecar write too, so a crash can never pair a new parquet with a
        # torn/old metadata file. Same per-writer unique name as the parquet.
        meta_tmp = meta_path.with_name(f"{meta_path.name}.{_writer_stamp()}.tmp")
        try:
            meta_tmp.write_text(meta_payload, encoding="utf-8")
            os.replace(meta_tmp, meta_path)
        finally:
            Path(meta_tmp).unlink(missing_ok=True)
        _restrict(meta_path)
        _prune(cache_dir, now)
    except Exception as exc:  # noqa: BLE001 - cache is best-effort
        logger.warning("價格快照寫入失敗（略過快取）：%s", exc)


def load_snapshot(
    cache_dir: Path | str,
    codes,
    start_date,
    end_date,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, list[str], list[str], list[str]] | None:
    """Return a cached (daily_data, success, failed, errors) tuple, or None.

    Returns None on any miss, staleness, or error so the caller downloads live.
    """
    if not _PARQUET_AVAILABLE:
        return None  # no engine: always a miss, caller downloads live
    try:
        now = _as_utc(now)
        cache_dir = Path(cache_dir)
        parquet_path, meta_path = _paths(cache_dir, _snapshot_key(codes, start_date, end_date))
        if not parquet_path.exists() or not meta_path.exists():
            return None
        info = json.loads(meta_path.read_text(encoding="utf-8"))
        captured_at = _as_utc(datetime.fromisoformat(info["captured_at"]))
        age_seconds = (now - captured_at).total_seconds()
        if age_seconds < 0:
            return None  # clock moved backwards; distrust the snapshot
        # Classification is FIXED at capture: a window that was current then has
        # a possibly-partial last bar forever, so it must never be promoted to
        # the longer historical TTL once the calendar rolls the end date past.
        # A snapshot without the field predates it — assume the短 TTL rather
        # than recomputing at load, which is exactly the promotion this prevents.
        if info.get("end_was_current", True):
            if age_seconds > RECENT_END_TTL_SECONDS:
                return None
        elif age_seconds > HISTORICAL_MAX_AGE_DAYS * 86400:
            return None
        daily_data = pd.read_parquet(parquet_path)
        return daily_data, list(info["success_list"]), list(info["failed_list"]), list(info["download_errors"])
    except Exception as exc:  # noqa: BLE001 - cache is best-effort
        logger.warning("價格快照讀取失敗（改為重新下載）：%s", exc)
        return None
