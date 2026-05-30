import contextlib
import unittest
from io import StringIO
from unittest.mock import patch

import pandas as pd

with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
    from app import _compute_three_methods_matches
from config import THREE_METHODS_COLUMNS
from data_loader import (
    _download_candidate,
    _select_isin_table,
    _to_int,
    download_stock_data,
    normalize_yfinance_data,
    resample_ohlcv,
)
from signal_engine import add_three_methods_conditions, attach_investor_flow_flags


class StabilityTests(unittest.TestCase):
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

    def test_equal_three_methods_scores_have_no_final_direction(self):
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-04"]),
                "StockCode": ["2330.TW"],
                "Open": [100],
                "High": [101],
                "Low": [99],
                "Close": [100],
                "red_attack_success": [True],
                "black_attack_success": [True],
                "red_base": [pd.NA],
                "black_base": [pd.NA],
            }
        )

        result = add_three_methods_conditions(frame, lookback_bars=3)

        self.assertEqual(result.loc[0, "bullish_methods_count"], 1)
        self.assertEqual(result.loc[0, "bearish_methods_count"], 1)
        self.assertEqual(result.loc[0, "final_methods_direction"], "None")

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

    def test_empty_three_methods_tables_keep_export_schema(self):
        bullish, bearish = _compute_three_methods_matches(pd.DataFrame(), min_conditions=2)

        self.assertEqual(bullish.columns.tolist(), THREE_METHODS_COLUMNS)
        self.assertEqual(bearish.columns.tolist(), THREE_METHODS_COLUMNS)


if __name__ == "__main__":
    unittest.main()
