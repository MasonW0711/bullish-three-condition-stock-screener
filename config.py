"""Central configuration for the Bullish Three-Condition Stock Screener."""

from __future__ import annotations

from datetime import date, timedelta

APP_TITLE = "多頭三條件選股系統"
APP_PURPOSE = (
    "本工具使用台灣證券交易所（TWSE）或其他網路公開資訊進行台股篩選，"
    "供您作為評估是否買進的參考，並不構成投資建議。"
)

DEFAULT_STOCK_LIST = [
    "2330.TW",
    "2317.TW",
    "2382.TW",
    "2474.TW",
    "6182.TWO",
]

DEFAULT_TEXT_STOCK_LIST = "\n".join(DEFAULT_STOCK_LIST)

TIMEFRAME_OPTIONS = {
    "日 K": "D",
    "週 K": "W",
    "月 K": "M",
}

TIMEFRAME_LABELS = {code: label for label, code in TIMEFRAME_OPTIONS.items()}

NO_VOLUME_FILTER = "不使用成交量篩選"
VOLUME_FILTER_OPTIONS = [
    NO_VOLUME_FILTER,
    "要求 5 日量比",
    "要求 20 日量比",
    "同時要求 5 日與 20 日量比",
]

DEFAULT_PARAMETERS = {
    "start_date": date.today() - timedelta(days=365 * 3),
    "end_date": date.today(),
    "analysis_timeframe": "日 K",
    "lookback_days": 10,
    "min_gap_pct": 0.0,
    "min_close_vs_prev_pct": 0.0,
    "break_buffer_pct": 0.2,
    "retest_tolerance_pct": 0.5,
    "retest_break_pct": 0.3,
    "volume_filter_mode": NO_VOLUME_FILTER,
    "min_volume_ratio_5": 1.0,
    "min_volume_ratio_20": 1.0,
    "min_score": 2,
    "only_latest_day": True,
    "show_recent_signals": False,
}

REQUIRED_OHLCV_COLUMNS = [
    "Date",
    "StockCode",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
]

RESULT_COLUMNS = [
    "Date",
    "Timeframe",
    "StockCode",
    "Open",
    "High",
    "Low",
    "Close",
    "prev_close",
    "gap_pct",
    "close_vs_prev_pct",
    "Volume",
    "volume_ratio_5",
    "volume_ratio_20",
    "red_attack_success",
    "red_attack_failed",
    "black_attack_success",
    "black_attack_failed",
    "red_base",
    "black_base",
    "black_failed_base",
    "cond_A_red_attack_window",
    "cond_B_break_black_window",
    "cond_C_retest_base_window",
    "long_signal_score",
    "long_signal",
    "volume_filter_pass",
    "final_long_signal",
]

EXPORT_SHEET_NAMES = [
    "All_Data",
    "Latest_Result",
    "Final_Long_Signal",
    "Score_3",
    "Score_2",
    "Red_Attack_Success",
    "Black_Attack_Success",
    "Black_Attack_Failed",
    "Parameter_Settings",
]

CHART_BASE_LINE_STYLES = {
    "red_base": {"color": "#ef4444", "name": "紅攻基準"},
    "black_base": {"color": "#2563eb", "name": "黑攻基準"},
    "black_failed_base": {"color": "#16a34a", "name": "黑攻失敗基準"},
}

DISPLAY_COLUMN_LABELS = {
    "Date": "日期",
    "Timeframe": "週期",
    "StockCode": "股票代號",
    "Open": "開盤",
    "High": "最高",
    "Low": "最低",
    "Close": "收盤",
    "prev_close": "前一根收盤",
    "gap_pct": "開盤相對前收盤(%)",
    "close_vs_prev_pct": "收盤相對前收盤(%)",
    "Volume": "成交量",
    "volume_ratio_5": "5 日量比",
    "volume_ratio_20": "20 日量比",
    "red_attack_success": "紅攻成功",
    "red_attack_failed": "紅攻失敗",
    "black_attack_success": "黑攻成功",
    "black_attack_failed": "黑攻失敗",
    "red_base": "紅攻基準",
    "black_base": "黑攻基準",
    "black_failed_base": "黑攻失敗基準",
    "cond_A_red_attack_window": "條件 A 視窗",
    "cond_B_break_black_window": "條件 B 視窗",
    "cond_C_retest_base_window": "條件 C 視窗",
    "long_signal_score": "多頭分數",
    "long_signal": "多頭訊號",
    "volume_filter_pass": "成交量篩選通過",
    "final_long_signal": "最終多頭訊號",
}
