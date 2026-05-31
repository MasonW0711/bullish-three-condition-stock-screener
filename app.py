"""Streamlit entrypoint for the breakout-and-retest-hold stock screener."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import config as app_config
from chart_engine import create_stock_chart
from data_loader import (
    download_investor_flow_data,
    download_stock_data,
    load_stock_list_from_upload,
    load_taiwan_stock_universe,
    parse_stock_list,
    resample_ohlcv,
)
from export_engine import create_excel_bytes
from signal_engine import attach_investor_flow_flags, run_signal_pipeline

APP_TITLE = app_config.APP_TITLE
APP_PURPOSE = app_config.APP_PURPOSE
AUTO_UNIVERSE_DESCRIPTION = app_config.AUTO_UNIVERSE_DESCRIPTION
DEFAULT_PARAMETERS = app_config.DEFAULT_PARAMETERS
DEFAULT_TEXT_STOCK_LIST = app_config.DEFAULT_TEXT_STOCK_LIST
DISPLAY_COLUMN_LABELS = app_config.DISPLAY_COLUMN_LABELS
INVESTOR_FLAG_COLUMNS = app_config.INVESTOR_FLAG_COLUMNS
LATEST_SUMMARY_COLUMNS = app_config.LATEST_SUMMARY_COLUMNS
RESULT_COLUMNS = app_config.RESULT_COLUMNS
TIMEFRAME_LABELS = app_config.TIMEFRAME_LABELS
TIMEFRAME_OPTIONS = app_config.TIMEFRAME_OPTIONS


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def _load_taiwan_stock_universe_cached() -> pd.DataFrame:
    return load_taiwan_stock_universe()


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _download_investor_flow_data_cached(
    stock_codes: tuple[str, ...],
    end_date: date,
    lookback_days: int,
) -> pd.DataFrame:
    return download_investor_flow_data(
        stock_codes=list(stock_codes),
        end_date=end_date,
        lookback_days=lookback_days,
    )


@st.cache_data(ttl=60 * 30, show_spinner=False)
def _download_stock_data_cached(
    stock_codes: tuple[str, ...],
    start_date: date,
    end_date: date,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    return download_stock_data(
        stock_codes=list(stock_codes),
        start_date=start_date,
        end_date=end_date,
    )


def _build_params(
    start_date: date,
    end_date: date,
    analysis_timeframe: str,
    lookback_bars: int,
    min_volume: int,
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


def _apply_selected_investor_filters(processed_df: pd.DataFrame, params: dict) -> pd.DataFrame:
    output = processed_df.copy()
    for col in INVESTOR_FLAG_COLUMNS:
        if col not in output.columns:
            output[col] = False
    required_cols = _selected_investor_columns(params)
    output["investor_filter_pass"] = True
    if required_cols:
        output["investor_filter_pass"] = output[required_cols].fillna(False).all(axis=1)
        output["final_signal"] = output["final_signal"].fillna(False) & output["investor_filter_pass"]
    return output


def _compute_matching_retest_hold(processed_df: pd.DataFrame) -> pd.DataFrame:
    if processed_df.empty:
        return processed_df.copy()
    return (
        processed_df[processed_df["final_signal"].fillna(False)]
        .sort_values(["Date", "StockCode"], ascending=[False, True])
        .reset_index(drop=True)
    )


def _compute_latest_summary(matching_df: pd.DataFrame) -> pd.DataFrame:
    if matching_df.empty:
        return pd.DataFrame(columns=LATEST_SUMMARY_COLUMNS)

    latest = (
        matching_df.sort_values(["StockCode", "Date"])
        .groupby("StockCode", group_keys=False)
        .tail(1)
        .copy()
    )
    latest["SignalSummary"] = "Retest Hold Above " + latest["active_breakout_line_type"].fillna("")
    summary = latest.rename(
        columns={
            "Date": "LatestSignalDate",
            "active_breakout_line_type": "ActiveBreakoutLineType",
            "active_breakout_line_price": "ActiveBreakoutLinePrice",
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
) -> dict:
    empty = pd.DataFrame()
    return {
        "all_data": empty,
        "matching_retest_hold": empty,
        "latest_summary": _compute_latest_summary(empty),
        "success_list": success_list or [],
        "failed_list": failed_list or [],
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
        if progress_callback is not None:
            progress_callback(0.03, "正在準備股票下載清單...")

            def _download_progress_callback(progress_value: float, message: str) -> None:
                progress_callback(0.03 + min(max(progress_value, 0.0), 1.0) * 0.70, message)

            daily_data, success_list, failed_list = download_stock_data(
                stock_codes=stock_codes,
                start_date=params["start_date"],
                end_date=params["end_date"],
                progress_callback=_download_progress_callback,
            )
        else:
            daily_data, success_list, failed_list = _download_stock_data_cached(
                stock_codes=tuple(stock_codes),
                start_date=params["start_date"],
                end_date=params["end_date"],
            )
    except Exception as exc:
        messages.append({"level": "error", "text": f"股票資料下載失敗：{exc}"})
        return _empty_result(universe_df, messages=messages, used_auto_universe=use_auto_universe)

    if daily_data.empty:
        messages.append({"level": "warning", "text": "下載完成，但沒有取得任何可用股價資料。"})
        return _empty_result(
            universe_df,
            success_list=success_list,
            failed_list=failed_list,
            messages=messages,
            used_auto_universe=use_auto_universe,
        )

    if progress_callback is not None:
        progress_callback(0.78, "正在整理 K 棒週期與紅黑線訊號...")
    timeframe_data = resample_ohlcv(daily_data, timeframe_code)
    pipeline_params = {**params, "min_volume": min_volume_shares}
    processed = run_signal_pipeline(timeframe_data, pipeline_params)

    investor_filters_enabled = bool(_selected_investor_columns(params))
    investor_flow_df = pd.DataFrame()
    if investor_filters_enabled:
        if progress_callback is not None:
            progress_callback(0.88, "正在下載法人買賣超資料...")
        try:
            investor_flow_df = _download_investor_flow_data_cached(
                stock_codes=tuple(sorted(success_list)),
                end_date=params["end_date"],
                lookback_days=max(
                    int(app_config.INVESTOR_LOOKBACK_DAYS),
                    int(params.get("investor_consecutive_days", 3)) + 10,
                ),
            )
        except Exception as exc:
            messages.append({"level": "warning", "text": f"法人買賣超資料下載失敗，法人條件將視為未達成：{exc}"})
            investor_flow_df = pd.DataFrame()
        if investor_flow_df.empty:
            messages.append({"level": "warning", "text": "目前無法取得最新法人買賣超資料，法人條件已視為未達成。"})

    processed = attach_investor_flow_flags(
        processed,
        investor_flow_df,
        consecutive_days=params.get("investor_consecutive_days", 3),
    )
    processed = _apply_selected_investor_filters(processed, params)

    if not universe_df.empty and "StockName" in universe_df.columns:
        name_map = universe_df[["StockCode", "StockName"]].drop_duplicates("StockCode").set_index("StockCode")
        processed = processed.join(name_map, on="StockCode", how="left")
        processed["StockName"] = processed["StockName"].fillna(processed["StockCode"])
    else:
        processed["StockName"] = processed["StockCode"]

    matching_retest_hold = _compute_matching_retest_hold(processed)
    latest_summary = _compute_latest_summary(matching_retest_hold)
    if matching_retest_hold.empty:
        messages.append({"level": "info", "text": "本次篩選完成，但目前沒有符合條件的股票。"})
    if progress_callback is not None:
        progress_callback(1.0, "篩選完成。")

    return {
        "all_data": processed,
        "matching_retest_hold": matching_retest_hold,
        "latest_summary": latest_summary,
        "success_list": success_list,
        "failed_list": failed_list,
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
    return display_df[columns].rename(columns=DISPLAY_COLUMN_LABELS)


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption(APP_PURPOSE)

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
        start_date = st.date_input("開始日期", value=DEFAULT_PARAMETERS["start_date"])
        end_date = st.date_input("結束日期", value=DEFAULT_PARAMETERS["end_date"])
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
            help="台股 1 張 = 1000 股。設為 0 表示不篩選。",
        )
        lookback_bars = st.number_input(
            "回看 K 棒數",
            min_value=1,
            value=DEFAULT_PARAMETERS["lookback_bars"],
            step=1,
        )

        st.subheader("法人條件")
        investor_consecutive_days = st.number_input(
            "法人連續買賣超天數",
            min_value=1,
            max_value=20,
            value=DEFAULT_PARAMETERS["investor_consecutive_days"],
            step=1,
        )
        foreign_buy_streak = st.checkbox(
            f"外資最近 {int(investor_consecutive_days)} 日連續買超",
            value=DEFAULT_PARAMETERS["foreign_buy_streak"],
        )
        trust_buy_streak = st.checkbox(
            f"投信最近 {int(investor_consecutive_days)} 日連續買超",
            value=DEFAULT_PARAMETERS["trust_buy_streak"],
        )
        foreign_sell_streak = st.checkbox(
            f"外資最近 {int(investor_consecutive_days)} 日連續賣超",
            value=DEFAULT_PARAMETERS["foreign_sell_streak"],
        )
        trust_sell_streak = st.checkbox(
            f"投信最近 {int(investor_consecutive_days)} 日連續賣超",
            value=DEFAULT_PARAMETERS["trust_sell_streak"],
        )

        run_screening = st.button("開始篩選", type="primary", use_container_width=True)

    if start_date > end_date:
        st.error("開始日期必須早於或等於結束日期。")
        return

    params = _build_params(
        start_date=start_date,
        end_date=end_date,
        analysis_timeframe=analysis_timeframe,
        lookback_bars=lookback_bars,
        min_volume=min_volume,
        investor_consecutive_days=investor_consecutive_days,
        foreign_buy_streak=foreign_buy_streak,
        trust_buy_streak=trust_buy_streak,
        foreign_sell_streak=foreign_sell_streak,
        trust_sell_streak=trust_sell_streak,
    )

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
    matching_retest_hold: pd.DataFrame = results["matching_retest_hold"]
    latest_summary: pd.DataFrame = results["latest_summary"]
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
        preview = ", ".join(failed_list[:50])
        suffix = " ..." if len(failed_list) > 50 else ""
        st.warning("下載失敗股票：" + preview + suffix)
    else:
        st.success("所有要求的股票代號都已成功下載。")

    st.subheader("摘要")
    total_stocks = int(all_data["StockCode"].nunique()) if not all_data.empty else 0
    matched_stocks = int(matching_retest_hold["StockCode"].nunique()) if not matching_retest_hold.empty else 0
    breakout_count = int(
        (all_data["break_red_line_daily"].fillna(False) | all_data["break_black_line_daily"].fillna(False)).sum()
    ) if not all_data.empty else 0
    retest_count = int(all_data["retest_hold_daily"].fillna(False).sum()) if not all_data.empty else 0

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("可分析股票數", total_stocks)
    mc2.metric("符合股票數", matched_stocks)
    mc3.metric("突破 K 棒數", breakout_count)
    mc4.metric("回測守住 K 棒數", retest_count)

    st.subheader("符合條件的回測守住 K 棒")
    display_matching = pd.DataFrame()
    if matching_retest_hold.empty:
        st.info("目前沒有符合條件的回測守住 K 棒。")
    else:
        display_matching = _prepare_display_frame(matching_retest_hold, RESULT_COLUMNS)
        st.dataframe(display_matching, use_container_width=True)

    st.subheader("最新摘要（每股一列）")
    display_summary = pd.DataFrame()
    if latest_summary.empty:
        st.info("無最新摘要資料。")
    else:
        display_summary = _prepare_display_frame(latest_summary, LATEST_SUMMARY_COLUMNS)
        st.dataframe(display_summary, use_container_width=True)

    st.subheader("K 線圖")
    signal_stock_codes = (
        sorted(matching_retest_hold["StockCode"].dropna().astype(str).unique().tolist())
        if not matching_retest_hold.empty
        else []
    )
    if signal_stock_codes:
        selected_stock = st.selectbox("選擇股票（顯示符合條件的股票）", options=signal_stock_codes)
        selected_df = all_data[all_data["StockCode"] == selected_stock].copy()
        try:
            figure, chart_message = create_stock_chart(
                selected_df,
                timeframe_label=saved_params["analysis_timeframe"],
            )
        except Exception as exc:
            figure, chart_message = None, f"建立圖表時發生錯誤：{exc}"
        if chart_message:
            st.warning(chart_message)
        elif figure is not None:
            st.plotly_chart(figure, use_container_width=True)
    else:
        st.info("目前沒有可供選擇的符合條件股票。")

    st.subheader("結果下載")
    timeframe_code = TIMEFRAME_OPTIONS[saved_params["analysis_timeframe"]]
    csv_source = display_matching if not display_matching.empty else display_summary
    csv_bytes = csv_source.to_csv(index=False).encode("utf-8-sig") if not csv_source.empty else b""

    excel_bytes = b""
    excel_error = None
    try:
        excel_bytes = create_excel_bytes(
            all_data=all_data,
            matching_retest_hold=matching_retest_hold,
            latest_summary=latest_summary,
            failed_list=failed_list,
            params=saved_params,
        )
    except Exception as exc:
        excel_error = f"建立 Excel 匯出檔時發生錯誤：{exc}"

    if excel_error:
        st.error(excel_error)

    download_col1, download_col2 = st.columns(2)
    download_col1.download_button(
        label="下載 CSV 結果",
        data=csv_bytes,
        file_name=f"retest_hold_{timeframe_code}.csv",
        mime="text/csv",
        disabled=csv_source.empty,
        use_container_width=True,
    )
    download_col2.download_button(
        label="下載 Excel 結果",
        data=excel_bytes,
        file_name=f"retest_hold_{timeframe_code}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=all_data.empty or bool(excel_error),
        use_container_width=True,
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
        f"法人條件：{'、'.join(active_investor_filters) if active_investor_filters else '無'}"
    )


if __name__ == "__main__":
    main()
