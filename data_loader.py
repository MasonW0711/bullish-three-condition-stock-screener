"""Data loading and resampling utilities for the stock screener."""

from __future__ import annotations

import re
from io import StringIO
from typing import Iterable

import pandas as pd
import requests
import yfinance as yf

from config import (
    REQUIRED_OHLCV_COLUMNS,
    TAIWAN_COMMON_STOCK_CFICODE,
    TWSE_LISTED_ISIN_URL,
    TWSE_OTC_ISIN_URL,
    YFINANCE_BATCH_SIZE,
)


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _normalize_token(token: str) -> str:
    return token.strip().upper()


def _chunk_list(items: list[str], chunk_size: int) -> list[list[str]]:
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def normalize_symbol(stock_code: str) -> list[str]:
    """Return one or more symbol candidates for a user-provided code."""
    normalized = _normalize_token(stock_code)
    if not normalized:
        return []
    if normalized.endswith(".TW") or normalized.endswith(".TWO"):
        return [normalized]
    if re.fullmatch(r"\d{4}", normalized):
        return [f"{normalized}.TW", f"{normalized}.TWO"]
    return [normalized]


def parse_stock_list(text: str) -> list[str]:
    """Parse textarea input into a deduplicated list of stock codes."""
    if not text:
        return []

    tokens: list[str] = []
    for line in text.splitlines():
        parts = [part for part in line.replace(",", "\n").splitlines()]
        tokens.extend(parts)

    cleaned = [_normalize_token(token) for token in tokens if _normalize_token(token)]
    return _dedupe_preserve_order(cleaned)


def _fetch_isin_universe(url: str, suffix: str, market_label: str) -> pd.DataFrame:
    """Fetch and parse one Taiwan stock universe page from TWSE ISIN data."""
    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    # Try parsers in order: lxml (fastest), html5lib, then html.parser (stdlib).
    # This ensures the app works on Streamlit Cloud regardless of which optional
    # HTML-parsing packages are installed.
    html_content = StringIO(response.text)
    tables = None
    for parser in ("lxml", "html5lib", "html.parser"):
        try:
            html_content.seek(0)
            tables = pd.read_html(html_content, flavor=parser)
            break
        except Exception:
            continue
    if not tables:
        raise ValueError("公開股票清單來源未返回任何表格。")

    raw_df = tables[0].copy()
    if raw_df.shape[1] < 6:
        raise ValueError("公開股票清單欄位格式與預期不符。")

    raw_df.columns = ["RawCodeName", "ISIN", "ListDate", "Market", "Industry", "CFICode", "Remark"]
    parsed = raw_df["RawCodeName"].astype(str).str.extract(r"^(?P<BaseCode>\d{4})[　\s]+(?P<StockName>.+)$")
    universe = raw_df.join(parsed)
    universe["CFICode"] = universe["CFICode"].astype(str).str.strip()

    universe = universe[
        universe["BaseCode"].notna() & (universe["CFICode"] == TAIWAN_COMMON_STOCK_CFICODE)
    ].copy()
    universe["StockCode"] = universe["BaseCode"] + suffix
    universe["MarketLabel"] = market_label
    universe["Industry"] = universe["Industry"].fillna("未分類")

    return universe[["StockCode", "BaseCode", "StockName", "MarketLabel", "Industry"]].drop_duplicates(
        subset=["StockCode"]
    )


def load_taiwan_stock_universe() -> pd.DataFrame:
    """Load all Taiwan listed and OTC common-stock symbols from public TWSE sources."""
    listed_df = _fetch_isin_universe(TWSE_LISTED_ISIN_URL, ".TW", "上市")
    otc_df = _fetch_isin_universe(TWSE_OTC_ISIN_URL, ".TWO", "上櫃")
    universe_df = pd.concat([listed_df, otc_df], ignore_index=True)
    universe_df = universe_df.sort_values(["MarketLabel", "BaseCode"]).reset_index(drop=True)
    return universe_df


def load_stock_list_from_upload(uploaded_file) -> list[str]:
    """Load and validate a CSV upload containing a StockCode column."""
    if uploaded_file is None:
        return []

    upload_df = pd.read_csv(uploaded_file)
    if "StockCode" not in upload_df.columns:
        raise ValueError("上傳的 CSV 必須包含 'StockCode' 欄位。")

    codes = [
        _normalize_token(str(value))
        for value in upload_df["StockCode"].dropna().tolist()
        if _normalize_token(str(value))
    ]
    return _dedupe_preserve_order(codes)


def normalize_yfinance_data(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    """Convert a raw yfinance DataFrame into the project's long-format schema."""
    if df is None or df.empty:
        return pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS)

    normalized = df.copy()

    # yfinance may return MultiIndex columns in some environments. For a
    # single-symbol download we extract the requested symbol level when present.
    if isinstance(normalized.columns, pd.MultiIndex):
        ticker_level_values = normalized.columns.get_level_values(0)
        price_level_values = normalized.columns.get_level_values(-1)
        if stock_code in ticker_level_values:
            normalized = normalized.xs(stock_code, axis=1, level=0, drop_level=True)
        elif stock_code in price_level_values:
            normalized = normalized.xs(stock_code, axis=1, level=-1, drop_level=True)
        else:
            normalized.columns = [
                "_".join(str(part) for part in column if part)
                for column in normalized.columns.to_flat_index()
            ]

    normalized = normalized.reset_index()

    if "Datetime" in normalized.columns and "Date" not in normalized.columns:
        normalized = normalized.rename(columns={"Datetime": "Date"})
    if "Date" not in normalized.columns and len(normalized.columns) > 0:
        normalized = normalized.rename(columns={normalized.columns[0]: "Date"})

    rename_map = {
        "Open": "Open",
        "High": "High",
        "Low": "Low",
        "Close": "Close",
        "Volume": "Volume",
    }
    required_price_columns = set(rename_map.keys())
    if not required_price_columns.issubset(normalized.columns):
        return pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS)

    normalized = normalized[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
    normalized["Date"] = pd.to_datetime(normalized["Date"], errors="coerce", utc=True).dt.tz_localize(
        None
    )

    for column in ["Open", "High", "Low", "Close", "Volume"]:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    normalized["StockCode"] = stock_code
    normalized = normalized.dropna(subset=["Date"]).drop_duplicates(subset=["Date"])
    normalized = normalized.dropna(subset=["Open", "High", "Low", "Close"], how="all")
    normalized = normalized[REQUIRED_OHLCV_COLUMNS].sort_values("Date").reset_index(drop=True)
    return normalized


def _download_candidate(symbols: str | list[str], start_date, end_date) -> pd.DataFrame:
    return yf.download(
        symbols,
        start=start_date,
        end=end_date,
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="ticker",
        threads=True,
    )


def download_stock_data(
    stock_codes: list[str],
    start_date,
    end_date,
    progress_callback=None,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Download daily OHLCV data and return combined data plus success/failure lists."""
    success_list: list[str] = []
    failed_list: list[str] = []
    all_frames: list[pd.DataFrame] = []
    deduped_codes = _dedupe_preserve_order(stock_codes)

    total_codes = len(deduped_codes)
    if total_codes == 0:
        return pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS), success_list, failed_list

    for chunk_index, chunk in enumerate(_chunk_list(deduped_codes, YFINANCE_BATCH_SIZE), start=1):
        if progress_callback is not None:
            completed = (chunk_index - 1) * YFINANCE_BATCH_SIZE
            progress_callback(
                min(completed / total_codes, 1.0),
                f"正在下載第 {chunk_index} 批股票資料（共 {len(chunk)} 檔）...",
            )

        try:
            raw_data = _download_candidate(chunk if len(chunk) > 1 else chunk[0], start_date, end_date)
        except Exception:
            failed_list.extend(chunk)
            continue

        for raw_code in chunk:
            resolved_frame = pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS)
            resolved_symbol = None

            for candidate in normalize_symbol(raw_code):
                normalized = normalize_yfinance_data(raw_data, stock_code=candidate)
                if not normalized.empty:
                    resolved_frame = normalized
                    resolved_symbol = candidate
                    break

            if resolved_symbol is None:
                failed_list.append(raw_code)
                continue

            all_frames.append(resolved_frame)
            success_list.append(resolved_symbol)

        if progress_callback is not None:
            completed = min(chunk_index * YFINANCE_BATCH_SIZE, total_codes)
            progress_callback(
                min(completed / total_codes, 1.0),
                f"已完成 {completed} / {total_codes} 檔股票資料下載。",
            )

    if not all_frames:
        return pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS), success_list, failed_list

    all_data = pd.concat(all_frames, ignore_index=True)
    all_data = all_data.sort_values(["StockCode", "Date"]).reset_index(drop=True)
    return all_data, success_list, failed_list


def _resample_single_stock(stock_df: pd.DataFrame, rule: str) -> pd.DataFrame:
    stock_df = stock_df.sort_values("Date").copy()
    stock_df["TradeDate"] = stock_df["Date"]

    resampled = (
        stock_df.set_index("Date")
        .resample(rule, label="right", closed="right")
        .agg(
            {
                "TradeDate": "max",
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        )
        .reset_index(drop=True)
    )

    resampled = resampled.dropna(subset=["Open", "High", "Low", "Close"], how="all")
    if resampled.empty:
        return pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS)

    resampled = resampled.rename(columns={"TradeDate": "Date"})
    resampled["StockCode"] = stock_df["StockCode"].iloc[0]
    resampled = resampled[REQUIRED_OHLCV_COLUMNS].copy()
    resampled["Date"] = pd.to_datetime(resampled["Date"], errors="coerce")
    return resampled.dropna(subset=["Date"]).reset_index(drop=True)


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """
    Convert daily OHLCV data into the selected timeframe:
    - D: Daily K
    - W: Weekly K
    - M: Monthly K

    Each stock is resampled separately, and the result keeps the actual last
    trading day inside each resampled period as the Date column.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=[*REQUIRED_OHLCV_COLUMNS, "Timeframe"])

    required = set(REQUIRED_OHLCV_COLUMNS)
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Input data is missing required columns: {sorted(missing)}")

    prepared = df[REQUIRED_OHLCV_COLUMNS].copy()
    prepared["Date"] = pd.to_datetime(prepared["Date"], errors="coerce")
    prepared = prepared.dropna(subset=["Date"]).sort_values(["StockCode", "Date"]).reset_index(drop=True)

    timeframe = timeframe.upper()
    if timeframe not in {"D", "W", "M"}:
        raise ValueError("timeframe must be one of: D, W, M")

    if timeframe == "D":
        daily = prepared.copy()
        daily["Timeframe"] = "D"
        return daily.reset_index(drop=True)

    resample_rule = {"W": "W-FRI", "M": "ME"}[timeframe]
    frames: list[pd.DataFrame] = []
    for _, stock_df in prepared.groupby("StockCode", sort=False):
        stock_resampled = _resample_single_stock(stock_df, resample_rule)
        if not stock_resampled.empty:
            frames.append(stock_resampled)

    if not frames:
        return pd.DataFrame(columns=[*REQUIRED_OHLCV_COLUMNS, "Timeframe"])

    output = pd.concat(frames, ignore_index=True)
    output["Timeframe"] = timeframe
    output = output.sort_values(["StockCode", "Date"]).reset_index(drop=True)
    return output
