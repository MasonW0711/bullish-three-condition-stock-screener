"""Data loading and resampling utilities for the stock screener."""

from __future__ import annotations

import contextlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
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
    REQUEST_RETRIES,
    REQUEST_TIMEOUT,
    REQUIRED_OHLCV_COLUMNS,
    TAIWAN_COMMON_STOCK_CFICODE,
    TWSE_LISTED_ISIN_URL,
    TWSE_OTC_ISIN_URL,
    YFINANCE_BATCH_SIZE,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

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


def _request_once(url: str, headers: dict) -> requests.Response:
    """Single GET attempt with an automatic insecure retry on SSL failure."""
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        response.raise_for_status()
        return response
    except requests.exceptions.SSLError as exc:
        # 降級為不驗證憑證屬於安全性讓步（可能遭中間人竄改資料），
        # 必須留下紀錄，不可無聲發生。
        logger.warning("SSL 憑證驗證失敗，改以不驗證憑證方式重試：%s（%s）", url, exc)
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers, verify=False)
        response.raise_for_status()
        return response


def _get_with_ssl_fallback(url: str) -> requests.Response:
    """GET a URL with bounded retries for transient timeouts/connection errors.

    SSL certificate problems fall back to an insecure request (TWSE/TPEX still
    serve valid data). Timeouts and connection drops — common on the Streamlit
    Cloud runtime — are retried; other HTTP errors fail immediately.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    last_exc: Exception | None = None
    total_attempts = max(int(REQUEST_RETRIES), 0) + 1
    for attempt in range(total_attempts):
        try:
            return _request_once(url, headers)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            logger.warning("請求逾時或連線中斷（第 %d 次嘗試）：%s", attempt + 1, url)
            # 指數退避：立即重試對限流中的來源只會雪上加霜。
            if attempt + 1 < total_attempts:
                time.sleep(min(2 ** attempt, 8))
    raise last_exc if last_exc is not None else RuntimeError(f"請求失敗：{url}")


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

    # yfinance output shape can drift across versions/environments (MultiIndex
    # layout, renamed index, missing columns). Treat any structural surprise as
    # "no usable data for this symbol" instead of letting a KeyError abort the
    # whole batch.
    try:
        # yfinance may return MultiIndex columns in some environments. Extract
        # the requested symbol's columns BEFORE copying — this function is
        # called once per symbol on the same batch frame, and copying the
        # whole batch every time is O(batch²) memory churn.
        if isinstance(df.columns, pd.MultiIndex):
            ticker_level_values = df.columns.get_level_values(0)
            price_level_values = df.columns.get_level_values(-1)
            if stock_code in ticker_level_values:
                normalized = df.xs(stock_code, axis=1, level=0, drop_level=True).copy()
            elif stock_code in price_level_values:
                normalized = df.xs(stock_code, axis=1, level=-1, drop_level=True).copy()
            else:
                normalized = df.copy()
                normalized.columns = [
                    "_".join(str(part) for part in column if part)
                    for column in normalized.columns.to_flat_index()
                ]
        else:
            normalized = df.copy()

        normalized = normalized.reset_index()

        if "Datetime" in normalized.columns and "Date" not in normalized.columns:
            normalized = normalized.rename(columns={"Datetime": "Date"})
        if "Date" not in normalized.columns and len(normalized.columns) > 0:
            normalized = normalized.rename(columns={normalized.columns[0]: "Date"})

        required_price_columns = {"Open", "High", "Low", "Close", "Volume"}
        if not required_price_columns.issubset(normalized.columns):
            return pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS)

        normalized = normalized[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
        normalized["Date"] = pd.to_datetime(
            normalized["Date"], errors="coerce", utc=True
        ).dt.tz_localize(None)

        for column in ["Open", "High", "Low", "Close", "Volume"]:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

        normalized["StockCode"] = stock_code
        normalized = normalized.dropna(subset=["Date"]).drop_duplicates(subset=["Date"])
        normalized = normalized.dropna(subset=["Open", "High", "Low", "Close"], how="any")
        normalized = normalized[REQUIRED_OHLCV_COLUMNS].sort_values("Date").reset_index(drop=True)
        return normalized
    except Exception as exc:  # pragma: no cover - defensive against yfinance drift
        logger.warning("正規化 %s 的 yfinance 資料失敗：%s", stock_code, exc)
        return pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS)


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
                # Split/dividend-adjusted prices. With raw prices a split or large
                # ex-dividend gap makes Open << prev_close, which the attack logic
                # would read as a fabricated "big black attack" (and create a phantom
                # black line). Adjusted prices are continuous across corporate
                # actions, so the red/black-line logic only reacts to real moves.
                auto_adjust=True,
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
) -> tuple[pd.DataFrame, list[str], list[str], list[str]]:
    """Download daily OHLCV data.

    Returns combined data, the success list, the failure list, and a list of
    human-readable diagnostic notes for batch-level download errors (network /
    rate-limit failures). The notes let the UI distinguish "no data" from "the
    download itself failed" instead of silently swallowing the exception.
    """
    success_list: list[str] = []
    success_set: set[str] = set()
    failed_list: list[str] = []
    download_errors: list[str] = []
    all_frames: list[pd.DataFrame] = []
    deduped_codes = _dedupe_preserve_order(stock_codes)

    total_codes = len(deduped_codes)
    if total_codes == 0:
        return pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS), success_list, failed_list, download_errors

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
        except Exception as exc:
            raw_data = pd.DataFrame()
            note = f"第 {chunk_index} 批下載失敗（網路或來源異常）：{type(exc).__name__}: {exc}"
            logger.warning(note)
            download_errors.append(note)

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

            if resolved_symbol in success_set:
                continue
            all_frames.append(resolved_frame)
            success_list.append(resolved_symbol)
            success_set.add(resolved_symbol)

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
            except Exception as exc:
                fallback_data = pd.DataFrame()
                note = f"第 {chunk_index} 批備援下載失敗（網路或來源異常）：{type(exc).__name__}: {exc}"
                logger.warning(note)
                download_errors.append(note)

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
                if resolved_symbol in success_set:
                    continue
                all_frames.append(resolved_frame)
                success_list.append(resolved_symbol)
                success_set.add(resolved_symbol)

        if progress_callback is not None:
            completed = min(chunk_index * YFINANCE_BATCH_SIZE, total_codes)
            progress_callback(
                min(completed / total_codes, 1.0),
                f"已完成 {completed} / {total_codes} 檔股票資料下載。",
            )

    if not all_frames:
        return pd.DataFrame(columns=REQUIRED_OHLCV_COLUMNS), success_list, failed_list, download_errors

    all_data = pd.concat(all_frames, ignore_index=True)
    all_data = all_data.sort_values(["StockCode", "Date"]).reset_index(drop=True)
    return all_data, success_list, failed_list, download_errors


def _to_int(value) -> int | None:
    """Parse an institutional-flow number from public TWSE/TPEX tables.

    Dash / empty placeholders mean "no value reported" and map to 0; anything
    else that fails to parse returns ``None`` so callers can surface the data
    anomaly instead of silently treating it as a zero net position (which
    would silently break consecutive-day streaks).
    """
    text = str(value).strip().replace(",", "").replace("+", "")
    if text in {"", "nan", "NaN", "None", "--", "-", "—"}:
        return 0
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


_INVESTOR_COLUMNS = ["Date", "BaseCode", "foreign_net", "trust_net"]


def _empty_investor_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_INVESTOR_COLUMNS)


def _locate_investor_net_columns(fields: list[str]) -> tuple[int, int]:
    """Locate the foreign / trust net buy-sell columns by field name.

    Raises ValueError when the expected columns cannot be identified, so a
    source-side schema change surfaces as a fetch failure (counted and shown
    in the UI) instead of silently reading the wrong column.
    """

    def _find(predicate) -> int | None:
        return next((index for index, field in enumerate(fields) if predicate(field)), None)

    def _is_foreign_net(field: str) -> bool:
        # The dealer-only column (外資自營商買賣超) must not match.
        return "買賣超" in field and ("外資" in field or "外陸資" in field) and "自營商買賣超" not in field

    # Prefer the "excluding foreign dealer" variant when both are present.
    foreign_index = _find(lambda field: _is_foreign_net(field) and "不含" in field)
    if foreign_index is None:
        foreign_index = _find(_is_foreign_net)
    trust_index = _find(lambda field: "投信" in field and "買賣超" in field)

    if foreign_index is None or trust_index is None:
        raise ValueError(f"法人資料欄位格式改變，無法定位外資／投信買賣超欄位：{fields}")
    return foreign_index, trust_index


def _fetch_twse_investor_flow(trade_date: pd.Timestamp) -> pd.DataFrame:
    # Network/decode errors are allowed to propagate so the caller can tell a
    # transient failure (retry-worthy, worth surfacing) apart from a genuine
    # "no data for this day" result. Structural surprises return empty.
    url = (
        "https://www.twse.com.tw/rwd/zh/fund/T86"
        f"?date={trade_date.strftime('%Y%m%d')}&selectType=ALLBUT0999&response=json"
    )
    response = _get_with_ssl_fallback(url)
    payload = response.json()

    if payload.get("stat") != "OK":
        return _empty_investor_frame()

    rows = payload.get("data") or []
    if not rows:
        return _empty_investor_frame()

    frame = pd.DataFrame(rows)
    if frame.shape[1] < 11:
        return _empty_investor_frame()

    # Locate net buy/sell columns by field name; positional indexes are only a
    # fallback when the payload carries no field metadata.
    fields = [str(field).strip() for field in (payload.get("fields") or [])]
    if fields:
        foreign_index, trust_index = _locate_investor_net_columns(fields)
        if frame.shape[1] <= max(foreign_index, trust_index):
            raise ValueError(
                f"TWSE T86 資料欄數（{frame.shape[1]}）與欄位定義（{len(fields)}）不符。"
            )
    else:
        foreign_index, trust_index = 4, 10

    code_series = frame.iloc[:, 0].astype(str).str.strip()
    if not code_series.str.fullmatch(r"\d{4}").any():
        return _empty_investor_frame()
    result = pd.DataFrame(
        {
            "Date": trade_date.normalize(),
            "BaseCode": code_series,
            "foreign_net": frame.iloc[:, foreign_index].map(_to_int),
            "trust_net": frame.iloc[:, trust_index].map(_to_int),
        }
    )
    return result[result["BaseCode"].str.fullmatch(r"\d{4}", na=False)].reset_index(drop=True)


# TPEX daily institutional layout (verified against the live API):
# 0 code, 1 name, 2-4 foreign excl dealer, 5-7 foreign dealer,
# 8-10 foreign incl dealer, 11-13 trust, 14-22 dealer groups,
# 23 three-institution total.
_TPEX_FOREIGN_NET_INDEX = 4
_TPEX_TRUST_NET_INDEX = 13
_TPEX_EXPECTED_FIELD_COUNT = 24


def _validate_tpex_net_columns(fields: list[str], frame: pd.DataFrame) -> tuple[int, int]:
    """Validate the TPEX generic-name layout and return (foreign, trust) indexes.

    TPEX repeats generic 買進/賣出/買賣超 field names without institution
    prefixes, so the column meaning is validated structurally instead:
    expected field count, the three-institution total as the last column, and
    net == buy - sell arithmetic on each used net column (an inserted or
    reordered column breaks at least one of these). Raises ValueError so a
    schema change surfaces as a counted fetch failure instead of reading the
    wrong column.
    """
    if any("投信" in field for field in fields):
        # If TPEX ever ships institution-prefixed names, prefer locating by name.
        return _locate_investor_net_columns(fields)

    if len(fields) != _TPEX_EXPECTED_FIELD_COUNT or frame.shape[1] != len(fields):
        raise ValueError(
            f"TPEX 法人資料欄位數改變（欄位定義 {len(fields)}、資料 {frame.shape[1]} 欄，"
            f"預期 {_TPEX_EXPECTED_FIELD_COUNT} 欄），請確認來源格式。"
        )
    if "三大法人買賣超" not in fields[-1]:
        raise ValueError(f"TPEX 法人資料最後一欄不是三大法人合計：{fields[-1]}")

    for net_index in (_TPEX_FOREIGN_NET_INDEX, _TPEX_TRUST_NET_INDEX):
        if fields[net_index] != "買賣超股數":
            raise ValueError(f"TPEX 法人資料第 {net_index} 欄不是買賣超股數：{fields[net_index]}")
        buy = pd.to_numeric(frame.iloc[:, net_index - 2].map(_to_int), errors="coerce")
        sell = pd.to_numeric(frame.iloc[:, net_index - 1].map(_to_int), errors="coerce")
        net = pd.to_numeric(frame.iloc[:, net_index].map(_to_int), errors="coerce")
        # Tolerate isolated placeholder rows, but a systematic mismatch means
        # the columns no longer line up.
        if len(frame) > 0 and (buy - sell).eq(net).mean() < 0.99:
            raise ValueError(f"TPEX 法人資料第 {net_index} 欄不符合買賣超＝買進－賣出，欄位可能位移。")
    return _TPEX_FOREIGN_NET_INDEX, _TPEX_TRUST_NET_INDEX


def _fetch_tpex_investor_flow(trade_date: pd.Timestamp) -> pd.DataFrame:
    # 新版 TPEX OpenAPI 端點（舊版 /web/stock/...php 已是相容轉址，隨時可能下線）。
    url = (
        "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
        f"?type=Daily&sect=EW&date={trade_date.strftime('%Y/%m/%d')}&response=json"
    )
    response = _get_with_ssl_fallback(url)
    payload = response.json()

    tables = payload.get("tables") or []
    if not tables or not tables[0].get("data"):
        return _empty_investor_frame()

    frame = pd.DataFrame(tables[0]["data"])
    if frame.shape[1] < 14:
        return _empty_investor_frame()

    fields = [str(field).strip() for field in (tables[0].get("fields") or [])]
    if fields:
        foreign_index, trust_index = _validate_tpex_net_columns(fields, frame)
    else:
        foreign_index, trust_index = _TPEX_FOREIGN_NET_INDEX, _TPEX_TRUST_NET_INDEX

    code_series = frame.iloc[:, 0].astype(str).str.strip()
    if not code_series.str.fullmatch(r"\d{4}").any():
        return _empty_investor_frame()
    result = pd.DataFrame(
        {
            "Date": trade_date.normalize(),
            "BaseCode": code_series,
            "foreign_net": frame.iloc[:, foreign_index].map(_to_int),
            "trust_net": frame.iloc[:, trust_index].map(_to_int),
        }
    )
    return result[result["BaseCode"].str.fullmatch(r"\d{4}", na=False)].reset_index(drop=True)


def download_investor_flow_data(
    stock_codes: list[str] | None,
    end_date,
    lookback_days: int = INVESTOR_LOOKBACK_DAYS,
) -> pd.DataFrame:
    """Download recent daily institutional net buy/sell data for Taiwan stocks.

    The source data is whole-market per day; ``stock_codes=None`` keeps every
    stock (callers can cache the market-wide result and filter afterwards).
    The returned data is daily, regardless of the selected K-bar timeframe.
    It is later mapped to the latest selected K-bar by bar end date.
    """
    base_codes: set[str] | None = None
    if stock_codes is not None:
        base_codes = {
            str(code).split(".")[0].strip()
            for code in stock_codes
            if str(code).split(".")[0].strip()
        }
        if not base_codes:
            return _empty_investor_frame()

    end_ts = pd.Timestamp(end_date).normalize()
    start_ts = end_ts - pd.Timedelta(days=max(int(lookback_days), 5))

    fetch_tasks = [
        (trade_date, source_name, fetch)
        for trade_date in pd.bdate_range(start_ts, end_ts)
        for source_name, fetch in (
            ("TWSE", _fetch_twse_investor_flow),
            ("TPEX", _fetch_tpex_investor_flow),
        )
    ]

    def _fetch_one(task) -> tuple[pd.DataFrame | None, Exception | None]:
        trade_date, source_name, fetch = task
        try:
            return fetch(trade_date), None
        except Exception as exc:
            # Transient network/source failure for this day — isolate it,
            # keep going, and count it so the UI can report partial coverage
            # instead of silently treating filters as unmet.
            logger.warning("%s 法人資料抓取失敗（%s）：%s", source_name, trade_date.date(), exc)
            return None, exc

    # 每個交易日 × 兩個來源約 40 個請求；小規模並行可大幅縮短雲端等待時間，
    # 並行數刻意壓低以免觸發來源限流。
    with ThreadPoolExecutor(max_workers=4) as pool:
        fetch_results = list(pool.map(_fetch_one, fetch_tasks))

    fetch_attempts = len(fetch_tasks)
    fetch_failures = sum(1 for _, exc in fetch_results if exc is not None)
    # A failure is per-trade-date and market-wide (it affects every stock's streak
    # on that date), so the actionable detail is WHICH dates are unreliable, not
    # which stocks. Surface a bounded list for the UI / Excel diagnostics.
    failed_dates = sorted(
        {
            task[0].date().isoformat()
            for task, (_, exc) in zip(fetch_tasks, fetch_results)
            if exc is not None
        }
    )
    frames: list[pd.DataFrame] = [
        frame for frame, exc in fetch_results if exc is None and frame is not None and not frame.empty
    ]

    def _stamp(frame: pd.DataFrame) -> pd.DataFrame:
        frame.attrs["fetch_attempts"] = fetch_attempts
        frame.attrs["fetch_failures"] = fetch_failures
        frame.attrs["fetch_failure_dates"] = failed_dates
        return frame

    if not frames:
        return _stamp(_empty_investor_frame())

    output = pd.concat(frames, ignore_index=True)
    if base_codes is not None:
        output = output[output["BaseCode"].isin(base_codes)].copy()
    output = output.drop_duplicates(subset=["Date", "BaseCode"]).sort_values(["BaseCode", "Date"])
    output = output.reset_index(drop=True)
    return _stamp(output)


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
