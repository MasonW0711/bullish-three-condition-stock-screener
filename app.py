"""Streamlit entrypoint for the Bullish Three-Condition Stock Screener."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from chart_engine import create_stock_chart
from config import (
    APP_TITLE,
    APP_PURPOSE,
    DEFAULT_PARAMETERS,
    DEFAULT_TEXT_STOCK_LIST,
    DISPLAY_COLUMN_LABELS,
    NO_VOLUME_FILTER,
    RESULT_COLUMNS,
    TIMEFRAME_LABELS,
    TIMEFRAME_OPTIONS,
    VOLUME_FILTER_OPTIONS,
)
from data_loader import download_stock_data, load_stock_list_from_upload, parse_stock_list, resample_ohlcv
from export_engine import create_excel_bytes
from signal_engine import run_signal_pipeline


def _build_params(
    start_date: date,
    end_date: date,
    analysis_timeframe: str,
    lookback_days: int,
    min_gap_pct: float,
    min_close_vs_prev_pct: float,
    break_buffer_pct: float,
    retest_tolerance_pct: float,
    retest_break_pct: float,
    volume_filter_mode: str,
    min_volume_ratio_5: float,
    min_volume_ratio_20: float,
    min_score: int,
    only_latest_day: bool,
    show_recent_signals: bool,
) -> dict:
    return {
        "start_date": start_date,
        "end_date": end_date,
        "analysis_timeframe": analysis_timeframe,
        "lookback_days": int(lookback_days),
        "min_gap_pct": float(min_gap_pct),
        "min_close_vs_prev_pct": float(min_close_vs_prev_pct),
        "break_buffer_pct": float(break_buffer_pct),
        "retest_tolerance_pct": float(retest_tolerance_pct),
        "retest_break_pct": float(retest_break_pct),
        "volume_filter_mode": volume_filter_mode,
        "min_volume_ratio_5": float(min_volume_ratio_5),
        "min_volume_ratio_20": float(min_volume_ratio_20),
        "min_score": int(min_score),
        "only_latest_day": bool(only_latest_day),
        "show_recent_signals": bool(show_recent_signals),
    }


def _merge_stock_codes(text_input: str, uploaded_file) -> list[str]:
    text_codes = parse_stock_list(text_input)
    upload_codes = load_stock_list_from_upload(uploaded_file) if uploaded_file is not None else []
    merged = []
    for code in text_codes + upload_codes:
        if code not in merged:
            merged.append(code)
    return merged


def _prepare_display_frame(df: pd.DataFrame) -> pd.DataFrame:
    display_df = df.copy()
    for column in RESULT_COLUMNS:
        if column not in display_df.columns:
            display_df[column] = pd.NA
    display_df = display_df[RESULT_COLUMNS].sort_values(
        by=["final_long_signal", "long_signal_score", "volume_ratio_5", "close_vs_prev_pct"],
        ascending=[False, False, False, False],
        na_position="last",
    )
    return display_df.rename(columns=DISPLAY_COLUMN_LABELS)


def _recent_signal_stock_codes(processed_df: pd.DataFrame, lookback_days: int) -> list[str]:
    if processed_df.empty:
        return []

    recent_codes: list[str] = []
    for stock_code, stock_df in processed_df.groupby("StockCode", sort=False):
        recent_window = stock_df.sort_values("Date").tail(lookback_days)
        if recent_window["final_long_signal"].fillna(False).any():
            recent_codes.append(stock_code)
    return recent_codes


def _run_screening(stock_codes: list[str], params: dict) -> dict:
    timeframe_code = TIMEFRAME_OPTIONS[params["analysis_timeframe"]]

    daily_data, success_list, failed_list = download_stock_data(
        stock_codes=stock_codes,
        start_date=params["start_date"],
        end_date=params["end_date"],
    )

    if daily_data.empty:
        empty_df = pd.DataFrame(columns=RESULT_COLUMNS)
        return {
            "all_data": empty_df,
            "latest_result": empty_df,
            "display_df": empty_df,
            "success_list": success_list,
            "failed_list": failed_list,
            "recent_signal_codes": [],
        }

    timeframe_data = resample_ohlcv(daily_data, timeframe_code)
    processed = run_signal_pipeline(timeframe_data, params)

    if processed.empty:
        empty_df = pd.DataFrame(columns=RESULT_COLUMNS)
        return {
            "all_data": empty_df,
            "latest_result": empty_df,
            "display_df": empty_df,
            "success_list": success_list,
            "failed_list": failed_list,
            "recent_signal_codes": [],
        }

    latest_result = (
        processed.sort_values(["StockCode", "Date"]).groupby("StockCode", group_keys=False).tail(1)
    )
    recent_signal_codes = _recent_signal_stock_codes(processed, params["lookback_days"])

    display_source = latest_result if params["only_latest_day"] else processed
    if params["show_recent_signals"]:
        display_source = display_source[display_source["StockCode"].isin(recent_signal_codes)]
    display_df = _prepare_display_frame(display_source)

    return {
        "all_data": processed,
        "latest_result": latest_result,
        "display_df": display_df,
        "success_list": success_list,
        "failed_list": failed_list,
        "recent_signal_codes": recent_signal_codes,
    }


def _summary_metrics(latest_result: pd.DataFrame) -> dict:
    if latest_result.empty:
        return {
            "total_stocks": 0,
            "final_long_signal": 0,
            "score_3": 0,
            "score_2": 0,
            "latest_red_attack_success": 0,
            "latest_break_big_black": 0,
            "latest_retest_base": 0,
        }

    return {
        "total_stocks": int(latest_result["StockCode"].nunique()),
        "final_long_signal": int(latest_result["final_long_signal"].fillna(False).sum()),
        "score_3": int((latest_result["long_signal_score"] == 3).sum()),
        "score_2": int((latest_result["long_signal_score"] == 2).sum()),
        "latest_red_attack_success": int(latest_result["cond_A_red_attack_daily"].fillna(False).sum()),
        "latest_break_big_black": int(latest_result["cond_B_break_black_window"].fillna(False).sum()),
        "latest_retest_base": int(latest_result["cond_C_retest_base_window"].fillna(False).sum()),
    }


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption(APP_PURPOSE)

    with st.sidebar:
        st.header("篩選設定")

        st.subheader("股票清單輸入")
        stock_text = st.text_area(
            "每行輸入一個股票代號",
            value=DEFAULT_TEXT_STOCK_LIST,
            height=160,
        )
        uploaded_file = st.file_uploader("上傳 CSV", type=["csv"])

        st.subheader("日期與週期參數")
        start_date = st.date_input("開始日期", value=DEFAULT_PARAMETERS["start_date"])
        end_date = st.date_input("結束日期", value=DEFAULT_PARAMETERS["end_date"])
        analysis_timeframe = st.selectbox(
            "分析週期",
            options=list(TIMEFRAME_OPTIONS.keys()),
            index=0,
        )
        lookback_days = st.number_input(
            "回看 K 棒數",
            min_value=1,
            value=DEFAULT_PARAMETERS["lookback_days"],
            step=1,
        )

        st.subheader("攻擊門檻")
        min_gap_pct = st.number_input(
            "最小跳空幅度 (%)",
            value=DEFAULT_PARAMETERS["min_gap_pct"],
            step=0.1,
            format="%.2f",
        )
        min_close_vs_prev_pct = st.number_input(
            "最小收盤相對前收盤幅度 (%)",
            value=DEFAULT_PARAMETERS["min_close_vs_prev_pct"],
            step=0.1,
            format="%.2f",
        )

        st.subheader("突破與回測參數")
        break_buffer_pct = st.number_input(
            "突破緩衝 (%)",
            value=DEFAULT_PARAMETERS["break_buffer_pct"],
            step=0.1,
            format="%.2f",
        )
        retest_tolerance_pct = st.number_input(
            "回測容許值 (%)",
            value=DEFAULT_PARAMETERS["retest_tolerance_pct"],
            step=0.1,
            format="%.2f",
        )
        retest_break_pct = st.number_input(
            "回測跌破容許值 (%)",
            value=DEFAULT_PARAMETERS["retest_break_pct"],
            step=0.1,
            format="%.2f",
        )

        st.subheader("成交量條件")
        volume_filter_mode = st.selectbox(
            "成交量篩選模式",
            options=VOLUME_FILTER_OPTIONS,
            index=0,
        )
        min_volume_ratio_5 = st.number_input(
            "最小 5 日量比",
            value=DEFAULT_PARAMETERS["min_volume_ratio_5"],
            step=0.1,
            format="%.2f",
        )
        min_volume_ratio_20 = st.number_input(
            "最小 20 日量比",
            value=DEFAULT_PARAMETERS["min_volume_ratio_20"],
            step=0.1,
            format="%.2f",
        )

        st.subheader("篩選參數")
        min_score = st.number_input(
            "最低分數",
            min_value=1,
            max_value=3,
            value=DEFAULT_PARAMETERS["min_score"],
            step=1,
        )
        only_latest_day = st.checkbox(
            "只顯示每檔最新一根 K 棒",
            value=DEFAULT_PARAMETERS["only_latest_day"],
        )
        show_recent_signals = st.checkbox(
            "只顯示最近回看視窗內出現最終多頭訊號的股票",
            value=DEFAULT_PARAMETERS["show_recent_signals"],
        )

        run_screening = st.button("開始篩選", type="primary", use_container_width=True)

    if start_date > end_date:
        st.error("開始日期必須早於或等於結束日期。")
        return

    params = _build_params(
        start_date=start_date,
        end_date=end_date,
        analysis_timeframe=analysis_timeframe,
        lookback_days=lookback_days,
        min_gap_pct=min_gap_pct,
        min_close_vs_prev_pct=min_close_vs_prev_pct,
        break_buffer_pct=break_buffer_pct,
        retest_tolerance_pct=retest_tolerance_pct,
        retest_break_pct=retest_break_pct,
        volume_filter_mode=volume_filter_mode,
        min_volume_ratio_5=min_volume_ratio_5,
        min_volume_ratio_20=min_volume_ratio_20,
        min_score=min_score,
        only_latest_day=only_latest_day,
        show_recent_signals=show_recent_signals,
    )

    if run_screening:
        try:
            stock_codes = _merge_stock_codes(stock_text, uploaded_file)
        except ValueError as error:
            st.error(str(error))
            return

        if not stock_codes:
            st.warning("請至少在文字輸入區或 CSV 上傳中提供一個股票代號。")
            return

        with st.spinner("正在下載資料並執行篩選流程..."):
            st.session_state["screening_results"] = _run_screening(stock_codes, params)
            st.session_state["screening_params"] = params

    results = st.session_state.get("screening_results")
    saved_params = st.session_state.get("screening_params", params)

    if not results:
        st.info("請先設定條件，然後按下「開始篩選」。")
        return

    all_data = results["all_data"]
    latest_result = results["latest_result"]
    display_df = results["display_df"]
    success_list = results["success_list"]
    failed_list = results["failed_list"]

    st.subheader("下載狀態")
    status_col1, status_col2 = st.columns(2)
    status_col1.metric("成功下載檔數", len(success_list))
    status_col2.metric("失敗檔數", len(failed_list))
    if failed_list:
        st.warning("下載失敗股票：" + ", ".join(failed_list))
    else:
        st.success("所有要求的股票代號都已成功下載。")

    st.subheader("摘要指標")
    metrics = _summary_metrics(latest_result)
    metric_columns = st.columns(7)
    metric_columns[0].metric("股票總數", metrics["total_stocks"])
    metric_columns[1].metric("最終多頭訊號數", metrics["final_long_signal"])
    metric_columns[2].metric("3 分股票數", metrics["score_3"])
    metric_columns[3].metric("2 分股票數", metrics["score_2"])
    metric_columns[4].metric("最新大紅 K 數", metrics["latest_red_attack_success"])
    metric_columns[5].metric("最新突破黑攻基準數", metrics["latest_break_big_black"])
    metric_columns[6].metric("最新回測基準數", metrics["latest_retest_base"])

    st.subheader("篩選結果表")
    if display_df.empty:
        st.info("目前沒有符合結果篩選條件的資料列。")
    else:
        st.dataframe(display_df, use_container_width=True)

    st.subheader("圖表")
    chart_stock_codes = latest_result["StockCode"].dropna().astype(str).tolist()
    if chart_stock_codes:
        selected_stock = st.selectbox("選擇股票", options=chart_stock_codes)
        selected_stock_df = all_data[all_data["StockCode"] == selected_stock].copy()
        figure, chart_message = create_stock_chart(
            selected_stock_df,
            timeframe_label=saved_params["analysis_timeframe"],
        )
        if chart_message:
            st.warning(chart_message)
        elif figure is not None:
            st.plotly_chart(figure, use_container_width=True)
    else:
        st.info("目前沒有可用圖表資料。")

    st.subheader("Excel 下載")
    excel_bytes = create_excel_bytes(all_data, latest_result, saved_params)
    st.download_button(
        label="下載 Excel 結果",
        data=excel_bytes,
        file_name=f"bullish_three_condition_{TIMEFRAME_OPTIONS[saved_params['analysis_timeframe']]}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=all_data.empty,
    )

    if saved_params["volume_filter_mode"] != NO_VOLUME_FILTER:
        st.caption(
            f"成交量篩選已啟用：{saved_params['volume_filter_mode']}。"
        )

    st.caption(
        f"目前分析週期：{saved_params['analysis_timeframe']} "
        f"({TIMEFRAME_LABELS[TIMEFRAME_OPTIONS[saved_params['analysis_timeframe']]]})。"
    )


if __name__ == "__main__":
    main()
