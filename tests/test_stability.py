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
    download_investor_flow_data,
    download_stock_data,
    load_stock_list_from_upload,
    normalize_yfinance_data,
    resample_ohlcv,
)
from signal_engine import (
    attach_investor_flow_flags,
    build_direction_signals,
    run_signal_pipeline,
)


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
        self.assertTrue(result.loc[3, "p1_break_up_hold"])
        self.assertFalse(result.loc[3, "p3_break_down_reject"])
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

        # The long P1 hold genuinely fails (close below the breakout line).
        self.assertFalse(result.loc[3, "retest_hold_daily"])
        self.assertFalse(result.loc[3, "p1_break_up_hold"])
        # Bar 3 is itself a fresh new-line appearance (bars_since == 0), so the
        # appearance-bar-excluded window keeps P4 from firing; the bar matches
        # no path and final_signal stays False.
        self.assertEqual(result.loc[3, "bars_since_new_line"], 0)
        self.assertFalse(result.loc[3, "p4_new_line_reject"])
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
            data, successes, failures, download_errors = download_stock_data(
                ["6182"], "2026-05-01", "2026-05-29"
            )

        self.assertEqual(successes, ["6182.TWO"])
        self.assertEqual(failures, [])
        self.assertEqual(download_errors, [])
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

    def test_normalize_yfinance_data_tolerates_unexpected_shape(self):
        # yfinance schema drift must not raise; it should yield an empty frame.
        malformed = pd.DataFrame({"unexpected": [1, 2, 3]})

        result = normalize_yfinance_data(malformed, stock_code="2330.TW")

        self.assertTrue(result.empty)

    def test_download_stock_data_reports_batch_errors(self):
        # A raising download must be recorded as a diagnostic note, not swallowed.
        with patch("data_loader._download_candidate", side_effect=RuntimeError("boom")):
            data, successes, failures, download_errors = download_stock_data(
                ["2330"], "2026-05-01", "2026-05-29"
            )

        self.assertTrue(data.empty)
        self.assertEqual(successes, [])
        self.assertTrue(len(download_errors) >= 1)
        self.assertIn("boom", download_errors[0])

    def test_investor_flow_counts_transient_fetch_failures(self):
        # Network failures per day must be counted, not silently dropped.
        with patch("data_loader._fetch_twse_investor_flow", side_effect=RuntimeError("net")), patch(
            "data_loader._fetch_tpex_investor_flow", side_effect=RuntimeError("net")
        ):
            result = download_investor_flow_data(["2330"], "2026-05-05", lookback_days=5)

        self.assertTrue(result.empty)
        self.assertGreater(result.attrs.get("fetch_failures", 0), 0)
        self.assertEqual(
            result.attrs.get("fetch_failures"), result.attrs.get("fetch_attempts")
        )

    def test_attach_investor_flags_degrade_when_merge_fails(self):
        bars = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-25", "2026-05-26"]),
                "StockCode": ["2330.TW", "2330.TW"],
            }
        )
        flow = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-25"]),
                "BaseCode": ["2330"],
                "foreign_net": [10],
                "trust_net": [-5],
            }
        )
        with patch("signal_engine.pd.merge_asof", side_effect=ValueError("version")):
            result = attach_investor_flow_flags(bars, flow, consecutive_days=1)

        # Pipeline survives; flags degrade to False instead of aborting.
        self.assertFalse(bool(result["foreign_buy_streak_ok"].any()))

    def test_breakdown_sets_active_line_and_p3_reject(self):
        # Red line at 100 (bar1 red success); bar2 closes below it -> P3 reject.
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-01", "2026-05-04", "2026-05-05"]),
                "StockCode": ["2330.TW"] * 3,
                "Open": [100, 101, 104],
                "High": [101, 104, 104],
                "Low": [99, 100, 96],
                "Close": [100, 103, 98],
                "Volume": [1000, 1000, 1000],
            }
        )

        result = run_signal_pipeline(frame, {"lookback_bars": 10, "min_volume": 0})

        self.assertTrue(result.loc[2, "break_down_red_line"])
        self.assertFalse(result.loc[2, "break_red_line_daily"])
        self.assertEqual(result.loc[2, "active_breakdown_line_type"], "Red Line")
        self.assertEqual(result.loc[2, "active_breakdown_line_price"], 100)
        self.assertTrue(result.loc[2, "retest_reject_daily"])
        self.assertTrue(result.loc[2, "p3_break_down_reject"])

    def test_p3_reject_failure_when_close_above_line(self):
        # bar2 breaks the red line down (active breakdown line = 100); bar3 closes
        # above the line so it is NOT a reject even though the line is active.
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06"]),
                "StockCode": ["2330.TW"] * 4,
                "Open": [100, 101, 104, 97],
                "High": [101, 104, 104, 103],
                "Low": [99, 100, 96, 96],
                "Close": [100, 103, 98, 102],
                "Volume": [1000, 1000, 1000, 1000],
            }
        )

        result = run_signal_pipeline(frame, {"lookback_bars": 10, "min_volume": 0})

        self.assertEqual(result.loc[3, "active_breakdown_line_price"], 100)
        self.assertFalse(result.loc[3, "retest_reject_daily"])
        self.assertFalse(result.loc[3, "p3_break_down_reject"])

    def test_new_line_window_excludes_appearance_and_expires(self):
        # Red line appears at bar1; later attack-failure bars retest above it.
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(
                    ["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"]
                ),
                "StockCode": ["2330.TW"] * 5,
                "Open": [100, 101, 102, 103, 104],
                "High": [101, 104, 105, 106, 107],
                "Low": [99, 100, 99, 99, 99],
                "Close": [100, 103, 104, 105, 106],
                "Volume": [1000, 1000, 1000, 1000, 1000],
            }
        )

        result = run_signal_pipeline(frame, {"lookback_bars": 10, "min_volume": 0, "new_line_window": 2})

        # Appearance bar (bars_since == 0) is excluded from the window.
        self.assertEqual(result.loc[1, "bars_since_new_line"], 0)
        self.assertFalse(result.loc[1, "p2_new_line_hold"])
        # Inside the window (bars 1..2): P2 holds.
        self.assertEqual(result.loc[2, "bars_since_new_line"], 1)
        self.assertTrue(result.loc[2, "p2_new_line_hold"])
        self.assertTrue(result.loc[3, "p2_new_line_hold"])
        # Beyond the window (bars_since == 3 > 2): expired.
        self.assertEqual(result.loc[4, "bars_since_new_line"], 3)
        self.assertFalse(result.loc[4, "new_line_window_valid"])
        self.assertFalse(result.loc[4, "p2_new_line_hold"])

    def test_p4_new_line_reject_within_window(self):
        # Black line appears at bar1 (price below it); bar2 rejects below within window.
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-01", "2026-05-04", "2026-05-05"]),
                "StockCode": ["2330.TW"] * 3,
                "Open": [100, 98, 96],
                "High": [101, 101, 102],
                "Low": [99, 96, 95],
                "Close": [100, 97, 99],
                "Volume": [1000, 1000, 1000],
            }
        )

        result = run_signal_pipeline(frame, {"lookback_bars": 10, "min_volume": 0})

        self.assertEqual(result.loc[2, "bars_since_new_line"], 1)
        self.assertTrue(result.loc[2, "p4_new_line_reject"])
        # No downward break occurred (price was already below the line), so P3 stays off.
        self.assertFalse(result.loc[2, "p3_break_down_reject"])

    def test_prev_close_equal_to_line_is_breakout_not_breakdown(self):
        # bar3 has prev_close == prev red_line (both 100); a close above must be a
        # breakout, never a breakdown (§3.4 equality rule).
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06"]),
                "StockCode": ["2330.TW"] * 4,
                "Open": [100, 101, 99, 101],
                "High": [101, 104, 104, 105],
                "Low": [99, 100, 99, 99],
                "Close": [100, 103, 100, 104],
                "Volume": [1000, 1000, 1000, 1000],
            }
        )

        result = run_signal_pipeline(frame, {"lookback_bars": 10, "min_volume": 0})

        self.assertTrue(result.loc[3, "break_red_line_daily"])
        self.assertFalse(result.loc[3, "break_down_red_line"])
        # Up-break and down-break can never both fire on the same line/bar.
        self.assertFalse(bool((result["break_red_line_daily"] & result["break_down_red_line"]).any()))
        self.assertFalse(bool((result["break_black_line_daily"] & result["break_down_black_line"]).any()))

    def test_direction_signals_explode_into_multiple_rows(self):
        # bar2 satisfies both P3 (break-down reject) and P4 (new-line reject):
        # two short rows, no long rows, and no cross-direction dedup.
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-01", "2026-05-04", "2026-05-05"]),
                "StockCode": ["2330.TW"] * 3,
                "Open": [100, 101, 104],
                "High": [101, 104, 104],
                "Low": [99, 100, 96],
                "Close": [100, 103, 98],
                "Volume": [1000, 1000, 1000],
            }
        )
        processed = run_signal_pipeline(frame, {"lookback_bars": 10, "min_volume": 0})
        processed = attach_investor_flow_flags(processed, pd.DataFrame(), consecutive_days=3)

        bundle = build_direction_signals(processed, {"direction_filter": "全部"})
        long_signals = bundle["long_signals"]
        short_signals = bundle["short_signals"]

        self.assertTrue(long_signals.empty)
        self.assertEqual(len(short_signals), 2)
        self.assertEqual(
            set(short_signals["signal_type"]),
            {"P3_BreakDown_Reject", "P4_NewLine_Reject"},
        )
        self.assertEqual(set(short_signals["direction"]), {"Short"})

    def test_investor_filters_are_direction_aware(self):
        # A selected BUY streak must gate long signals only, never short signals.
        processed = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-05", "2026-05-05"]),
                "Timeframe": ["D", "D"],
                "StockCode": ["1111.TW", "2222.TW"],
                "StockName": ["1111.TW", "2222.TW"],
                "Open": [50, 60],
                "High": [51, 61],
                "Low": [49, 59],
                "Close": [50, 60],
                "Volume": [1000, 1000],
                "prev_close": [49, 61],
                "p1_final": [True, False],
                "p2_final": [False, False],
                "p3_final": [False, True],
                "p4_final": [False, False],
                "active_breakout_line_type": ["Red Line", None],
                "active_breakout_line_price": [50.0, float("nan")],
                "active_new_line_type": [None, None],
                "active_new_line_price": [float("nan"), float("nan")],
                "active_breakdown_line_type": [None, "Black Line"],
                "active_breakdown_line_price": [float("nan"), 60.0],
                "foreign_buy_streak_ok": [False, False],
                "trust_buy_streak_ok": [False, False],
                "foreign_sell_streak_ok": [False, False],
                "trust_sell_streak_ok": [False, False],
            }
        )

        bundle = build_direction_signals(
            processed,
            {"direction_filter": "全部", "foreign_buy_streak": True},
        )

        # Long P1 is filtered out (buy streak not satisfied)...
        self.assertTrue(bundle["long_signals"].empty)
        # ...but the short P3 survives because no sell filter was selected.
        self.assertEqual(len(bundle["short_signals"]), 1)
        self.assertEqual(bundle["short_signals"].loc[0, "signal_type"], "P3_BreakDown_Reject")


if __name__ == "__main__":
    unittest.main()
