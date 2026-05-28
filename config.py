"""Central configuration for the Bullish Three-Condition Stock Screener."""

from __future__ import annotations

from datetime import date, timedelta

APP_TITLE = "Bullish Three-Condition Stock Screener"

DEFAULT_STOCK_LIST = [
    "2330.TW",
    "2317.TW",
    "2382.TW",
    "2474.TW",
    "6182.TWO",
]

DEFAULT_TEXT_STOCK_LIST = "\n".join(DEFAULT_STOCK_LIST)

TIMEFRAME_OPTIONS = {
    "Daily K": "D",
    "Weekly K": "W",
    "Monthly K": "M",
}

TIMEFRAME_LABELS = {code: label for label, code in TIMEFRAME_OPTIONS.items()}

NO_VOLUME_FILTER = "No volume filter"
VOLUME_FILTER_OPTIONS = [
    NO_VOLUME_FILTER,
    "Require 5-day volume ratio",
    "Require 20-day volume ratio",
    "Require both 5-day and 20-day volume ratios",
]

DEFAULT_PARAMETERS = {
    "start_date": date.today() - timedelta(days=365 * 3),
    "end_date": date.today(),
    "analysis_timeframe": "Daily K",
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
    "red_base": {"color": "#ef4444", "name": "red_base"},
    "black_base": {"color": "#2563eb", "name": "black_base"},
    "black_failed_base": {"color": "#16a34a", "name": "black_failed_base"},
}
