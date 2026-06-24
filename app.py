"""Streamlit entrypoint for the breakout-and-retest-hold stock screener."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import config as app_config
from chart_engine import create_stock_chart
from display_utils import booleans_to_chinese, sanitize_for_spreadsheet
from data_loader import (
    download_investor_flow_data,
    download_stock_data,
    load_stock_list_from_upload,
    load_taiwan_stock_universe,
    parse_stock_list,
    resample_ohlcv,
)
from export_engine import create_excel_bytes
from signal_engine import (
    attach_investor_flow_flags,
    build_direction_signals,
    run_signal_pipeline,
)

APP_TITLE = app_config.APP_TITLE
APP_VERSION = app_config.APP_VERSION
APP_UPDATED = app_config.APP_UPDATED
APP_PURPOSE = app_config.APP_PURPOSE
AUTO_UNIVERSE_DESCRIPTION = app_config.AUTO_UNIVERSE_DESCRIPTION
DEFAULT_PARAMETERS = app_config.DEFAULT_PARAMETERS
DEFAULT_TEXT_STOCK_LIST = app_config.DEFAULT_TEXT_STOCK_LIST
DIRECTION_FILTER_OPTIONS = app_config.DIRECTION_FILTER_OPTIONS
DISPLAY_COLUMN_LABELS = app_config.DISPLAY_COLUMN_LABELS
LATEST_SUMMARY_COLUMNS = app_config.LATEST_SUMMARY_COLUMNS
SIGNAL_COLUMNS = app_config.SIGNAL_COLUMNS
TIMEFRAME_LABELS = app_config.TIMEFRAME_LABELS
TIMEFRAME_OPTIONS = app_config.TIMEFRAME_OPTIONS


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def _load_taiwan_stock_universe_cached() -> pd.DataFrame:
    return load_taiwan_stock_universe()


# 法人資料一律抓「全市場」再過濾，因此快取鍵只需日期與回看天數；
# 若把股票清單放進快取鍵，換一批清單就會重抓相同的全市場資料。
@st.cache_data(ttl=60 * 60, show_spinner=False)
def _download_investor_flow_data_cached(
    end_date: date,
    lookback_days: int,
) -> pd.DataFrame:
    return download_investor_flow_data(
        stock_codes=None,
        end_date=end_date,
        lookback_days=lookback_days,
    )


# 底線開頭的參數不會進入 st.cache_data 的快取鍵：首跑會以 callback 回報
# 下載進度，相同條件重跑則直接命中快取、不再重新下載。
@st.cache_data(ttl=60 * 30, show_spinner=False)
def _download_stock_data_cached(
    stock_codes: tuple[str, ...],
    start_date: date,
    end_date: date,
    _progress_callback=None,
) -> tuple[pd.DataFrame, list[str], list[str], list[str]]:
    return download_stock_data(
        stock_codes=list(stock_codes),
        start_date=start_date,
        end_date=end_date,
        progress_callback=_progress_callback,
    )


# 所有參數一律由呼叫端明確提供；預設值只存在於 config.DEFAULT_PARAMETERS，
# 避免兩處定義隨時間漂移。
def _build_params(
    start_date: date,
    end_date: date,
    analysis_timeframe: str,
    lookback_bars: int,
    min_volume: int,
    new_line_window: int,
    direction_filter: str,
    investor_consecutive_days: int,
    foreign_buy_streak: bool,
    trust_buy_streak: bool,
    foreign_sell_streak: bool,
    trust_sell_streak: bool,
) -> dict:
    return {
        "start_date": start_date,
        "end_date": end_date,
        "analysis_timeframe": analysis_timeframe,
        "lookback_bars": int(lookback_bars),
        "min_volume": int(min_volume),
        "new_line_window": max(int(new_line_window), 1),
        "direction_filter": direction_filter,
        "investor_consecutive_days": max(int(investor_consecutive_days), 1),
        "foreign_buy_streak": bool(foreign_buy_streak),
        "trust_buy_streak": bool(trust_buy_streak),
        "foreign_sell_streak": bool(foreign_sell_streak),
        "trust_sell_streak": bool(trust_sell_streak),
    }


def _selected_investor_columns(params: dict) -> list[str]:
    return [
        col
        for col, enabled in (
            ("foreign_buy_streak_ok", params.get("foreign_buy_streak", False)),
            ("trust_buy_streak_ok", params.get("trust_buy_streak", False)),
            ("foreign_sell_streak_ok", params.get("foreign_sell_streak", False)),
            ("trust_sell_streak_ok", params.get("trust_sell_streak", False)),
        )
        if enabled
    ]


# 同一根 K 棒可能同時命中兩條路徑；摘要每股只留一列時，
# 以「突破型」優先（P1 > P2、P3 > P4），避免取決於資料排列順序。
_SIGNAL_TYPE_PRIORITY = {
    "P1_BreakUp_Hold": 2,
    "P2_NewLine_Hold": 1,
    "P3_BreakDown_Reject": 2,
    "P4_NewLine_Reject": 1,
}


def _compute_latest_summary(signals_df: pd.DataFrame) -> pd.DataFrame:
    """Latest valid signal per stock for one direction's exploded frame (§4.2)."""
    if signals_df is None or signals_df.empty:
        return pd.DataFrame(columns=LATEST_SUMMARY_COLUMNS)

    ranked = signals_df.copy()
    ranked["_priority"] = ranked["signal_type"].map(_SIGNAL_TYPE_PRIORITY).fillna(0)
    latest = (
        ranked.sort_values(["StockCode", "Date", "_priority"])
        .groupby("StockCode", group_keys=False)
        .tail(1)
        .drop(columns=["_priority"])
        .copy()
    )
    summary = latest.rename(
        columns={
            "Date": "LatestSignalDate",
            "direction": "Direction",
            "signal_type": "SignalType",
            "retest_line_type": "RetestLineType",
            "retest_line_price": "RetestLinePrice",
            "Open": "LatestOpen",
            "High": "LatestHigh",
            "Low": "LatestLow",
            "Close": "LatestClose",
            "Volume": "LatestVolume",
        }
    )
    for col in LATEST_SUMMARY_COLUMNS:
        if col not in summary.columns:
            summary[col] = pd.NA
    return summary[LATEST_SUMMARY_COLUMNS].sort_values("LatestSignalDate", ascending=False).reset_index(drop=True)


def _empty_result(
    universe_df: pd.DataFrame,
    success_list=None,
    failed_list=None,
    messages=None,
    used_auto_universe: bool = False,
    download_errors=None,
) -> dict:
    empty = pd.DataFrame()
    empty_summary = _compute_latest_summary(empty)
    return {
        "all_data": empty,
        "long_signals": empty,
        "short_signals": empty,
        "latest_summary_long": empty_summary,
        "latest_summary_short": empty_summary,
        "success_list": success_list or [],
        "failed_list": failed_list or [],
        "download_errors": download_errors or [],
        "universe_df": universe_df,
        "messages": messages or [],
        "used_auto_universe": used_auto_universe,
    }


def _run_screening(params: dict, use_auto_universe: bool, manual_codes: list[str], progress_callback=None) -> dict:
    timeframe_code = TIMEFRAME_OPTIONS[params["analysis_timeframe"]]
    min_volume_shares = params["min_volume"] * 1000
    messages: list[dict[str, str]] = []

    universe_df = pd.DataFrame()
    if use_auto_universe:
        universe_df = _load_taiwan_stock_universe_cached()
        stock_codes = universe_df["StockCode"].dropna().astype(str).tolist()
    else:
        stock_codes = manual_codes
        try:
            manual_universe = _load_taiwan_stock_universe_cached()
        except Exception as exc:
            manual_universe = pd.DataFrame()
            messages.append({"level": "warning", "text": f"無法載入股票名稱對照表：{exc}"})
        if not manual_universe.empty:
            lookup_codes = {str(code).strip().upper() for code in stock_codes if str(code).strip()}
            lookup_base_codes = {code.split(".")[0] for code in lookup_codes}
            universe_df = manual_universe[
                manual_universe["StockCode"].astype(str).str.upper().isin(lookup_codes)
                | manual_universe["BaseCode"].astype(str).str.upper().isin(lookup_base_codes)
            ].copy()

    if not stock_codes:
        messages.append({"level": "warning", "text": "請先提供至少一個有效的股票代號。"})
        return _empty_result(universe_df, messages=messages, used_auto_universe=use_auto_universe)

    try:
        download_progress_callback = None
        if progress_callback is not None:
            progress_callback(0.03, "正在準備股票下載清單...")

            def _download_progress_callback(progress_value: float, message: str) -> None:
                progress_callback(0.03 + min(max(progress_value, 0.0), 1.0) * 0.70, message)

            download_progress_callback = _download_progress_callback

        daily_data, success_list, failed_list, download_errors = _download_stock_data_cached(
            stock_codes=tuple(stock_codes),
            start_date=params["start_date"],
            end_date=params["end_date"],
            _progress_callback=download_progress_callback,
        )
        # The cached call returns the stored objects; copy the mutable diagnostics
        # list before appending investor-fetch notes so we never mutate the cache.
        download_errors = list(download_errors)
    except Exception as exc:
        messages.append({"level": "error", "text": f"股票資料下載失敗：{exc}"})
        return _empty_result(universe_df, messages=messages, used_auto_universe=use_auto_universe)

    if download_errors:
        sample = "；".join(download_errors[:3])
        more = f"（另有 {len(download_errors) - 3} 筆未列出）" if len(download_errors) > 3 else ""
        messages.append({
            "level": "warning",
            "text": f"部分股票批次下載失敗，可能是網路逾時或來源限流：{sample}{more}",
        })

    if daily_data.empty:
        messages.append({"level": "warning", "text": "下載完成，但沒有取得任何可用股價資料。"})
        return _empty_result(
            universe_df,
            success_list=success_list,
            failed_list=failed_list,
            messages=messages,
            used_auto_universe=use_auto_universe,
            download_errors=download_errors,
        )

    if progress_callback is not None:
        progress_callback(0.78, "正在整理 K 棒週期與紅黑線訊號...")
    timeframe_data = resample_ohlcv(daily_data, timeframe_code)

    # 防呆：日期區間太短時（例如 30 天 × 月 K），K 棒數不足以計算回看條件，
    # 結果必然為空。明確告知使用者是「資料不夠」而不是「市場沒訊號」。
    if not timeframe_data.empty:
        median_bars = int(timeframe_data.groupby("StockCode").size().median())
        # 訊號管線需要 prev_close（少一根）加上回看範圍才有意義。
        required_bars = min(int(params["lookback_bars"]), 5) + 1
        if median_bars < required_bars:
            messages.append({
                "level": "warning",
                "text": (
                    f"目前日期區間在「{params['analysis_timeframe']}」下，每檔股票僅約 {median_bars} 根 K 棒，"
                    f"不足以判斷突破與回測訊號（建議至少 {required_bars} 根）。"
                    "若篩選結果為空，請拉長日期區間或改用較短的分析週期。"
                ),
            })

    pipeline_params = {**params, "min_volume": min_volume_shares}
    processed = run_signal_pipeline(timeframe_data, pipeline_params)

    investor_filters_enabled = bool(_selected_investor_columns(params))
    investor_flow_df = pd.DataFrame()
    investor_market_trading_days = None
    if investor_filters_enabled:
        if progress_callback is not None:
            progress_callback(0.88, "正在下載法人買賣超資料...")
        # Size the fetch window in TRADING days tied to BOTH the streak length N
        # and the lookback window, then convert to calendar days (~5/7) with a
        # holiday pad. The streak must be evaluable across the whole lookback
        # window, not only the most recent bar, or older qualifying signals get
        # silently dropped when N is large.
        needed_trading_days = int(params.get("investor_consecutive_days", 3)) + int(
            params.get("lookback_bars", 10)
        )
        investor_lookback_days = max(
            int(app_config.INVESTOR_LOOKBACK_DAYS),
            int(needed_trading_days * 1.6) + 14,
        )
        market_wide_flow_empty = True
        try:
            investor_flow_df = _download_investor_flow_data_cached(
                end_date=params["end_date"],
                lookback_days=investor_lookback_days,
            )
            market_wide_flow_empty = investor_flow_df.empty
            if not investor_flow_df.empty:
                # Capture the whole-market trading-day axis BEFORE narrowing the
                # frame to screened symbols, so the consecutive-day streak does
                # not bridge gaps in a small/single-stock screen (§3.7). Union in
                # the attempted-but-failed dates (a date where BOTH TWSE and TPEX
                # fetches errored has no rows, so it would otherwise drop off the
                # axis and be bridged); those dates were real attempts, so a NaN
                # gap there correctly breaks the streak. Holidays return empty
                # (not an error) and never enter fetch_failure_dates, so they are
                # not injected.
                observed_days = pd.DatetimeIndex(
                    pd.to_datetime(investor_flow_df["Date"], errors="coerce").dropna().unique()
                )
                failed_days = pd.DatetimeIndex(
                    pd.to_datetime(
                        pd.Index(investor_flow_df.attrs.get("fetch_failure_dates", [])),
                        errors="coerce",
                    ).dropna()
                )
                investor_market_trading_days = observed_days.union(failed_days)
                screened_base_codes = {
                    str(code).split(".")[0].strip() for code in success_list
                }
                filtered_flow = investor_flow_df[
                    investor_flow_df["BaseCode"].isin(screened_base_codes)
                ].reset_index(drop=True)
                filtered_flow.attrs = dict(investor_flow_df.attrs)
                investor_flow_df = filtered_flow
        except Exception as exc:
            messages.append({"level": "warning", "text": f"法人買賣超資料下載失敗，法人條件將視為未達成：{exc}"})
            investor_flow_df = pd.DataFrame()
        fetch_failures = int(investor_flow_df.attrs.get("fetch_failures", 0))
        fetch_attempts = int(investor_flow_df.attrs.get("fetch_attempts", 0))
        failed_dates = list(investor_flow_df.attrs.get("fetch_failure_dates", []))
        if investor_flow_df.empty:
            if market_wide_flow_empty:
                messages.append({"level": "warning", "text": "目前無法取得最新法人買賣超資料，法人條件已視為未達成。"})
            else:
                messages.append({"level": "info", "text": "已取得法人買賣超資料，但本次篩選的股票在此期間沒有對應的法人買賣超紀錄，法人條件視為未達成。"})
        elif fetch_failures > 0:
            date_sample = "、".join(failed_dates[:5])
            date_more = f" 等共 {len(failed_dates)} 日" if len(failed_dates) > 5 else ""
            date_detail = f"，受影響日期：{date_sample}{date_more}" if failed_dates else ""
            warning_text = (
                f"法人買賣超資料有 {fetch_failures}/{fetch_attempts} 次抓取失敗（網路或來源異常），"
                f"這些日期的法人連續買賣超條件可能不準確{date_detail}。"
            )
            messages.append({"level": "warning", "text": warning_text})
            # Also record it in the batch-level diagnostics so it appears in the
            # Excel「下載失敗清單」alongside the stock download errors.
            download_errors.append(warning_text)

    processed = attach_investor_flow_flags(
        processed,
        investor_flow_df,
        consecutive_days=params.get("investor_consecutive_days", 3),
        market_trading_days=investor_market_trading_days,
    )

    if not universe_df.empty and "StockName" in universe_df.columns:
        name_map = universe_df[["StockCode", "StockName"]].drop_duplicates("StockCode").set_index("StockCode")
        processed = processed.join(name_map, on="StockCode", how="left")
        processed["StockName"] = processed["StockName"].fillna(processed["StockCode"])
    else:
        processed["StockName"] = processed["StockCode"]

    direction_bundle = build_direction_signals(processed, params)
    long_signals = direction_bundle["long_signals"]
    short_signals = direction_bundle["short_signals"]
    latest_summary_long = _compute_latest_summary(long_signals)
    latest_summary_short = _compute_latest_summary(short_signals)
    if long_signals.empty and short_signals.empty:
        messages.append({"level": "info", "text": "本次篩選完成，但目前沒有符合條件的股票。"})
    if progress_callback is not None:
        progress_callback(1.0, "篩選完成。")

    return {
        "all_data": processed,
        "long_signals": long_signals,
        "short_signals": short_signals,
        "latest_summary_long": latest_summary_long,
        "latest_summary_short": latest_summary_short,
        "success_list": success_list,
        "failed_list": failed_list,
        "download_errors": download_errors,
        "universe_df": universe_df,
        "messages": messages,
        "used_auto_universe": use_auto_universe,
    }


def _prepare_display_frame(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    display_df = df.copy()
    for col in columns:
        if col not in display_df.columns:
            display_df[col] = pd.NA
    if "Timeframe" in display_df.columns:
        display_df["Timeframe"] = display_df["Timeframe"].map(TIMEFRAME_LABELS).fillna(display_df["Timeframe"])
    selected = booleans_to_chinese(display_df[columns])
    return selected.rename(columns=DISPLAY_COLUMN_LABELS)


def _render_direction_results(
    direction_label: str,
    key_prefix: str,
    signals_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    all_data: pd.DataFrame,
    saved_params: dict,
) -> None:
    """Render one direction's signal table, latest summary, and K-line chart."""
    st.markdown(f"#### {direction_label}符合訊號")
    if signals_df is None or signals_df.empty:
        st.info(f"目前沒有符合條件的{direction_label}訊號。")
    else:
        st.dataframe(_prepare_display_frame(signals_df, SIGNAL_COLUMNS), width="stretch")

    st.markdown(f"#### {direction_label}最新摘要（每股一列）")
    if summary_df is None or summary_df.empty:
        st.info("無最新摘要資料。")
    else:
        st.dataframe(_prepare_display_frame(summary_df, LATEST_SUMMARY_COLUMNS), width="stretch")

    st.markdown(f"#### {direction_label} K 線圖")
    signal_stock_codes = (
        sorted(signals_df["StockCode"].dropna().astype(str).unique().tolist())
        if signals_df is not None and not signals_df.empty
        else []
    )
    if not signal_stock_codes:
        st.info(f"目前沒有可供選擇的{direction_label}股票。")
        return

    selected_stock = st.selectbox(
        "選擇股票",
        options=signal_stock_codes,
        key=f"{key_prefix}_chart_select",
    )
    selected_df = all_data[all_data["StockCode"] == selected_stock].copy()
    try:
        figure, chart_message = create_stock_chart(
            selected_df,
            timeframe_label=saved_params["analysis_timeframe"],
            direction=direction_label,
        )
    except Exception as exc:
        figure, chart_message = None, f"建立圖表時發生錯誤：{exc}"
    if chart_message:
        st.warning(chart_message)
    elif figure is not None:
        st.plotly_chart(figure, width="stretch")


def _render_sidebar() -> dict:
    """Render the sidebar form and return all user inputs as one dict."""
    with st.sidebar:
        st.header("篩選設定")

        st.subheader("股票來源")
        use_auto_universe = st.checkbox(
            "自動抓取台灣全市場股票（上市＋上櫃）",
            value=True,
            help=AUTO_UNIVERSE_DESCRIPTION,
        )

        manual_codes: list[str] = []
        if not use_auto_universe:
            stock_text = st.text_area("股票代號清單", value=DEFAULT_TEXT_STOCK_LIST, height=150)
            uploaded_file = st.file_uploader("上傳 CSV 或 Excel 檔（需含 StockCode 欄位）", type=["csv", "xlsx"])
            if uploaded_file is not None:
                try:
                    manual_codes = load_stock_list_from_upload(uploaded_file)
                    if not manual_codes:
                        st.warning("上傳檔案中沒有可用的股票代號，請檢查內容。")
                except ValueError as exc:
                    st.warning(str(exc))
            if not manual_codes:
                manual_codes = parse_stock_list(stock_text)
        else:
            st.caption("資料來源：TWSE 公開 ISIN 清單（上市與上櫃普通股）")

        st.subheader("日期與週期")
        default_start_date, default_end_date = app_config.default_date_range()
        start_date = st.date_input("開始日期", value=default_start_date)
        end_date = st.date_input("結束日期", value=default_end_date)
        analysis_timeframe = st.selectbox(
            "分析週期",
            options=list(TIMEFRAME_OPTIONS.keys()),
            index=list(TIMEFRAME_OPTIONS.keys()).index(DEFAULT_PARAMETERS["analysis_timeframe"]),
        )

        st.subheader("篩選條件")
        min_volume = st.number_input(
            "最小成交量（張）",
            min_value=0,
            value=DEFAULT_PARAMETERS["min_volume"],
            step=100,
            help=(
                "台股 1 張 = 1000 股。設為 0 表示不篩選。"
                "門檻套用在「所選分析週期的單根 K 棒」成交量："
                "日 K 比對單日量、週 K 比對一週總量、月 K 比對一月總量，"
                "切換週期時請自行調整數值。"
            ),
        )
        lookback_bars = st.number_input(
            "回看 K 棒數",
            min_value=1,
            value=DEFAULT_PARAMETERS["lookback_bars"],
            step=1,
        )
        new_line_window = st.number_input(
            "新線回測窗格（交易日）",
            min_value=1,
            value=DEFAULT_PARAMETERS["new_line_window"],
            step=1,
            help="新紅／黑線出現後幾個交易日內，仍可作為 P2／P4 新線回測的基準線（不含出現當根）。",
        )
        direction_filter = st.selectbox(
            "方向過濾",
            options=DIRECTION_FILTER_OPTIONS,
            index=DIRECTION_FILTER_OPTIONS.index(DEFAULT_PARAMETERS["direction_filter"]),
            help="選擇只看做多（P1／P2）、只看做空（P3／P4），或兩者皆顯示。",
        )

        st.subheader("法人條件")
        st.caption("連買條件用於做多結果、連賣條件用於做空結果。")
        investor_consecutive_days = st.number_input(
            "法人連續買賣超天數",
            min_value=1,
            max_value=20,
            value=DEFAULT_PARAMETERS["investor_consecutive_days"],
            step=1,
        )
        foreign_buy_streak = st.checkbox(
            f"做多：外資最近 {int(investor_consecutive_days)} 日連續買超",
            value=DEFAULT_PARAMETERS["foreign_buy_streak"],
        )
        trust_buy_streak = st.checkbox(
            f"做多：投信最近 {int(investor_consecutive_days)} 日連續買超",
            value=DEFAULT_PARAMETERS["trust_buy_streak"],
        )
        foreign_sell_streak = st.checkbox(
            f"做空：外資最近 {int(investor_consecutive_days)} 日連續賣超",
            value=DEFAULT_PARAMETERS["foreign_sell_streak"],
        )
        trust_sell_streak = st.checkbox(
            f"做空：投信最近 {int(investor_consecutive_days)} 日連續賣超",
            value=DEFAULT_PARAMETERS["trust_sell_streak"],
        )

        run_screening = st.button("開始篩選", type="primary", width="stretch")

    return {
        "use_auto_universe": use_auto_universe,
        "manual_codes": manual_codes,
        "run_screening": run_screening,
        "start_date": start_date,
        "end_date": end_date,
        "params": _build_params(
            start_date=start_date,
            end_date=end_date,
            analysis_timeframe=analysis_timeframe,
            lookback_bars=lookback_bars,
            min_volume=min_volume,
            new_line_window=new_line_window,
            direction_filter=direction_filter,
            investor_consecutive_days=investor_consecutive_days,
            foreign_buy_streak=foreign_buy_streak,
            trust_buy_streak=trust_buy_streak,
            foreign_sell_streak=foreign_sell_streak,
            trust_sell_streak=trust_sell_streak,
        ),
    }


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption(f"版本 v{APP_VERSION}　更新日期：{APP_UPDATED}")
    st.caption(APP_PURPOSE)

    sidebar = _render_sidebar()
    use_auto_universe = sidebar["use_auto_universe"]
    manual_codes = sidebar["manual_codes"]
    run_screening = sidebar["run_screening"]
    params = sidebar["params"]

    if sidebar["start_date"] > sidebar["end_date"]:
        st.error("開始日期必須早於或等於結束日期。")
        return

    if run_screening:
        if use_auto_universe:
            try:
                _load_taiwan_stock_universe_cached()
            except Exception as exc:
                st.error(f"無法取得台股上市與上櫃股票清單：{exc}")
                return
        elif not manual_codes:
            st.warning("請先輸入至少一個股票代號。")
            return

        progress_placeholder = st.sidebar.empty()
        progress_bar = st.sidebar.progress(0.0)

        def _progress_callback(progress_value: float, message: str) -> None:
            progress_bar.progress(min(max(progress_value, 0.0), 1.0))
            progress_placeholder.caption(message)

        try:
            with st.spinner("正在下載資料並偵測突破回測訊號..."):
                st.session_state["screening_results"] = _run_screening(
                    params,
                    use_auto_universe=use_auto_universe,
                    manual_codes=manual_codes,
                    progress_callback=_progress_callback,
                )
                st.session_state["screening_params"] = params
                # 匯出檔以 run_id 為快取鍵：只有新一次篩選才需要重建。
                st.session_state["screening_run_id"] = st.session_state.get("screening_run_id", 0) + 1
        except Exception as exc:
            st.error(f"篩選過程發生錯誤：{exc}")
            return
        finally:
            progress_bar.empty()
            progress_placeholder.empty()

    results = st.session_state.get("screening_results")
    saved_params = st.session_state.get("screening_params", params)
    if not results:
        st.info("請設定條件後按下「開始篩選」。")
        return

    all_data: pd.DataFrame = results["all_data"]
    long_signals: pd.DataFrame = results["long_signals"]
    short_signals: pd.DataFrame = results["short_signals"]
    latest_summary_long: pd.DataFrame = results["latest_summary_long"]
    latest_summary_short: pd.DataFrame = results["latest_summary_short"]
    success_list: list[str] = results["success_list"]
    failed_list: list[str] = results["failed_list"]
    universe_df: pd.DataFrame = results["universe_df"]
    messages: list[dict[str, str]] = results.get("messages", [])
    used_auto_universe: bool = bool(results.get("used_auto_universe", False))

    for message in messages:
        level = message.get("level", "info")
        text = message.get("text", "").strip()
        if not text:
            continue
        if level == "error":
            st.error(text)
        elif level == "warning":
            st.warning(text)
        else:
            st.info(text)

    st.subheader("下載狀態")
    if used_auto_universe and not universe_df.empty:
        listed_count = int((universe_df["MarketLabel"] == "上市").sum())
        otc_count = int((universe_df["MarketLabel"] == "上櫃").sum())
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("自動載入股票總數", int(len(universe_df)))
        c2.metric("上市股票數", listed_count)
        c3.metric("上櫃股票數", otc_count)
        c4.metric("成功下載檔數", len(success_list))
    else:
        c1, c2 = st.columns(2)
        c1.metric("成功下載檔數", len(success_list))
        c2.metric("失敗檔數", len(failed_list))

    if failed_list:
        if used_auto_universe:
            # Whole-market mode always has hundreds of symbols with no recent data
            # (delisted / illiquid / newly listed). Listing them is noise, not a
            # failure — summarize the count and keep the full list in the Excel.
            st.caption(
                f"全市場模式下有 {len(failed_list)} 個代號在此區間無可用資料"
                "（多為下市、流動性不足或新上市），完整清單見 Excel「下載失敗清單」。"
            )
        else:
            preview = ", ".join(failed_list[:50])
            suffix = " ..." if len(failed_list) > 50 else ""
            st.warning("下載失敗股票：" + preview + suffix)
    else:
        st.success("所有要求的股票代號都已成功下載。")

    def _col_sum(frame: pd.DataFrame, *cols: str) -> int:
        if frame is None or frame.empty:
            return 0
        mask = pd.Series(False, index=frame.index)
        for col in cols:
            if col in frame.columns:
                mask = mask | frame[col].fillna(False)
        return int(mask.sum())

    st.subheader("摘要")
    total_stocks = int(all_data["StockCode"].nunique()) if not all_data.empty else 0
    long_stocks = int(long_signals["StockCode"].nunique()) if not long_signals.empty else 0
    short_stocks = int(short_signals["StockCode"].nunique()) if not short_signals.empty else 0
    breakout_count = _col_sum(all_data, "break_red_line_daily", "break_black_line_daily")
    breakdown_count = _col_sum(all_data, "break_down_red_line", "break_down_black_line")
    hold_count = _col_sum(all_data, "retest_hold_daily")
    reject_count = _col_sum(all_data, "retest_reject_daily")

    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("可分析股票數", total_stocks)
    mc2.metric("做多符合股票數", long_stocks)
    mc3.metric("做空符合股票數", short_stocks)
    bc1, bc2, bc3, bc4 = st.columns(4)
    bc1.metric("突破 K 棒數", breakout_count)
    bc2.metric("跌破 K 棒數", breakdown_count)
    bc3.metric("回測守住 K 棒數", hold_count)
    bc4.metric("回測壓回 K 棒數", reject_count)

    direction_filter = saved_params.get("direction_filter", "全部")
    long_tab, short_tab = st.tabs(["做多", "做空"])
    with long_tab:
        if direction_filter == "做空":
            st.info("方向過濾設為「做空」，已隱藏做多結果。")
        else:
            _render_direction_results("做多", "long", long_signals, latest_summary_long, all_data, saved_params)
    with short_tab:
        if direction_filter == "做多":
            st.info("方向過濾設為「做多」，已隱藏做空結果。")
        else:
            _render_direction_results("做空", "short", short_signals, latest_summary_short, all_data, saved_params)

    st.subheader("結果下載")
    timeframe_code = TIMEFRAME_OPTIONS[saved_params["analysis_timeframe"]]

    # 匯出內容只在新一次篩選後改變；以 run_id 快取，避免每次 UI 互動
    # （例如切換 K 線圖股票）都對全量資料重建 Excel。
    run_id = st.session_state.get("screening_run_id", 0)
    export_cache = st.session_state.get("export_cache")
    if export_cache is None or export_cache.get("run_id") != run_id:
        combined_signals = pd.concat(
            [frame for frame in (long_signals, short_signals) if not frame.empty],
            ignore_index=True,
        ) if (not long_signals.empty or not short_signals.empty) else pd.DataFrame()
        display_combined = (
            _prepare_display_frame(combined_signals, SIGNAL_COLUMNS) if not combined_signals.empty else pd.DataFrame()
        )
        csv_bytes = (
            sanitize_for_spreadsheet(display_combined).to_csv(index=False).encode("utf-8-sig")
            if not display_combined.empty
            else b""
        )

        excel_bytes = b""
        excel_error = None
        try:
            excel_bytes = create_excel_bytes(
                all_data=all_data,
                long_signals=long_signals,
                short_signals=short_signals,
                latest_summary_long=latest_summary_long,
                latest_summary_short=latest_summary_short,
                failed_list=failed_list,
                params=saved_params,
                download_notes=results.get("download_errors", []),
            )
        except Exception as exc:
            excel_error = f"建立 Excel 匯出檔時發生錯誤：{exc}"

        export_cache = {
            "run_id": run_id,
            "csv_bytes": csv_bytes,
            "csv_empty": display_combined.empty,
            "excel_bytes": excel_bytes,
            "excel_error": excel_error,
        }
        st.session_state["export_cache"] = export_cache

    csv_bytes = export_cache["csv_bytes"]
    excel_bytes = export_cache["excel_bytes"]
    excel_error = export_cache["excel_error"]

    if excel_error:
        st.error(excel_error)

    csv_direction_label = {"做多": "做多", "做空": "做空"}.get(direction_filter, "做多＋做空")
    download_col1, download_col2 = st.columns(2)
    download_col1.download_button(
        label=f"下載 CSV 結果（{csv_direction_label}）",
        data=csv_bytes,
        file_name=f"signals_{timeframe_code}.csv",
        mime="text/csv",
        disabled=bool(export_cache["csv_empty"]),
        width="stretch",
    )
    download_col2.download_button(
        label="下載 Excel 結果",
        data=excel_bytes,
        file_name=f"signals_{timeframe_code}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=all_data.empty or bool(excel_error),
        width="stretch",
    )

    days = saved_params.get("investor_consecutive_days", 3)
    active_investor_filters = [
        label
        for enabled, label in (
            (saved_params.get("foreign_buy_streak"), f"做多-外資近{days}日連買"),
            (saved_params.get("trust_buy_streak"), f"做多-投信近{days}日連買"),
            (saved_params.get("foreign_sell_streak"), f"做空-外資近{days}日連賣"),
            (saved_params.get("trust_sell_streak"), f"做空-投信近{days}日連賣"),
        )
        if enabled
    ]
    st.caption(
        f"目前分析週期：{saved_params['analysis_timeframe']}　"
        f"方向過濾：{direction_filter}　"
        f"回看 {saved_params['lookback_bars']} 根 K 棒　"
        f"新線窗格 {saved_params.get('new_line_window', 5)} 日　"
        f"最小成交量 {saved_params['min_volume']} 張　"
        f"法人條件：{'、'.join(active_investor_filters) if active_investor_filters else '無'}"
    )


if __name__ == "__main__":
    main()
