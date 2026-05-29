"""Streamlit entrypoint for the Big Red / Big Black Attack Stock Screener."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import config as app_config
from chart_engine import create_stock_chart
from data_loader import (
    download_investor_flow_data,
    download_stock_data,
    load_taiwan_stock_universe,
    parse_stock_list,
    resample_ohlcv,
)
from export_engine import create_excel_bytes
from signal_engine import attach_investor_flow_flags, run_signal_pipeline

APP_TITLE = getattr(app_config, "APP_TITLE", "大紅攻 / 大黑攻 訊號選股系統")
APP_PURPOSE = getattr(
    app_config,
    "APP_PURPOSE",
    "本工具使用台灣證券交易所（TWSE）或其他網路公開資訊進行台股篩選，供您作為評估是否買進的參考，並不構成投資建議。",
)
AUTO_UNIVERSE_DESCRIPTION = getattr(
    app_config,
    "AUTO_UNIVERSE_DESCRIPTION",
    "系統會自動抓取台灣上市與上櫃普通股股票清單，無須手動上傳 CSV。",
)
DEFAULT_PARAMETERS = app_config.DEFAULT_PARAMETERS
DEFAULT_TEXT_STOCK_LIST = getattr(app_config, "DEFAULT_TEXT_STOCK_LIST", "2330.TW\n2317.TW\n2382.TW")
DISPLAY_COLUMN_LABELS = getattr(app_config, "DISPLAY_COLUMN_LABELS", {})
RESULT_COLUMNS = app_config.RESULT_COLUMNS
THREE_METHODS_COLUMNS = getattr(app_config, "THREE_METHODS_COLUMNS", [])
TIMEFRAME_LABELS = app_config.TIMEFRAME_LABELS
TIMEFRAME_OPTIONS = app_config.TIMEFRAME_OPTIONS


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def _load_taiwan_stock_universe_cached() -> pd.DataFrame:
    return load_taiwan_stock_universe()


def _build_params(
    start_date: date,
    end_date: date,
    analysis_timeframe: str,
    lookback_bars: int,
    min_volume: int,
    min_conditions: int = 2,
    pullback_pct: float = 2.0,
    investor_consecutive_days: int = 3,
    foreign_buy_streak: bool = False,
    trust_buy_streak: bool = False,
    foreign_sell_streak: bool = False,
    trust_sell_streak: bool = False,
) -> dict:
    return {
        "start_date": start_date,
        "end_date": end_date,
        "analysis_timeframe": analysis_timeframe,
        "lookback_bars": int(lookback_bars),
        "min_volume": int(min_volume),
        "min_conditions": int(min_conditions),
        "pullback_pct": float(pullback_pct),
        "investor_consecutive_days": max(int(investor_consecutive_days), 1),
        "foreign_buy_streak": bool(foreign_buy_streak),
        "trust_buy_streak": bool(trust_buy_streak),
        "foreign_sell_streak": bool(foreign_sell_streak),
        "trust_sell_streak": bool(trust_sell_streak),
    }


def _compute_lookback_matches(
    processed_df: pd.DataFrame,
    lookback_bars: int,
    min_volume_shares: int,
) -> pd.DataFrame:
    """Return K-bars in the last lookback_bars that have any attack signal and pass volume."""
    if processed_df.empty:
        return processed_df.copy()

    # Last N bars per stock.
    recent = (
        processed_df
        .sort_values(["StockCode", "Date"])
        .groupby("StockCode", group_keys=False)
        .tail(lookback_bars)
    )

    has_signal = (
        recent["red_attack_success"].fillna(False)
        | recent["red_attack_failed"].fillna(False)
        | recent["black_attack_success"].fillna(False)
        | recent["black_attack_failed"].fillna(False)
    )
    volume_ok = recent["Volume"].fillna(0) >= min_volume_shares

    return recent[has_signal & volume_ok].sort_values(
        ["Date", "StockCode"], ascending=[False, True]
    ).reset_index(drop=True)


def _compute_latest_summary(matching_df: pd.DataFrame) -> pd.DataFrame:
    """One row per stock: the most recent attack signal within the lookback window."""
    if matching_df.empty:
        return pd.DataFrame(
            columns=[
                "StockCode", "StockName", "LatestSignalDate", "Timeframe",
                "LatestSignalSummary", "LatestOpen", "LatestClose",
                "LatestPrevClose", "LatestVolume",
            ]
        )

    latest = (
        matching_df
        .sort_values(["StockCode", "Date"])
        .groupby("StockCode", group_keys=False)
        .tail(1)
    )

    cols_needed = {
        "Date": "LatestSignalDate",
        "signal_summary": "LatestSignalSummary",
        "Open": "LatestOpen",
        "Close": "LatestClose",
        "prev_close": "LatestPrevClose",
        "Volume": "LatestVolume",
    }
    result = latest[
        ["StockCode"] +
        (["StockName"] if "StockName" in latest.columns else []) +
        ["Timeframe"] +
        [c for c in cols_needed]
    ].rename(columns=cols_needed).reset_index(drop=True)

    return result.sort_values("LatestSignalDate", ascending=False)


def _run_screening(params: dict, use_auto_universe: bool, manual_codes: list[str], progress_callback=None) -> dict:
    timeframe_code = TIMEFRAME_OPTIONS[params["analysis_timeframe"]]
    min_volume_shares = params["min_volume"] * 1000  # convert lots → shares

    universe_df = pd.DataFrame()
    if use_auto_universe:
        universe_df = _load_taiwan_stock_universe_cached()
        stock_codes = universe_df["StockCode"].dropna().astype(str).tolist()
    else:
        stock_codes = manual_codes
        universe_df = pd.DataFrame()  # no universe for manual mode

    if not stock_codes:
        return _empty_result(universe_df)

    daily_data, success_list, failed_list = download_stock_data(
        stock_codes=stock_codes,
        start_date=params["start_date"],
        end_date=params["end_date"],
        progress_callback=progress_callback,
    )

    if daily_data.empty:
        return _empty_result(universe_df, success_list=success_list, failed_list=failed_list)

    # Pre-filter: remove stocks with insufficient average daily volume (performance).
    if min_volume_shares > 0:
        recent_avg = daily_data.groupby("StockCode")["Volume"].apply(
            lambda x: x.tail(20).mean() if len(x) >= 20 else x.mean()
        )
        active = set(recent_avg[recent_avg >= min_volume_shares].index)
        daily_data = daily_data[daily_data["StockCode"].isin(active)].copy()
        success_list = [s for s in success_list if s in active]

    if daily_data.empty:
        return _empty_result(universe_df, success_list=success_list, failed_list=failed_list)

    timeframe_data = resample_ohlcv(daily_data, timeframe_code)
    processed = run_signal_pipeline(timeframe_data, params)

    if processed.empty:
        return _empty_result(universe_df, success_list=success_list, failed_list=failed_list)

    investor_flow_df = download_investor_flow_data(
        stock_codes=success_list,
        end_date=params["end_date"],
        lookback_days=max(
            int(getattr(app_config, "INVESTOR_LOOKBACK_DAYS", 20)),
            int(params.get("investor_consecutive_days", 3)) + 10,
        ),
    )
    processed = attach_investor_flow_flags(
        processed,
        investor_flow_df,
        consecutive_days=params.get("investor_consecutive_days", 3),
    )

    # Join Chinese stock name from the universe lookup table.
    if not universe_df.empty and "StockName" in universe_df.columns:
        name_map = (
            universe_df[["StockCode", "StockName"]]
            .drop_duplicates("StockCode")
            .set_index("StockCode")
        )
        processed = processed.join(name_map, on="StockCode", how="left")
        processed["StockName"] = processed["StockName"].fillna(processed["StockCode"])
    else:
        processed["StockName"] = processed["StockCode"]

    matching_signals = _compute_lookback_matches(processed, params["lookback_bars"], min_volume_shares)
    latest_summary = _compute_latest_summary(matching_signals)
    three_methods_bullish, three_methods_bearish = _compute_three_methods_matches(
        processed, params.get("min_conditions", 2)
    )
    three_methods_bullish, three_methods_bearish = _apply_investor_filters(
        three_methods_bullish,
        three_methods_bearish,
        params,
    )

    return {
        "all_data": processed,
        "matching_signals": matching_signals,
        "latest_summary": latest_summary,
        "three_methods_bullish": three_methods_bullish,
        "three_methods_bearish": three_methods_bearish,
        "success_list": success_list,
        "failed_list": failed_list,
        "universe_df": universe_df,
    }


def _compute_three_methods_matches(
    processed_df: pd.DataFrame,
    min_conditions: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return one-row-per-stock Three Methods summaries with exclusive direction."""
    if processed_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Get the most recent row per stock.
    latest = (
        processed_df
        .sort_values(["StockCode", "Date"])
        .groupby("StockCode", group_keys=False)
        .tail(1)
        .copy()
    )

    # Columns that may or may not be present, fill with safe defaults if missing.
    for cnt_col in ("bullish_methods_count", "bearish_methods_count", "final_methods_count"):
        if cnt_col not in latest.columns:
            latest[cnt_col] = 0
    for cond_col in (
        "bull_cond_1_in_window", "bull_cond_2_in_window", "bull_cond_3_in_window",
        "bear_cond_1_in_window", "bear_cond_2_in_window", "bear_cond_3_in_window",
        "red_base", "black_base", "final_methods_direction",
        "foreign_buy_streak_ok", "trust_buy_streak_ok", "foreign_sell_streak_ok", "trust_sell_streak_ok",
    ):
        if cond_col not in latest.columns:
            latest[cond_col] = "None" if cond_col == "final_methods_direction" else pd.NA

    def _select_cols(df: pd.DataFrame) -> pd.DataFrame:
        """Return only the THREE_METHODS_COLUMNS that exist in df."""
        available = [c for c in THREE_METHODS_COLUMNS if c in df.columns]
        return df[available].reset_index(drop=True)

    bullish = latest[
        (latest["final_methods_direction"] == "Bullish")
        & (latest["final_methods_count"] >= min_conditions)
    ].copy()
    bearish = latest[
        (latest["final_methods_direction"] == "Bearish")
        & (latest["final_methods_count"] >= min_conditions)
    ].copy()

    bullish = _select_cols(bullish).sort_values(
        ["final_methods_count", "bullish_methods_count"], ascending=[False, False]
    ).reset_index(drop=True) if not bullish.empty else pd.DataFrame()

    bearish = _select_cols(bearish).sort_values(
        ["final_methods_count", "bearish_methods_count"], ascending=[False, False]
    ).reset_index(drop=True) if not bearish.empty else pd.DataFrame()

    return bullish, bearish


def _apply_investor_filters(
    bullish_df: pd.DataFrame,
    bearish_df: pd.DataFrame,
    params: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply optional institutional-flow filters to bullish/bearish result tables."""
    bullish = bullish_df.copy()
    bearish = bearish_df.copy()

    bull_required = [
        col
        for col, enabled in (
            ("foreign_buy_streak_ok", params.get("foreign_buy_streak", False)),
            ("trust_buy_streak_ok", params.get("trust_buy_streak", False)),
        )
        if enabled
    ]
    bear_required = [
        col
        for col, enabled in (
            ("foreign_sell_streak_ok", params.get("foreign_sell_streak", False)),
            ("trust_sell_streak_ok", params.get("trust_sell_streak", False)),
        )
        if enabled
    ]

    if not bullish.empty and bull_required:
        bullish = bullish[bullish[bull_required].fillna(False).all(axis=1)].reset_index(drop=True)
    if not bearish.empty and bear_required:
        bearish = bearish[bearish[bear_required].fillna(False).all(axis=1)].reset_index(drop=True)
    return bullish, bearish


def _empty_result(universe_df: pd.DataFrame, success_list=None, failed_list=None) -> dict:
    empty = pd.DataFrame()
    return {
        "all_data": empty,
        "matching_signals": empty,
        "latest_summary": empty,
        "three_methods_bullish": empty,
        "three_methods_bearish": empty,
        "success_list": success_list or [],
        "failed_list": failed_list or [],
        "universe_df": universe_df,
    }


def _prepare_display_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Select RESULT_COLUMNS (fill missing with NA) and rename to Chinese labels."""
    display_df = df.copy()
    for col in RESULT_COLUMNS:
        if col not in display_df.columns:
            display_df[col] = pd.NA
    if "Timeframe" in display_df.columns:
        display_df["Timeframe"] = display_df["Timeframe"].map(TIMEFRAME_LABELS).fillna(display_df["Timeframe"])
    available = [c for c in RESULT_COLUMNS if c in display_df.columns]
    return display_df[available].rename(columns=DISPLAY_COLUMN_LABELS)


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption(APP_PURPOSE)

    with st.sidebar:
        st.header("篩選設定")

        # ── Stock source ────────────────────────────────────────────────────
        st.subheader("股票來源")
        use_auto_universe = st.checkbox(
            "🇹🇼 自動抓取台灣全市場股票（上市＋上櫃）",
            value=True,
            help=AUTO_UNIVERSE_DESCRIPTION,
        )

        manual_codes: list[str] = []
        if not use_auto_universe:
            st.caption("每行一個股票代號（例如 2330.TW）")
            stock_text = st.text_area(
                "股票代號清單",
                value=DEFAULT_TEXT_STOCK_LIST,
                height=150,
            )
            uploaded_csv = st.file_uploader("或上傳 CSV（需含 StockCode 欄位）", type=["csv"])
            if uploaded_csv is not None:
                try:
                    csv_df = pd.read_csv(uploaded_csv)
                    if "StockCode" in csv_df.columns:
                        manual_codes = csv_df["StockCode"].dropna().astype(str).str.strip().tolist()
                    else:
                        st.warning("上傳的 CSV 必須包含 'StockCode' 欄位。")
                except Exception as exc:
                    st.warning(f"無法讀取 CSV：{exc}")
            if not manual_codes:
                manual_codes = parse_stock_list(stock_text)
        else:
            st.caption("資料來源：TWSE 公開 ISIN 清單（上市與上櫃普通股）")

        # ── Date and timeframe ───────────────────────────────────────────────
        st.subheader("日期與週期")
        start_date = st.date_input("開始日期", value=DEFAULT_PARAMETERS["start_date"])
        end_date = st.date_input("結束日期", value=DEFAULT_PARAMETERS["end_date"])
        analysis_timeframe = st.selectbox(
            "分析週期",
            options=list(TIMEFRAME_OPTIONS.keys()),
            index=0,
        )

        # ── Filter parameters ────────────────────────────────────────────────
        st.subheader("篩選條件")
        min_volume = st.number_input(
            "最小成交量（張）",
            min_value=0,
            value=DEFAULT_PARAMETERS["min_volume"],
            step=100,
            help="台股 1 張 = 1000 股。設為 0 表示不篩選。yfinance 資料以股數計算，系統自動換算。",
        )
        lookback_bars = st.number_input(
            "回看 K 棒數",
            min_value=1,
            value=DEFAULT_PARAMETERS["lookback_bars"],
            step=1,
            help="只顯示最近 N 根 K 棒內出現攻擊訊號的資料列。",
        )
        min_conditions = st.selectbox(
            "三方法最少達成條件數",
            options=[1, 2, 3],
            index=DEFAULT_PARAMETERS.get("min_conditions", 2) - 1,
            help="多頭或空頭三方法，至少需滿足幾個條件才算成立（預設 2）",
        )
        pullback_pct = st.slider(
            "回測有效範圍 ±% (pullback_pct)",
            min_value=0.0,
            max_value=10.0,
            value=float(DEFAULT_PARAMETERS.get("pullback_pct", 2.0)),
            step=0.5,
            format="%.1f%%",
            help="回測條件（條件三）的有效觸及範圍：Low（多頭）或 High（空頭）須在基準價 ±pct% 以內，且收盤不可收破基準價。",
        )
        investor_consecutive_days = st.number_input(
            "法人連續買賣超天數",
            min_value=1,
            max_value=20,
            value=int(DEFAULT_PARAMETERS.get("investor_consecutive_days", 3)),
            step=1,
            help="以最近 N 個交易日判斷外資 / 投信是否連續買超或連續賣超。",
        )
        st.caption(f"法人最近 {int(investor_consecutive_days)} 日條件（可個別勾選）")
        foreign_buy_streak = st.checkbox(
            f"多頭：外資最近 {int(investor_consecutive_days)} 日連續買超",
            value=bool(DEFAULT_PARAMETERS.get("foreign_buy_streak", False)),
        )
        trust_buy_streak = st.checkbox(
            f"多頭：投信最近 {int(investor_consecutive_days)} 日連續買超",
            value=bool(DEFAULT_PARAMETERS.get("trust_buy_streak", False)),
        )
        foreign_sell_streak = st.checkbox(
            f"空頭：外資最近 {int(investor_consecutive_days)} 日連續賣超",
            value=bool(DEFAULT_PARAMETERS.get("foreign_sell_streak", False)),
        )
        trust_sell_streak = st.checkbox(
            f"空頭：投信最近 {int(investor_consecutive_days)} 日連續賣超",
            value=bool(DEFAULT_PARAMETERS.get("trust_sell_streak", False)),
        )

        run_screening = st.button("開始篩選", type="primary", use_container_width=True)

    # ── Validation ───────────────────────────────────────────────────────────
    if start_date > end_date:
        st.error("開始日期必須早於或等於結束日期。")
        return

    params = _build_params(
        start_date=start_date,
        end_date=end_date,
        analysis_timeframe=analysis_timeframe,
        lookback_bars=lookback_bars,
        min_volume=min_volume,
        min_conditions=min_conditions,
        pullback_pct=pullback_pct,
        investor_consecutive_days=investor_consecutive_days,
        foreign_buy_streak=foreign_buy_streak,
        trust_buy_streak=trust_buy_streak,
        foreign_sell_streak=foreign_sell_streak,
        trust_sell_streak=trust_sell_streak,
    )

    # ── Run screening ────────────────────────────────────────────────────────
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

        with st.spinner("正在下載資料並偵測攻擊訊號..."):
            st.session_state["screening_results"] = _run_screening(
                params,
                use_auto_universe=use_auto_universe,
                manual_codes=manual_codes,
                progress_callback=_progress_callback,
            )
            st.session_state["screening_params"] = params

        progress_bar.empty()
        progress_placeholder.empty()

    # ── Retrieve cached results ───────────────────────────────────────────────
    results = st.session_state.get("screening_results")
    saved_params = st.session_state.get("screening_params", params)

    if not results:
        st.info("請設定條件後按下「開始篩選」。")
        return

    all_data: pd.DataFrame = results["all_data"]
    matching_signals: pd.DataFrame = results["matching_signals"]
    latest_summary: pd.DataFrame = results["latest_summary"]
    three_methods_bullish: pd.DataFrame = results.get("three_methods_bullish", pd.DataFrame())
    three_methods_bearish: pd.DataFrame = results.get("three_methods_bearish", pd.DataFrame())
    success_list: list[str] = results["success_list"]
    failed_list: list[str] = results["failed_list"]
    universe_df: pd.DataFrame = results["universe_df"]

    # ── Download status ───────────────────────────────────────────────────────
    st.subheader("下載狀態")
    if not universe_df.empty:
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
        preview = ", ".join(failed_list[:50])
        suffix = " ..." if len(failed_list) > 50 else ""
        st.warning("下載失敗股票：" + preview + suffix)
    else:
        st.success("所有要求的股票代號都已成功下載。")

    # ── Summary metrics ───────────────────────────────────────────────────────
    st.subheader("摘要")
    total_stocks = int(all_data["StockCode"].nunique()) if not all_data.empty else 0
    matched_stocks = int(matching_signals["StockCode"].nunique()) if not matching_signals.empty else 0
    red_success = int(matching_signals["red_attack_success"].fillna(False).sum()) if not matching_signals.empty else 0
    red_failed = int(matching_signals["red_attack_failed"].fillna(False).sum()) if not matching_signals.empty else 0
    black_success = int(matching_signals["black_attack_success"].fillna(False).sum()) if not matching_signals.empty else 0
    black_failed = int(matching_signals["black_attack_failed"].fillna(False).sum()) if not matching_signals.empty else 0

    mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
    mc1.metric("篩選後股票數", total_stocks)
    mc2.metric(f"近 {saved_params['lookback_bars']} 棒有訊號股票數", matched_stocks)
    mc3.metric("大紅攻成功 K 棒數", red_success)
    mc4.metric("大紅攻失敗 K 棒數", red_failed)
    mc5.metric("大黑攻成功 K 棒數", black_success)
    mc6.metric("大黑攻失敗 K 棒數", black_failed)

    # ── Three Methods result tables ───────────────────────────────────────────
    min_cond_label = saved_params.get("min_conditions", 2)
    with st.expander(f"🟢 多頭三方法篩選（達成 ≥ {min_cond_label} 個條件）", expanded=True):
        if three_methods_bullish.empty:
            st.info("目前沒有符合多頭三方法條件的股票。")
        else:
            display_bull = three_methods_bullish.rename(columns=DISPLAY_COLUMN_LABELS)
            st.dataframe(display_bull, use_container_width=True)
            st.caption(f"共 {len(three_methods_bullish)} 檔股票符合多頭三方法條件（≥ {min_cond_label} 個）。")

    with st.expander(f"🔴 空頭三方法篩選（達成 ≥ {min_cond_label} 個條件）", expanded=True):
        if three_methods_bearish.empty:
            st.info("目前沒有符合空頭三方法條件的股票。")
        else:
            display_bear = three_methods_bearish.rename(columns=DISPLAY_COLUMN_LABELS)
            st.dataframe(display_bear, use_container_width=True)
            st.caption(f"共 {len(three_methods_bearish)} 檔股票符合空頭三方法條件（≥ {min_cond_label} 個）。")

    # ── Matching signals table ────────────────────────────────────────────────
    st.subheader("訊號匹配結果（回看視窗內、有訊號、量達標）")
    if matching_signals.empty:
        st.info("目前沒有符合條件的攻擊訊號 K 棒。")
    else:
        display_matching = _prepare_display_frame(matching_signals)
        st.dataframe(display_matching, use_container_width=True)

    # ── Latest summary table ──────────────────────────────────────────────────
    st.subheader("最新訊號摘要（每股一列）")
    if latest_summary.empty:
        st.info("無最新訊號摘要資料。")
    else:
        summary_label_map = {
            "StockCode": "股票代號",
            "StockName": "股票名稱",
            "LatestSignalDate": "最新訊號日期",
            "Timeframe": "週期",
            "LatestSignalSummary": "最新訊號",
            "LatestOpen": "開盤",
            "LatestClose": "收盤",
            "LatestPrevClose": "前一根收盤",
            "LatestVolume": "成交量",
        }
        display_summary = latest_summary.copy()
        if "Timeframe" in display_summary.columns:
            display_summary["Timeframe"] = display_summary["Timeframe"].map(TIMEFRAME_LABELS).fillna(
                display_summary["Timeframe"]
            )
        display_summary = display_summary.rename(
            columns={k: v for k, v in summary_label_map.items() if k in latest_summary.columns}
        )
        st.dataframe(display_summary, use_container_width=True)

    # ── Chart ─────────────────────────────────────────────────────────────────
    st.subheader("K 線圖")
    signal_stock_codes = sorted(matching_signals["StockCode"].dropna().astype(str).unique().tolist()) if not matching_signals.empty else []
    if signal_stock_codes:
        selected_stock = st.selectbox("選擇股票（顯示有訊號的股票）", options=signal_stock_codes)
        selected_df = all_data[all_data["StockCode"] == selected_stock].copy()
        figure, chart_message = create_stock_chart(
            selected_df,
            timeframe_label=saved_params["analysis_timeframe"],
        )
        if chart_message:
            st.warning(chart_message)
        elif figure is not None:
            st.plotly_chart(figure, use_container_width=True)
    else:
        st.info("目前沒有可供選擇的有訊號股票。")

    # ── Excel download ────────────────────────────────────────────────────────
    st.subheader("Excel 下載")
    excel_bytes = create_excel_bytes(
        all_data=all_data,
        matching_signals=matching_signals,
        latest_summary=latest_summary,
        three_methods_bullish=three_methods_bullish,
        three_methods_bearish=three_methods_bearish,
        failed_list=failed_list,
        params=saved_params,
    )
    timeframe_code = TIMEFRAME_OPTIONS[saved_params["analysis_timeframe"]]
    st.download_button(
        label="下載 Excel 結果",
        data=excel_bytes,
        file_name=f"attack_signals_{timeframe_code}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=all_data.empty,
    )

    active_investor_filters = [
        label
        for enabled, label in (
            (saved_params.get("foreign_buy_streak"), f"外資近{saved_params.get('investor_consecutive_days', 3)}日連買"),
            (saved_params.get("trust_buy_streak"), f"投信近{saved_params.get('investor_consecutive_days', 3)}日連買"),
            (saved_params.get("foreign_sell_streak"), f"外資近{saved_params.get('investor_consecutive_days', 3)}日連賣"),
            (saved_params.get("trust_sell_streak"), f"投信近{saved_params.get('investor_consecutive_days', 3)}日連賣"),
        )
        if enabled
    ]
    st.caption(
        f"目前分析週期：{saved_params['analysis_timeframe']}　"
        f"回看 {saved_params['lookback_bars']} 根 K 棒　"
        f"最小成交量 {saved_params['min_volume']} 張　"
        f"三方法最少條件數 {saved_params.get('min_conditions', 2)}　"
        f"回測有效範圍 ±{saved_params.get('pullback_pct', 2.0):.1f}%　"
        f"法人連續天數 {saved_params.get('investor_consecutive_days', 3)} 日　"
        f"法人條件：{'、'.join(active_investor_filters) if active_investor_filters else '無'}"
    )


if __name__ == "__main__":
    main()
