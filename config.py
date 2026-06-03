"""Central configuration for the breakout-and-retest-hold stock screener."""

from __future__ import annotations

from datetime import date, timedelta

APP_TITLE = "紅黑線突破回測守住選股系統"
APP_PURPOSE = (
    "本工具使用台灣證券交易所（TWSE）或其他網路公開資訊進行台股篩選，"
    "供您作為評估是否買進的參考，並不構成投資建議。"
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

DEFAULT_PARAMETERS = {
    "start_date": date.today() - timedelta(days=365 * 2),
    "end_date": date.today(),
    "analysis_timeframe": "Daily K",
    "lookback_bars": 10,
    # UI uses lots (張); internal calculations convert to shares.
    "min_volume": 2000,
    "investor_consecutive_days": 3,
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
    "break_red_line_daily",
    "break_black_line_daily",
    "breakout_line_type",
    "breakout_line_price",
    "active_breakout_line_type",
    "active_breakout_line_price",
    "retest_hold_daily",
    *INVESTOR_FLAG_COLUMNS,
    "volume_pass",
    "lookback_rank",
    "final_signal",
]

LATEST_SUMMARY_COLUMNS = [
    "StockCode",
    "StockName",
    "LatestSignalDate",
    "Timeframe",
    "ActiveBreakoutLineType",
    "ActiveBreakoutLinePrice",
    "LatestOpen",
    "LatestHigh",
    "LatestLow",
    "LatestClose",
    "LatestVolume",
    "SignalSummary",
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
    "break_red_line_daily": "突破紅線",
    "break_black_line_daily": "突破黑線",
    "breakout_line_type": "突破線類型",
    "breakout_line_price": "突破線價格",
    "active_breakout_line_type": "目前回測線類型",
    "active_breakout_line_price": "目前回測線價格",
    "retest_hold_daily": "回測守住",
    "foreign_buy_streak_ok": "外資連買條件",
    "trust_buy_streak_ok": "投信連買條件",
    "foreign_sell_streak_ok": "外資連賣條件",
    "trust_sell_streak_ok": "投信連賣條件",
    "volume_pass": "成交量達標",
    "lookback_rank": "最近K棒序",
    "final_signal": "最終符合",
    "LatestSignalDate": "最新訊號日期",
    "ActiveBreakoutLineType": "回測線類型",
    "ActiveBreakoutLinePrice": "回測線價格",
    "LatestOpen": "開盤",
    "LatestHigh": "最高",
    "LatestLow": "最低",
    "LatestClose": "收盤",
    "LatestVolume": "成交量",
    "SignalSummary": "訊號摘要",
}
