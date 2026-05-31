import contextlib
import unittest
from io import BytesIO
from io import StringIO
from unittest.mock import patch

import pandas as pd

with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
    from app import _compute_latest_summary
from config import LATEST_SUMMARY_COLUMNS
from data_loader import (
    _download_candidate,
    _select_isin_table,
    _to_int,
    download_stock_data,
    load_stock_list_from_upload,
    normalize_yfinance_data,
    resample_ohlcv,
)
from signal_engine import attach_investor_flow_flags, run_signal_pipeline


class StabilityTests(unittest.TestCase):
    def test_monthly_resample_uses_actual_last_trading_day(self):
        daily = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-29", "2026-06-01", "2026-06-30"]),
                "StockCode": ["2330.TW", "2330.TW", "2330.TW"],
                "Open": [100, 102, 110],
                "High": [105, 108, 112],
                "Low": [99, 101, 109],
                "Close": [103, 107, 111],
                "Volume": [1000, 2000, 3000],
            }
        )

        monthly = resample_ohlcv(daily, "M")

        self.assertEqual(monthly.loc[0, "Date"], pd.Timestamp("2026-05-29"))
        self.assertEqual(monthly.loc[1, "Date"], pd.Timestamp("2026-06-30"))
        self.assertEqual(monthly.loc[1, "Volume"], 5000)

    def test_weekly_resample_uses_actual_last_trading_day(self):
        daily = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-04", "2026-05-05"]),
                "StockCode": ["2330.TW", "2330.TW"],
                "Open": [100, 102],
                "High": [105, 108],
                "Low": [99, 101],
                "Close": [103, 107],
                "Volume": [1000, 2000],
            }
        )

        weekly = resample_ohlcv(daily, "W")

        self.assertEqual(weekly.loc[0, "Date"], pd.Timestamp("2026-05-05"))
        self.assertEqual(weekly.loc[0, "Open"], 100)
        self.assertEqual(weekly.loc[0, "Close"], 107)
        self.assertEqual(weekly.loc[0, "Volume"], 3000)

    def test_investor_flags_use_latest_flow_date_on_or_before_bar_date(self):
        bars = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-07"]),
                "StockCode": ["2330.TW"],
                "Open": [100],
                "High": [101],
                "Low": [99],
                "Close": [100],
                "Volume": [1000],
            }
        )
        flow = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-08"]),
                "BaseCode": ["2330"] * 4,
                "foreign_net": [1, 1, 1, 1],
                "trust_net": [1, 1, 1, 1],
            }
        )

        result = attach_investor_flow_flags(bars, flow, consecutive_days=3)

        self.assertTrue(result.loc[0, "foreign_buy_streak_ok"])
        self.assertTrue(result.loc[0, "trust_buy_streak_ok"])

    def test_investor_flags_stop_after_last_available_flow_date(self):
        bars = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-11"]),
                "StockCode": ["2330.TW"],
                "Open": [100],
                "High": [101],
                "Low": [99],
                "Close": [100],
                "Volume": [1000],
            }
        )
        flow = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-04", "2026-05-05", "2026-05-06"]),
                "BaseCode": ["2330"] * 3,
                "foreign_net": [1, 1, 1],
                "trust_net": [1, 1, 1],
            }
        )

        result = attach_investor_flow_flags(bars, flow, consecutive_days=3)

        self.assertFalse(result.loc[0, "foreign_buy_streak_ok"])
        self.assertFalse(result.loc[0, "trust_buy_streak_ok"])

    def test_failed_attacks_do_not_create_opposite_lines(self):
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-01", "2026-05-04", "2026-05-05"]),
                "StockCode": ["2330.TW"] * 3,
                "Open": [100, 105, 95],
                "High": [101, 106, 104],
                "Low": [99, 97, 94],
                "Close": [100, 98, 103],
                "Volume": [1000, 1000, 1000],
            }
        )

        result = run_signal_pipeline(frame, {"lookback_bars": 3, "min_volume": 0})

        self.assertTrue(result.loc[1, "red_attack_failed"])
        self.assertFalse(result.loc[1, "black_attack_success"])
        self.assertTrue(pd.isna(result.loc[1, "black_line"]))
        self.assertTrue(result.loc[2, "black_attack_failed"])
        self.assertFalse(result.loc[2, "red_attack_success"])
        self.assertTrue(pd.isna(result.loc[2, "red_line"]))

    def test_breakout_and_retest_hold_are_final_signal(self):
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06"]),
                "StockCode": ["2330.TW"] * 4,
                "Open": [100, 95, 99, 101],
                "High": [101, 97, 106, 103],
                "Low": [99, 94, 98, 99],
                "Close": [100, 96, 105, 102],
                "Volume": [1000, 1000, 3000, 3000],
            }
        )

        result = run_signal_pipeline(frame, {"lookback_bars": 3, "min_volume": 2000})

        self.assertTrue(result.loc[2, "break_black_line_daily"])
        self.assertEqual(result.loc[2, "active_breakout_line_type"], "Black Line")
        self.assertEqual(result.loc[2, "active_breakout_line_price"], 100)
        self.assertTrue(result.loc[3, "retest_hold_daily"])
        self.assertTrue(result.loc[3, "final_signal"])

    def test_retest_failure_is_not_final_signal(self):
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06"]),
                "StockCode": ["2330.TW"] * 4,
                "Open": [100, 95, 99, 101],
                "High": [101, 97, 106, 103],
                "Low": [99, 94, 98, 99],
                "Close": [100, 96, 105, 97],
                "Volume": [1000, 1000, 3000, 3000],
            }
        )

        result = run_signal_pipeline(frame, {"lookback_bars": 3, "min_volume": 2000})

        self.assertFalse(result.loc[3, "retest_hold_daily"])
        self.assertFalse(result.loc[3, "final_signal"])

    def test_invalid_universe_table_shape_raises_clear_error(self):
        bad_table = pd.DataFrame([["2330 台積電", "TW0002330008", "2020/01/01"]])

        with self.assertRaisesRegex(ValueError, "公開股票清單表格格式異常"):
            _select_isin_table([bad_table])

    def test_yfinance_multiindex_normalization_yields_ohlcv_columns(self):
        raw = pd.DataFrame(
            [[100, 105, 99, 103, 1000]],
            index=pd.to_datetime(["2026-05-04"]),
            columns=pd.MultiIndex.from_product(
                [["2330.TW"], ["Open", "High", "Low", "Close", "Volume"]]
            ),
        )

        result = normalize_yfinance_data(raw, "2330.TW")

        self.assertEqual(
            result.columns.tolist(),
            ["Date", "StockCode", "Open", "High", "Low", "Close", "Volume"],
        )
        self.assertEqual(result.loc[0, "StockCode"], "2330.TW")
        self.assertEqual(result.loc[0, "Close"], 103)

    def test_yfinance_download_end_date_is_inclusive_for_user_selection(self):
        with patch("data_loader.yf.download", return_value=pd.DataFrame()) as mocked_download:
            _download_candidate("2330.TW", "2026-05-01", "2026-05-29")

        self.assertEqual(mocked_download.call_args.kwargs["end"].isoformat(), "2026-05-30")

    def test_investor_integer_parser_tolerates_public_data_placeholders(self):
        self.assertEqual(_to_int("1,234"), 1234)
        self.assertEqual(_to_int("(1,234)"), -1234)
        self.assertEqual(_to_int("--"), 0)
        self.assertEqual(_to_int("not-a-number"), 0)

    def test_bare_otc_code_falls_back_without_failed_result(self):
        fallback_raw = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-29"]),
                "Open": [100],
                "High": [105],
                "Low": [99],
                "Close": [103],
                "Volume": [1000],
            }
        ).set_index("Date")

        with patch("data_loader._download_candidate", side_effect=[pd.DataFrame(), fallback_raw]):
            data, successes, failures = download_stock_data(["6182"], "2026-05-01", "2026-05-29")

        self.assertEqual(successes, ["6182.TWO"])
        self.assertEqual(failures, [])
        self.assertEqual(data.loc[0, "StockCode"], "6182.TWO")

    def test_excel_upload_supports_common_stock_code_columns(self):
        upload_buffer = BytesIO()
        pd.DataFrame({"股票代號": ["2330.TW", "2317.tw", None, "2330.TW"]}).to_excel(
            upload_buffer,
            index=False,
            engine="openpyxl",
        )
        upload_buffer.name = "stocks.xlsx"
        upload_buffer.seek(0)

        result = load_stock_list_from_upload(upload_buffer)

        self.assertEqual(result, ["2330.TW", "2317.TW"])

    def test_empty_latest_summary_keeps_export_schema(self):
        latest_summary = _compute_latest_summary(pd.DataFrame())

        self.assertEqual(latest_summary.columns.tolist(), LATEST_SUMMARY_COLUMNS)


if __name__ == "__main__":
    unittest.main()
