"""Central configuration for the breakout-and-retest-hold stock screener."""

from __future__ import annotations

from datetime import date, timedelta

APP_VERSION = "2.1.0"
APP_UPDATED = "2026-06-12"

APP_TITLE = "紅黑線多空雙向突破回測選股系統"
APP_PURPOSE = (
    "本工具使用台灣證券交易所（TWSE）或其他網路公開資訊進行台股篩選，"
    "依方向（做多／做空）分流呈現符合訊號的股票，供您作為評估參考，並不構成投資建議。"
)
AUTO_UNIVERSE_DESCRIPTION = "系統會自動抓取台灣上市與上櫃普通股股票清單，無須手動上傳 CSV。"

DEFAULT_STOCK_LIST = [
    "2330.TW",
    "2317.TW",
    "2382.TW",
    "2474.TW",
    "6182.TWO",
]
DEFAULT_TEXT_STOCK_LIST = "\n".join(DEFAULT_STOCK_LIST)

TWSE_LISTED_ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
TWSE_OTC_ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
TAIWAN_COMMON_STOCK_CFICODE = "ESVUFR"
YFINANCE_BATCH_SIZE = 75
INVESTOR_LOOKBACK_DAYS = 20

# Network resilience for TWSE/TPEX requests. The cloud runtime frequently hits
# slow responses at market peaks, so the timeout is generous and transient
# failures are retried a bounded number of times before giving up.
REQUEST_TIMEOUT = 60
REQUEST_RETRIES = 2

TIMEFRAME_OPTIONS = {
    "Daily K": "D",
    "Weekly K": "W",
    "Monthly K": "M",
}
TIMEFRAME_LABELS = {code: label for label, code in TIMEFRAME_OPTIONS.items()}

# Direction routing (v2). The values double as the direction_filter选项 shown in the UI.
DIRECTION_FILTER_OPTIONS = ["全部", "做多", "做空"]

# 預設只抓最近一個月，避免全市場 × 長區間下載在雲端逾時。
# 注意：日期必須在每次 rerun 時求值（見 default_date_range()），不能在模組
# import 時固定，否則長駐的 Streamlit 行程會讓預設結束日期停在啟動當天。
DEFAULT_DATE_SPAN_DAYS = 30


def default_date_range() -> tuple[date, date]:
    """Return today's default (start_date, end_date), evaluated at call time."""
    today = date.today()
    return today - timedelta(days=DEFAULT_DATE_SPAN_DAYS), today


DEFAULT_PARAMETERS = {
    "analysis_timeframe": "Daily K",
    "lookback_bars": 10,
    # UI uses lots (張); internal calculations convert to shares.
    "min_volume": 2000,
    "investor_consecutive_days": 3,
    # v2 additions.
    "new_line_window": 5,
    "direction_filter": "全部",
    "foreign_buy_streak": False,
    "trust_buy_streak": False,
    "foreign_sell_streak": False,
    "trust_sell_streak": False,
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

INVESTOR_FLAG_COLUMNS = [
    "foreign_buy_streak_ok",
    "trust_buy_streak_ok",
    "foreign_sell_streak_ok",
    "trust_sell_streak_ok",
]

RESULT_COLUMNS = [
    "Date",
    "Timeframe",
    "StockCode",
    "StockName",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "prev_close",
    "red_attack_success",
    "red_attack_failed",
    "black_attack_success",
    "black_attack_failed",
    "attack_type",
    "attack_result",
    "signal_summary",
    "red_line",
    "black_line",
    "new_line_appeared",
    "new_line_type",
    "new_line_price",
    "break_red_line_daily",
    "break_black_line_daily",
    "breakout_line_type",
    "breakout_line_price",
    "break_down_red_line",
    "break_down_black_line",
    "breakdown_line_type",
    "breakdown_line_price",
    "active_breakout_line_type",
    "active_breakout_line_price",
    "active_breakdown_line_type",
    "active_breakdown_line_price",
    "bars_since_new_line",
    "active_new_line_type",
    "active_new_line_price",
    "new_line_window_valid",
    "retest_hold_daily",
    "retest_reject_daily",
    "p1_break_up_hold",
    "p2_new_line_hold",
    "p3_break_down_reject",
    "p4_new_line_reject",
    *INVESTOR_FLAG_COLUMNS,
    "volume_pass",
    "lookback_rank",
    "p1_final",
    "p2_final",
    "p3_final",
    "p4_final",
    "final_signal",
]

# Unified schema for the exploded Long_Signals / Short_Signals frames (§3.8).
SIGNAL_COLUMNS = [
    "Date",
    "Timeframe",
    "StockCode",
    "StockName",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "prev_close",
    "direction",
    "signal_type",
    "retest_line_type",
    "retest_line_price",
    *INVESTOR_FLAG_COLUMNS,
]

LATEST_SUMMARY_COLUMNS = [
    "StockCode",
    "StockName",
    "Direction",
    "SignalType",
    "LatestSignalDate",
    "Timeframe",
    "RetestLineType",
    "RetestLinePrice",
    "LatestOpen",
    "LatestHigh",
    "LatestLow",
    "LatestClose",
    "LatestVolume",
    *INVESTOR_FLAG_COLUMNS,
]

DISPLAY_COLUMN_LABELS = {
    "Date": "日期",
    "Timeframe": "週期",
    "StockCode": "股票代號",
    "StockName": "股票名稱",
    "Open": "開盤",
    "High": "最高",
    "Low": "最低",
    "Close": "收盤",
    "Volume": "成交量",
    "prev_close": "前一根收盤",
    "red_attack_success": "大紅攻成功",
    "red_attack_failed": "大紅攻失敗",
    "black_attack_success": "大黑攻成功",
    "black_attack_failed": "大黑攻失敗",
    "attack_type": "攻擊類型",
    "attack_result": "攻擊結果",
    "signal_summary": "訊號摘要",
    "red_line": "紅線",
    "black_line": "黑線",
    "new_line_appeared": "新線出現",
    "new_line_type": "新線類型",
    "new_line_price": "新線價格",
    "break_red_line_daily": "突破紅線",
    "break_black_line_daily": "突破黑線",
    "breakout_line_type": "突破線類型",
    "breakout_line_price": "突破線價格",
    "break_down_red_line": "跌破紅線",
    "break_down_black_line": "跌破黑線",
    "breakdown_line_type": "跌破線類型",
    "breakdown_line_price": "跌破線價格",
    "active_breakout_line_type": "目前突破回測線類型",
    "active_breakout_line_price": "目前突破回測線價格",
    "active_breakdown_line_type": "目前跌破回測線類型",
    "active_breakdown_line_price": "目前跌破回測線價格",
    "bars_since_new_line": "新線後第幾根",
    "active_new_line_type": "目前新線類型",
    "active_new_line_price": "目前新線價格",
    "new_line_window_valid": "新線窗格有效",
    "retest_hold_daily": "回測守住",
    "retest_reject_daily": "回測壓回",
    "p1_break_up_hold": "P1突破守住",
    "p2_new_line_hold": "P2新線守住",
    "p3_break_down_reject": "P3跌破壓回",
    "p4_new_line_reject": "P4新線壓回",
    "p1_final": "P1符合",
    "p2_final": "P2符合",
    "p3_final": "P3符合",
    "p4_final": "P4符合",
    "direction": "方向",
    "signal_type": "訊號路徑",
    "retest_line_type": "回測線類型",
    "retest_line_price": "回測線價格",
    "foreign_buy_streak_ok": "外資連買條件",
    "trust_buy_streak_ok": "投信連買條件",
    "foreign_sell_streak_ok": "外資連賣條件",
    "trust_sell_streak_ok": "投信連賣條件",
    "volume_pass": "成交量達標",
    "lookback_rank": "最近K棒序",
    "final_signal": "最終符合",
    "Direction": "方向",
    "SignalType": "訊號路徑",
    "LatestSignalDate": "最新訊號日期",
    "RetestLineType": "回測線類型",
    "RetestLinePrice": "回測線價格",
    "LatestOpen": "開盤",
    "LatestHigh": "最高",
    "LatestLow": "最低",
    "LatestClose": "收盤",
    "LatestVolume": "成交量",
    "SignalSummary": "訊號摘要",
    "foreign_net": "外資買賣超",
    "trust_net": "投信買賣超",
    "red_line_raw": "紅線（原始）",
    "black_line_raw": "黑線（原始）",
}

# Excel 匯出用：工作表名稱中文化
EXCEL_SHEET_LABELS = {
    "All_Data": "完整資料",
    "Long_Signals": "做多訊號",
    "Short_Signals": "做空訊號",
    "Latest_Summary_Long": "做多最新摘要",
    "Latest_Summary_Short": "做空最新摘要",
    "Failed_Downloads": "下載失敗清單",
    "Parameter_Settings": "參數設定",
}

# Excel 匯出用：參數設定工作表的參數名稱中文化
EXCEL_PARAMETER_LABELS = {
    "start_date": "開始日期",
    "end_date": "結束日期",
    "analysis_timeframe": "分析週期",
    "direction_filter": "方向過濾",
    "min_volume": "最小成交量（張）",
    "lookback_bars": "回看 K 棒數",
    "new_line_window": "新線回測窗格（交易日）",
    "investor_consecutive_days": "法人連續買賣超天數",
    "foreign_buy_streak": "外資連續買超條件（做多）",
    "trust_buy_streak": "投信連續買超條件（做多）",
    "foreign_sell_streak": "外資連續賣超條件（做空）",
    "trust_sell_streak": "投信連續賣超條件（做空）",
}
