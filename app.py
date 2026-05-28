"""Streamlit entrypoint for the Bullish Three-Condition Stock Screener."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import config as app_config
from chart_engine import create_stock_chart
from data_loader import download_stock_data, load_taiwan_stock_universe, resample_ohlcv
from export_engine import create_excel_bytes
from signal_engine import run_signal_pipeline

APP_TITLE = getattr(app_config, "APP_TITLE", "多頭三條件選股系統")
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
DISPLAY_COLUMN_LABELS = getattr(app_config, "DISPLAY_COLUMN_LABELS", {})
NO_VOLUME_FILTER = app_config.NO_VOLUME_FILTER
RESULT_COLUMNS = app_config.RESULT_COLUMNS
TIMEFRAME_LABELS = app_config.TIMEFRAME_LABELS
TIMEFRAME_OPTIONS = app_config.TIMEFRAME_OPTIONS
VOLUME_FILTER_OPTIONS = app_config.VOLUME_FILTER_OPTIONS


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def _load_taiwan_stock_universe_cached() -> pd.DataFrame:
    return load_taiwan_stock_universe()


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
    min_daily_volume_lots: int,
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
        "min_daily_volume_lots": int(min_daily_volume_lots),
        "min_score": int(min_score),
        "only_latest_day": bool(only_latest_day),
        "show_recent_signals": bool(show_recent_signals),
    }


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


def _run_screening(params: dict, progress_callback=None) -> dict:
    timeframe_code = TIMEFRAME_OPTIONS[params["analysis_timeframe"]]
    universe_df = _load_taiwan_stock_universe_cached()
    stock_codes = universe_df["StockCode"].dropna().astype(str).tolist()

    if not stock_codes:
        empty_df = pd.DataFrame(columns=RESULT_COLUMNS)
        return {
            "all_data": empty_df,
            "latest_result": empty_df,
            "display_df": empty_df,
            "success_list": [],
            "failed_list": [],
            "recent_signal_codes": [],
            "universe_df": universe_df,
        }

    daily_data, success_list, failed_list = download_stock_data(
        stock_codes=stock_codes,
        start_date=params["start_date"],
        end_date=params["end_date"],
        progress_callback=progress_callback,
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
            "universe_df": universe_df,
        }

    # Pre-filter: keep only stocks with sufficient average daily volume.
    # yfinance returns Taiwan stock volume in shares; 1 lot (張) = 1000 shares.
    min_lots = int(params.get("min_daily_volume_lots", 0))
    if min_lots > 0:
        min_shares = min_lots * 1000
        recent_avg = daily_data.groupby("StockCode")["Volume"].apply(
            lambda x: x.tail(20).mean() if len(x) >= 20 else x.mean()
        )
        active_stocks = set(recent_avg[recent_avg >= min_shares].index)
        daily_data = daily_data[daily_data["StockCode"].isin(active_stocks)].copy()
        success_list = [s for s in success_list if s in active_stocks]

    if daily_data.empty:
        empty_df = pd.DataFrame(columns=RESULT_COLUMNS)
        return {
            "all_data": empty_df,
            "latest_result": empty_df,
            "display_df": empty_df,
            "success_list": success_list,
            "failed_list": failed_list,
            "recent_signal_codes": [],
            "universe_df": universe_df,
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
            "universe_df": universe_df,
        }

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
        "universe_df": universe_df,
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
        st.subheader("股票範圍")
        st.info(AUTO_UNIVERSE_DESCRIPTION)
        st.caption("資料來源：TWSE 公開 ISIN 清單（上市與上櫃普通股）")

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
        min_daily_volume_lots = st.number_input(
            "最小日均成交量（張）",
            value=DEFAULT_PARAMETERS["min_daily_volume_lots"],
            min_value=0,
            step=100,
            help="設為 0 表示不篩選。台股 1 張 = 1000 股，yfinance 資料以股數計算，系統自動換算。",
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
        min_daily_volume_lots=min_daily_volume_lots,
        min_score=min_score,
        only_latest_day=only_latest_day,
        show_recent_signals=show_recent_signals,
    )

    if run_screening:
        try:
            universe_df = _load_taiwan_stock_universe_cached()
        except ValueError as error:
            st.error(str(error))
            return
        except Exception as error:
            st.error(f"無法取得台股上市與上櫃股票清單：{error}")
            return

        if universe_df.empty:
            st.warning("目前無法取得台股上市與上櫃普通股股票清單。")
            return

        progress_placeholder = st.sidebar.empty()
        progress_bar = st.sidebar.progress(0.0)

        def _progress_callback(progress_value: float, message: str) -> None:
            progress_bar.progress(min(max(progress_value, 0.0), 1.0))
            progress_placeholder.caption(message)

        with st.spinner("正在自動抓取台股上市與上櫃股票資料並執行篩選流程..."):
            st.session_state["screening_results"] = _run_screening(
                params,
                progress_callback=_progress_callback,
            )
            st.session_state["screening_params"] = params
        progress_bar.empty()
        progress_placeholder.empty()

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
    universe_df = results["universe_df"]

    st.subheader("下載狀態")
    listed_count = int((universe_df["MarketLabel"] == "上市").sum()) if not universe_df.empty else 0
    otc_count = int((universe_df["MarketLabel"] == "上櫃").sum()) if not universe_df.empty else 0
    status_col1, status_col2, status_col3, status_col4 = st.columns(4)
    status_col1.metric("自動載入股票總數", int(len(universe_df)))
    status_col2.metric("上市股票數", listed_count)
    status_col3.metric("上櫃股票數", otc_count)
    status_col4.metric("成功下載檔數", len(success_list))
    st.metric("失敗檔數", len(failed_list))
    if failed_list:
        preview = ", ".join(failed_list[:50])
        suffix = " ..." if len(failed_list) > 50 else ""
        st.warning("下載失敗股票：" + preview + suffix)
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
