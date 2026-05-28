"""Central configuration for the Big Red / Big Black Attack Stock Screener."""

from __future__ import annotations

from datetime import date, timedelta

APP_TITLE = "大紅攻 / 大黑攻 訊號選股系統"
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

TIMEFRAME_OPTIONS = {
    "日 K": "D",
    "週 K": "W",
    "月 K": "M",
}

TIMEFRAME_LABELS = {code: label for label, code in TIMEFRAME_OPTIONS.items()}

DEFAULT_PARAMETERS = {
    "start_date": date.today() - timedelta(days=365 * 2),
    "end_date": date.today(),
    "analysis_timeframe": "日 K",
    "lookback_bars": 10,
    # min_volume is in lots (張); 1 lot = 1000 shares. Applied as Volume >= min_volume * 1000.
    "min_volume": 2000,
    # min_conditions: how many of the 3 Three Methods conditions must be satisfied (1 / 2 / 3).
    "min_conditions": 2,
    # pullback_pct: ±% range around the reference price that a valid pullback (cond_3) must touch.
    # Close must also not close beyond the reference, or the pullback is considered failed.
    "pullback_pct": 2.0,
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

# Columns shown in the matching-signals result table.
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
    "red_base",
    "black_base",
    "red_attack_success",
    "red_attack_failed",
    "black_attack_success",
    "black_attack_failed",
    "attack_type",
    "attack_result",
    "attack_direction",
    "signal_summary",
]

# Columns shown in the Three Methods result tables.
THREE_METHODS_COLUMNS = [
    "StockCode",
    "StockName",
    "Date",
    "final_methods_direction",
    "final_methods_count",
    "Close",
    "Volume",
    "red_base",
    "black_base",
    "bull_cond_1_in_window",
    "bull_cond_2_in_window",
    "bull_cond_3_in_window",
    "bullish_methods_count",
    "bear_cond_1_in_window",
    "bear_cond_2_in_window",
    "bear_cond_3_in_window",
    "bearish_methods_count",
]

DISPLAY_COLUMN_LABELS = {
    "Date": "日期",
    "Timeframe": "週期",
    "StockCode": "股票代號",
    "StockName": "股票名稱",
    "final_methods_direction": "最終三方法方向",
    "final_methods_count": "最終三方法分數",
    "Open": "開盤",
    "High": "最高",
    "Low": "最低",
    "Close": "收盤",
    "Volume": "成交量",
    "prev_close": "前一根收盤",
    "red_base": "多攻基準",
    "black_base": "空攻基準",
    "red_attack_success": "大紅攻成功",
    "red_attack_failed": "大紅攻失敗",
    "black_attack_success": "大黑攻成功",
    "black_attack_failed": "大黑攻失敗",
    "attack_type": "攻擊類型",
    "attack_result": "攻擊結果",
    "attack_direction": "攻擊方向",
    "signal_summary": "訊號摘要",
    "bull_cond_1_in_window": "多頭條件1(大紅攻)",
    "bull_cond_2_in_window": "多頭條件2(突破空攻)",
    "bull_cond_3_in_window": "多頭條件3(回測)",
    "bullish_methods_count": "多頭達成條件數",
    "bear_cond_1_in_window": "空頭條件1(大黑攻)",
    "bear_cond_2_in_window": "空頭條件2(跌破多攻)",
    "bear_cond_3_in_window": "空頭條件3(反彈回測)",
    "bearish_methods_count": "空頭達成條件數",
}
