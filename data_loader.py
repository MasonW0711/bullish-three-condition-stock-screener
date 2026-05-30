"""Data loading and resampling utilities for the stock screener."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
import re
from io import StringIO
from typing import Iterable

import pandas as pd
import requests
import urllib3
import yfinance as yf

from config import (
    INVESTOR_LOOKBACK_DAYS,
    REQUIRED_OHLCV_COLUMNS,
    TAIWAN_COMMON_STOCK_CFICODE,
    TWSE_LISTED_ISIN_URL,
    TWSE_OTC_ISIN_URL,
    YFINANCE_BATCH_SIZE,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_YFINANCE_LOGGER_NAMES = ("yfinance", "peewee")


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


def _get_with_ssl_fallback(url: str) -> requests.Response:
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, timeout=30, headers=headers)
        response.raise_for_status()
        return response
    except requests.exceptions.SSLError:
        response = requests.get(url, timeout=30, headers=headers, verify=False)
        response.raise_for_status()
        return response


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
        tokens.extend(line.replace(",", "\n").splitlines())

    cleaned = [_normalize_token(token) for token in tokens if _normalize_token(token)]
    return _dedupe_preserve_order(cleaned)


def _select_isin_table(tables: list[pd.DataFrame]) -> pd.DataFrame:
    """Select the actual TWSE ISIN stock table from read_html() results."""
    for table in tables:
        if table.shape[1] != 7:
            continue
        first_col = table.iloc[:, 0].astype(str).str.strip()
        parsed = first_col.str.extract(r"^(?P<BaseCode>\d{4})[　\s]+(?P<StockName>.+)$")
        if parsed["BaseCode"].notna().any():
            return table.copy()
    raise ValueError("公開股票清單表格格式異常：找不到 7 欄且含股票代號名稱的資料表。")


def _fetch_isin_universe(url: str, suffix: str, market_label: str) -> pd.DataFrame:
    """Fetch and parse one Taiwan stock universe page from TWSE ISIN data."""
    try:
        response = _get_with_ssl_fallback(url)
    except requests.RequestException as exc:
        raise ValueError(f"{market_label}股票清單下載失敗：{exc}") from exc

    # Use only valid pandas read_html parser strategies.
    # "html.parser" is not a supported pandas flavor, so if lxml/html5lib are
    # unavailable it causes a false "no tables returned" failure.
    html_content = StringIO(response.text)
    tables = None
    for parser in ("lxml", None):
        try:
            html_content.seek(0)
            if parser is None:
                tables = pd.read_html(html_content)
            else:
                tables = pd.read_html(html_content, flavor=parser)
            break
        except Exception:
            continue
    if not tables:
        raise ValueError("公開股票清單來源未返回任何表格。")

    raw_df = _select_isin_table(tables)
    if raw_df.shape[1] != 7:
        raise ValueError(f"公開股票清單欄位格式與預期不符：預期 7 欄，實際 {raw_df.shape[1]} 欄。")

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
    """Load and validate a CSV/XLSX upload containing a stock-code column."""
    if uploaded_file is None:
        return []

    file_name = getattr(uploaded_file, "name", "uploaded_file")
    suffix = Path(file_name).suffix.lower()
    reader = pd.read_excel if suffix in {".xlsx", ".xls"} else pd.read_csv

    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)

    try:
        upload_df = reader(uploaded_file)
    except Exception as exc:
        raise ValueError(f"無法讀取上傳檔案：{exc}") from exc

    candidate_columns = ("StockCode", "stock_code", "stockcode", "股票代號", "代號")
    stock_code_column = next((column for column in candidate_columns if column in upload_df.columns), None)
    if stock_code_column is None:
        raise ValueError("上傳檔案必須包含股票代號欄位，例如 'StockCode'。")

    codes = [
        _normalize_token(str(value))
        for value in upload_df[stock_code_column].dropna().tolist()
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

    required_price_columns = {"Open", "High", "Low", "Close", "Volume"}
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
    normalized = normalized.dropna(subset=["Open", "High", "Low", "Close"], how="any")
    normalized = normalized[REQUIRED_OHLCV_COLUMNS].sort_values("Date").reset_index(drop=True)
    return normalized


def _download_candidate(symbols: str | list[str], start_date, end_date) -> pd.DataFrame:
    # yfinance treats `end` as exclusive; users expect the selected end date to
    # be included when that trading day has data.
    end_exclusive = pd.Timestamp(end_date).normalize() + pd.Timedelta(days=1)
    loggers = [logging.getLogger(name) for name in _YFINANCE_LOGGER_NAMES]
    previous_levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
            return yf.download(
                symbols,
                start=start_date,
                end=end_exclusive.date(),
                interval="1d",
                auto_adjust=False,
                progress=False,
                group_by="ticker",
                threads=True,
                multi_level_index=True,
            )
    finally:
        for logger, level in zip(loggers, previous_levels):
            logger.setLevel(level)


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

        candidate_map = {raw_code: normalize_symbol(raw_code) for raw_code in chunk}
        primary_symbols = _dedupe_preserve_order(
            candidates[0]
            for candidates in candidate_map.values()
            if candidates
        )
        if not primary_symbols:
            failed_list.extend(chunk)
            continue

        try:
            raw_data = _download_candidate(
                primary_symbols if len(primary_symbols) > 1 else primary_symbols[0],
                start_date,
                end_date,
            )
        except Exception:
            raw_data = pd.DataFrame()

        unresolved_codes: list[str] = []

        for raw_code in chunk:
            candidates = candidate_map.get(raw_code, [])
            resolved_frame = pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS)
            resolved_symbol = None

            primary_candidates = candidates[:1] if len(candidates) > 1 else candidates
            for candidate in primary_candidates:
                normalized = normalize_yfinance_data(raw_data, stock_code=candidate)
                if not normalized.empty:
                    resolved_frame = normalized
                    resolved_symbol = candidate
                    break

            if resolved_symbol is None:
                if len(candidates) > 1:
                    unresolved_codes.append(raw_code)
                    continue
                failed_list.append(raw_code)
                continue

            if resolved_symbol in success_list:
                continue
            all_frames.append(resolved_frame)
            success_list.append(resolved_symbol)

        if unresolved_codes:
            fallback_symbols = _dedupe_preserve_order(
                candidate
                for raw_code in unresolved_codes
                for candidate in candidate_map.get(raw_code, [])[1:]
            )
            try:
                fallback_data = _download_candidate(
                    fallback_symbols if len(fallback_symbols) > 1 else fallback_symbols[0],
                    start_date,
                    end_date,
                )
            except Exception:
                fallback_data = pd.DataFrame()

            for raw_code in unresolved_codes:
                resolved_frame = pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS)
                resolved_symbol = None
                for candidate in candidate_map.get(raw_code, [])[1:]:
                    normalized = normalize_yfinance_data(fallback_data, stock_code=candidate)
                    if not normalized.empty:
                        resolved_frame = normalized
                        resolved_symbol = candidate
                        break

                if resolved_symbol is None:
                    failed_list.append(raw_code)
                    continue
                if resolved_symbol in success_list:
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


def _to_int(value) -> int:
    text = str(value).strip().replace(",", "").replace("+", "")
    if text in {"", "nan", "NaN", "None", "--", "-", "—"}:
        return 0
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _fetch_twse_investor_flow(trade_date: pd.Timestamp) -> pd.DataFrame:
    url = (
        "https://www.twse.com.tw/rwd/zh/fund/T86"
        f"?date={trade_date.strftime('%Y%m%d')}&selectType=ALLBUT0999&response=json"
    )
    try:
        response = _get_with_ssl_fallback(url)
        payload = response.json()
    except Exception:
        return pd.DataFrame(columns=["Date", "BaseCode", "foreign_net", "trust_net"])

    if payload.get("stat") != "OK":
        return pd.DataFrame(columns=["Date", "BaseCode", "foreign_net", "trust_net"])

    rows = payload.get("data") or []
    if not rows:
        return pd.DataFrame(columns=["Date", "BaseCode", "foreign_net", "trust_net"])

    frame = pd.DataFrame(rows)
    if frame.shape[1] < 11:
        return pd.DataFrame(columns=["Date", "BaseCode", "foreign_net", "trust_net"])
    code_series = frame.iloc[:, 0].astype(str).str.strip()
    if not code_series.str.fullmatch(r"\d{4}").any():
        return pd.DataFrame(columns=["Date", "BaseCode", "foreign_net", "trust_net"])
    result = pd.DataFrame(
        {
            "Date": trade_date.normalize(),
            "BaseCode": code_series,
            "foreign_net": frame.iloc[:, 4].map(_to_int),
            "trust_net": frame.iloc[:, 10].map(_to_int),
        }
    )
    return result[result["BaseCode"].str.fullmatch(r"\d{4}", na=False)].reset_index(drop=True)


def _fetch_tpex_investor_flow(trade_date: pd.Timestamp) -> pd.DataFrame:
    roc_date = f"{trade_date.year - 1911:03d}/{trade_date.month:02d}/{trade_date.day:02d}"
    url = (
        "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
        f"?l=zh-tw&d={roc_date}&o=json"
    )
    try:
        response = _get_with_ssl_fallback(url)
        payload = response.json()
    except Exception:
        return pd.DataFrame(columns=["Date", "BaseCode", "foreign_net", "trust_net"])

    tables = payload.get("tables") or []
    if not tables or not tables[0].get("data"):
        return pd.DataFrame(columns=["Date", "BaseCode", "foreign_net", "trust_net"])

    frame = pd.DataFrame(tables[0]["data"])
    if frame.shape[1] < 14:
        return pd.DataFrame(columns=["Date", "BaseCode", "foreign_net", "trust_net"])
    code_series = frame.iloc[:, 0].astype(str).str.strip()
    if not code_series.str.fullmatch(r"\d{4}").any():
        return pd.DataFrame(columns=["Date", "BaseCode", "foreign_net", "trust_net"])
    # OTC schema:
    # 0 code, 1 name, 2-4 foreign excl dealer, 5-7 foreign dealer,
    # 8-10 foreign incl dealer, 11-13 trust.
    result = pd.DataFrame(
        {
            "Date": trade_date.normalize(),
            "BaseCode": code_series,
            "foreign_net": frame.iloc[:, 4].map(_to_int),
            "trust_net": frame.iloc[:, 13].map(_to_int),
        }
    )
    return result[result["BaseCode"].str.fullmatch(r"\d{4}", na=False)].reset_index(drop=True)


def download_investor_flow_data(
    stock_codes: list[str],
    end_date,
    lookback_days: int = INVESTOR_LOOKBACK_DAYS,
) -> pd.DataFrame:
    """Download recent daily institutional net buy/sell data for Taiwan stocks.

    The returned data is daily, regardless of the selected K-bar timeframe.
    It is later mapped to the latest selected K-bar by bar end date.
    """
    base_codes = {
        str(code).split(".")[0].strip()
        for code in stock_codes
        if str(code).split(".")[0].strip()
    }
    if not base_codes:
        return pd.DataFrame(columns=["Date", "BaseCode", "foreign_net", "trust_net"])

    end_ts = pd.Timestamp(end_date).normalize()
    start_ts = end_ts - pd.Timedelta(days=max(int(lookback_days), 5))

    frames: list[pd.DataFrame] = []
    for trade_date in pd.bdate_range(start_ts, end_ts):
        twse_df = _fetch_twse_investor_flow(trade_date)
        if not twse_df.empty:
            frames.append(twse_df)
        tpex_df = _fetch_tpex_investor_flow(trade_date)
        if not tpex_df.empty:
            frames.append(tpex_df)

    if not frames:
        return pd.DataFrame(columns=["Date", "BaseCode", "foreign_net", "trust_net"])

    output = pd.concat(frames, ignore_index=True)
    output = output[output["BaseCode"].isin(base_codes)].copy()
    output = output.drop_duplicates(subset=["Date", "BaseCode"]).sort_values(["BaseCode", "Date"])
    return output.reset_index(drop=True)


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

    resample_rule = {"W": "W-FRI", "M": pd.offsets.MonthEnd()}[timeframe]
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
