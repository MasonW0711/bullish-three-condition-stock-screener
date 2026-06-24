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
    _fetch_twse_investor_flow,
    _locate_investor_net_columns,
    _select_isin_table,
    _to_int,
    _validate_tpex_net_columns,
    download_investor_flow_data,
    download_stock_data,
    load_stock_list_from_upload,
    normalize_yfinance_data,
    resample_ohlcv,
)
from display_utils import booleans_to_chinese, sanitize_for_spreadsheet
from export_engine import create_excel_bytes
from config import EXCEL_SHEET_LABELS
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

    def test_investor_streak_does_not_bridge_a_missing_trading_day(self):
        # 2026-05-06 is a trading day for other stocks but missing for 2330 (its
        # fetch failed, or it did not trade). A 3-day streak must NOT be inferred
        # from only the 05-04, 05-05, 05-07 rows: that bridges the 05-06 gap.
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
                "Date": pd.to_datetime(
                    ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"]
                ),
                "BaseCode": ["2330", "2330", "9999", "2330"],  # 05-06 belongs to another stock
                "foreign_net": [1, 1, 1, 1],
                "trust_net": [1, 1, 1, 1],
            }
        )

        result = attach_investor_flow_flags(bars, flow, consecutive_days=3)

        self.assertFalse(result.loc[0, "foreign_buy_streak_ok"])
        self.assertFalse(result.loc[0, "trust_buy_streak_ok"])

    def test_investor_streak_holds_across_full_consecutive_days(self):
        # Sanity check the day-aware path still confirms a genuine 3-day streak
        # when every trading day in range is present for the stock.
        bars = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-06"]),
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
                "trust_net": [-1, -1, -1],
            }
        )

        result = attach_investor_flow_flags(bars, flow, consecutive_days=3)

        self.assertTrue(result.loc[0, "foreign_buy_streak_ok"])
        self.assertTrue(result.loc[0, "trust_sell_streak_ok"])
        self.assertFalse(result.loc[0, "foreign_sell_streak_ok"])

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

    def test_long_retest_failure_is_not_a_long_signal(self):
        # bar2 breaks UP through the black line at 100; bar3 closes back below it
        # (97 < 100). The long P1 hold fails (close below the line), so there is
        # no LONG signal — but closing back below a line you broke above is a
        # genuine P3 breakdown-reject (short), so the bar IS a short final signal.
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
        self.assertFalse(result.loc[3, "p1_final"])
        # Bar 3 is itself a fresh new-line appearance (bars_since == 0), so P4 is
        # excluded on the appearance bar.
        self.assertEqual(result.loc[3, "bars_since_new_line"], 0)
        self.assertFalse(result.loc[3, "p4_new_line_reject"])
        # It IS a genuine P3 breakdown-reject of the black line at 100.
        self.assertTrue(result.loc[3, "break_down_black_line"])
        self.assertTrue(result.loc[3, "p3_break_down_reject"])
        self.assertTrue(result.loc[3, "final_signal"])

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
        # Dash placeholders genuinely mean "no value reported" -> 0.
        self.assertEqual(_to_int("--"), 0)
        # Unparseable garbage is a data anomaly -> None (logged upstream),
        # not a silent zero that would break consecutive-day streaks.
        self.assertIsNone(_to_int("not-a-number"))

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

    def test_sanitize_for_spreadsheet_escapes_formula_prefixes(self):
        frame = pd.DataFrame(
            {
                "StockCode": ["=cmd|' /C calc'!A0", "2330.TW", "@SUM(1,2)"],
                "Close": [100.5, 200.0, 300.0],
            }
        )

        result = sanitize_for_spreadsheet(frame)

        self.assertEqual(result.loc[0, "StockCode"], "'=cmd|' /C calc'!A0")
        self.assertEqual(result.loc[1, "StockCode"], "2330.TW")
        self.assertEqual(result.loc[2, "StockCode"], "'@SUM(1,2)")
        # Numeric columns are untouched.
        self.assertEqual(result.loc[0, "Close"], 100.5)

    def test_booleans_to_chinese_skips_integer_columns(self):
        frame = pd.DataFrame({"flag": [True, False], "count": [0, 1]})

        result = booleans_to_chinese(frame)

        self.assertEqual(result["flag"].tolist(), ["是", "否"])
        self.assertEqual(result["count"].tolist(), [0, 1])

    def test_empty_latest_summary_keeps_export_schema(self):
        latest_summary = _compute_latest_summary(pd.DataFrame())

        self.assertEqual(latest_summary.columns.tolist(), LATEST_SUMMARY_COLUMNS)

    def test_latest_summary_prefers_breakout_path_on_same_bar(self):
        # Same stock, same date, two long paths: P1 must win regardless of
        # the row order produced by the explode step.
        signals = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-05", "2026-05-05"]),
                "StockCode": ["2330.TW", "2330.TW"],
                "signal_type": ["P2_NewLine_Hold", "P1_BreakUp_Hold"],
                "direction": ["Long", "Long"],
                "retest_line_type": ["Red Line", "Black Line"],
                "retest_line_price": [100.0, 99.0],
            }
        )

        summary = _compute_latest_summary(signals)

        self.assertEqual(len(summary), 1)
        self.assertEqual(summary.loc[0, "SignalType"], "P1_BreakUp_Hold")

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

    def test_investor_net_columns_located_by_field_name_not_position(self):
        # An inserted column must not shift which values are read.
        fields = [
            "證券代號",
            "證券名稱",
            "新插入的欄位",
            "外陸資買進股數(不含外資自營商)",
            "外陸資賣出股數(不含外資自營商)",
            "外陸資買賣超股數(不含外資自營商)",
            "外資自營商買進股數",
            "外資自營商賣出股數",
            "外資自營商買賣超股數",
            "投信買進股數",
            "投信賣出股數",
            "投信買賣超股數",
        ]

        foreign_index, trust_index = _locate_investor_net_columns(fields)

        self.assertEqual(fields[foreign_index], "外陸資買賣超股數(不含外資自營商)")
        self.assertEqual(fields[trust_index], "投信買賣超股數")

    def test_investor_net_columns_missing_raises_clear_error(self):
        # If the source renames the columns beyond recognition the fetch must
        # fail loudly (counted as fetch failure) instead of reading index 4.
        fields = ["證券代號", "證券名稱", "改名後的欄位A", "改名後的欄位B"]

        with self.assertRaisesRegex(ValueError, "無法定位外資／投信買賣超欄位"):
            _locate_investor_net_columns(fields)

    def test_twse_investor_flow_reads_columns_named_in_payload(self):
        payload = {
            "stat": "OK",
            "fields": [
                "證券代號",
                "證券名稱",
                "外陸資買進股數(不含外資自營商)",
                "外陸資賣出股數(不含外資自營商)",
                "外資自營商買賣超股數",
                "外陸資買賣超股數(不含外資自營商)",
                "投信買進股數",
                "投信賣出股數",
                "投信買賣超股數",
                "自營商買賣超股數",
                "三大法人買賣超股數",
            ],
            "data": [
                ["2330", "台積電", "100", "50", "999", "1,234", "30", "10", "(20)", "0", "0"],
            ],
        }

        class _StubResponse:
            def json(self):
                return payload

        with patch("data_loader._get_with_ssl_fallback", return_value=_StubResponse()):
            result = _fetch_twse_investor_flow(pd.Timestamp("2026-05-05"))

        # foreign_net comes from the named column (index 5), not legacy index 4.
        self.assertEqual(result.loc[0, "foreign_net"], 1234)
        self.assertEqual(result.loc[0, "trust_net"], -20)

    @staticmethod
    def _tpex_generic_fields() -> list:
        return (
            ["代號", "名稱"]
            + ["買進股數", "賣出股數", "買賣超股數"] * 7
            + ["三大法人買賣超股數合計"]
        )

    def test_tpex_generic_layout_validates_and_returns_known_indexes(self):
        fields = self._tpex_generic_fields()
        # foreign excl dealer net = 100-40=60 (col 4); trust net = 30-10=20 (col 13).
        row = ["5483", "中美晶", "100", "40", "60", "0", "0", "0", "100", "40", "60",
               "30", "10", "20", "0", "0", "0", "5", "1", "4", "5", "1", "4", "84"]
        frame = pd.DataFrame([row])

        foreign_index, trust_index = _validate_tpex_net_columns(fields, frame)

        self.assertEqual((foreign_index, trust_index), (4, 13))

    def test_tpex_shifted_columns_fail_arithmetic_validation(self):
        fields = self._tpex_generic_fields()
        # Shift values by one column: net != buy - sell everywhere.
        row = ["5483", "中美晶", "999", "100", "40", "60", "0", "0", "0", "100", "40",
               "60", "30", "10", "20", "0", "0", "0", "5", "1", "4", "5", "1", "84"]
        frame = pd.DataFrame([row])

        with self.assertRaisesRegex(ValueError, "買賣超＝買進－賣出"):
            _validate_tpex_net_columns(fields, frame)

    def test_tpex_unexpected_field_count_raises(self):
        fields = self._tpex_generic_fields() + ["新增欄位"]
        frame = pd.DataFrame([["5483"] + ["0"] * 24])

        with self.assertRaisesRegex(ValueError, "欄位數改變"):
            _validate_tpex_net_columns(fields, frame)

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

    def test_new_lower_red_line_does_not_fake_a_breakout(self):
        # A new red line forms BELOW the still-active old line on bar 4. The close
        # sits between the two lines (above the new line, below the old one). That
        # must NOT be reported as a breakout: prev-close-vs-old-line and
        # close-vs-new-line would otherwise compare two different lines (§3.4a).
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(
                    ["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"]
                ),
                "StockCode": ["X"] * 5,
                "Open": [100, 101, 99, 97, 98.5],
                "High": [101, 104, 100, 99, 100],
                "Low": [99, 100, 96, 96, 97],
                "Close": [100, 103, 98, 97.5, 99.5],
                "Volume": [1000] * 5,
            }
        )

        result = run_signal_pipeline(frame, {"lookback_bars": 10, "min_volume": 0})

        # Bar 4 creates a new red line at 97.5; close 99.5 is still below the old
        # line (100), so no genuine breakout occurred.
        self.assertTrue(result.loc[4, "red_attack_success"])
        self.assertFalse(result.loc[4, "break_red_line_daily"])

    def test_new_higher_black_line_does_not_fake_a_breakdown(self):
        # Mirror of the breakout case. An old black line sits at 100; price rises,
        # then bar 3 forms a NEW black line at 105 (a higher level). Its close
        # (103) is below the new line only because the line just moved up — it is
        # still above the old line, so this is not a genuine breakdown (§3.4b).
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(
                    ["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06"]
                ),
                "StockCode": ["X"] * 4,
                "Open": [100, 98, 99, 104],
                "High": [101, 99, 106, 106],
                "Low": [99, 95, 98, 102],
                "Close": [100, 96, 105, 103],
                "Volume": [1000] * 4,
            }
        )

        result = run_signal_pipeline(frame, {"lookback_bars": 10, "min_volume": 0})

        # Bar 3 moves the black line up to 105 (a fresh black attack success).
        self.assertTrue(result.loc[3, "black_attack_success"])
        self.assertEqual(result.loc[3, "black_line"], 105)
        self.assertFalse(result.loc[3, "break_down_black_line"])

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


    def test_weekly_resample_then_pipeline_detects_weekly_attack(self):
        # End-to-end: daily -> weekly resample -> signal pipeline. The signal
        # logic must run on the WEEKLY bars (never on daily then resampled).
        # Week 1 closes at 100; week 2 opens above 100 and closes above 100 ->
        # a weekly big-red-attack success that creates a red line at 100.
        daily = pd.DataFrame(
            {
                "Date": pd.to_datetime(
                    [
                        # Week 1 (ends Fri 2026-05-08)
                        "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08",
                        # Week 2 (ends Fri 2026-05-15)
                        "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14", "2026-05-15",
                    ]
                ),
                "StockCode": ["2330.TW"] * 10,
                "Open": [98, 99, 100, 99, 98, 105, 106, 107, 108, 109],
                "High": [99, 101, 101, 100, 101, 112, 113, 114, 115, 116],
                "Low": [97, 98, 99, 98, 97, 104, 105, 106, 107, 108],
                "Close": [99, 100, 100, 99, 100, 108, 109, 110, 111, 112],
                "Volume": [1000] * 10,
            }
        )

        weekly = resample_ohlcv(daily, "W")
        result = run_signal_pipeline(weekly, {"lookback_bars": 10, "min_volume": 0})

        # Two weekly bars; week 2 is the red attack success at prev weekly close 100.
        self.assertEqual(len(result), 2)
        self.assertEqual(result.loc[1, "prev_close"], 100)
        self.assertTrue(result.loc[1, "red_attack_success"])
        self.assertEqual(result.loc[1, "red_line"], 100)

    def test_create_excel_bytes_has_all_localized_sheets(self):
        signals = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-05"]),
                "Timeframe": ["D"],
                "StockCode": ["2330.TW"],
                "StockName": ["台積電"],
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Volume": [1000],
                "prev_close": [99.5],
                "direction": ["Long"],
                "signal_type": ["P1_BreakUp_Hold"],
                "retest_line_type": ["Red Line"],
                "retest_line_price": [100.0],
                "foreign_buy_streak_ok": [True],
                "trust_buy_streak_ok": [False],
                "foreign_sell_streak_ok": [False],
                "trust_sell_streak_ok": [False],
            }
        )
        params = {
            "start_date": "2026-05-01",
            "end_date": "2026-05-29",
            "analysis_timeframe": "Daily K",
            "direction_filter": "全部",
            "min_volume": 2000,
            "lookback_bars": 10,
        }

        data = create_excel_bytes(
            all_data=signals,
            long_signals=signals,
            short_signals=pd.DataFrame(),
            latest_summary_long=pd.DataFrame(),
            latest_summary_short=pd.DataFrame(),
            failed_list=["1234.TW"],
            params=params,
            download_notes=["第 1 批下載失敗：timeout"],
        )

        self.assertTrue(data.startswith(b"PK"))  # valid xlsx (zip) container
        sheets = pd.read_excel(BytesIO(data), sheet_name=None)
        for label in EXCEL_SHEET_LABELS.values():
            self.assertIn(label, sheets)
        # The failed-downloads sheet carries both the code and the batch note.
        failed_sheet = sheets[EXCEL_SHEET_LABELS["Failed_Downloads"]]
        self.assertIn("1234.TW", failed_sheet.to_string())
        self.assertIn("timeout", failed_sheet.to_string())

    def test_failed_downloads_sheet_escapes_formula_injection(self):
        # A user-supplied "stock code" that is a spreadsheet formula must be
        # neutralized in the Failed_Downloads sheet, not just the signal sheets.
        params = {
            "start_date": "2026-05-01",
            "end_date": "2026-05-29",
            "analysis_timeframe": "Daily K",
            "min_volume": 0,
            "lookback_bars": 5,
        }
        data = create_excel_bytes(
            all_data=pd.DataFrame(),
            long_signals=pd.DataFrame(),
            short_signals=pd.DataFrame(),
            latest_summary_long=pd.DataFrame(),
            latest_summary_short=pd.DataFrame(),
            failed_list=["=cmd|'/c calc'!A1", "@SUM(1,2)", "2330.TW"],
            params=params,
            download_notes=["=HYPERLINK('http://evil','x')"],
        )
        failed_sheet = pd.read_excel(
            BytesIO(data), sheet_name=EXCEL_SHEET_LABELS["Failed_Downloads"], dtype=str
        )
        cells = failed_sheet.fillna("").to_numpy().ravel().tolist()
        # Every formula-leading cell is prefixed with ' (rendered as plain text).
        self.assertIn("'=cmd|'/c calc'!A1", cells)
        self.assertIn("'@SUM(1,2)", cells)
        self.assertIn("'=HYPERLINK('http://evil','x')", cells)
        # A benign code is untouched.
        self.assertIn("2330.TW", cells)

    def test_create_excel_bytes_handles_all_empty_frames(self):
        # A run with no signals must still produce a valid workbook with every
        # sheet, not raise.
        params = {
            "start_date": "2026-05-01",
            "end_date": "2026-05-29",
            "analysis_timeframe": "Weekly K",
            "min_volume": 0,
            "lookback_bars": 5,
        }
        data = create_excel_bytes(
            all_data=pd.DataFrame(),
            long_signals=pd.DataFrame(),
            short_signals=pd.DataFrame(),
            latest_summary_long=pd.DataFrame(),
            latest_summary_short=pd.DataFrame(),
            failed_list=[],
            params=params,
        )
        sheets = pd.read_excel(BytesIO(data), sheet_name=None)
        for label in EXCEL_SHEET_LABELS.values():
            self.assertIn(label, sheets)

    def test_upward_gap_breakout_on_attack_success_fires_p1(self):
        # Regression: a genuine breakout of a stable line (100) that ALSO is a
        # red-attack-success on the same bar (which moves red_line below 100)
        # must still register as a breakout and seed the P1 retest-hold path.
        # The old line-equality guard wrongly dropped it.
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(
                    ["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"]
                ),
                "StockCode": ["2330.TW"] * 5,
                "Open": [100, 101, 104, 102, 105],
                "High": [101, 104, 105, 111, 106],
                "Low": [99, 100, 97, 99, 99],
                "Close": [100, 103, 98, 110, 104],
                "Volume": [1000, 1000, 1000, 3000, 3000],
            }
        )

        result = run_signal_pipeline(frame, {"lookback_bars": 5, "min_volume": 2000})

        # bar3 is a red-attack-success that moves red_line to 98 (below the old line)...
        self.assertTrue(result.loc[3, "red_attack_success"])
        self.assertEqual(result.loc[3, "red_line"], 98)
        # ...yet close 110 clears the OLD line at 100: a genuine breakout against it.
        self.assertTrue(result.loc[3, "break_red_line_daily"])
        self.assertEqual(result.loc[3, "active_breakout_line_price"], 100)
        # bar4 holds above the broken line -> P1 final signal.
        self.assertTrue(result.loc[4, "retest_hold_daily"])
        self.assertTrue(result.loc[4, "p1_break_up_hold"])
        self.assertTrue(result.loc[4, "final_signal"])

    def test_downward_gap_breakdown_on_attack_success_fires_p3(self):
        # Mirror of the breakout case: a genuine breakdown of a stable black line
        # (100) that ALSO is a black-attack-success (which moves black_line above
        # 100) must still register as a breakdown and seed the P3 reject path.
        frame = pd.DataFrame(
            {
                "Date": pd.to_datetime(
                    ["2026-05-01", "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"]
                ),
                "StockCode": ["2330.TW"] * 5,
                "Open": [100, 99, 96, 99, 95],
                "High": [101, 100, 102, 100, 101],
                "Low": [99, 96, 95, 91, 98],
                "Close": [100, 97, 101, 92, 99],
                "Volume": [1000, 1000, 1000, 1000, 1000],
            }
        )

        result = run_signal_pipeline(frame, {"lookback_bars": 10, "min_volume": 0})

        self.assertTrue(result.loc[3, "black_attack_success"])
        self.assertEqual(result.loc[3, "black_line"], 101)
        self.assertTrue(result.loc[3, "break_down_black_line"])
        self.assertEqual(result.loc[3, "active_breakdown_line_price"], 100)
        self.assertTrue(result.loc[4, "retest_reject_daily"])
        self.assertTrue(result.loc[4, "p3_break_down_reject"])
        self.assertTrue(result.loc[4, "final_signal"])

    def test_investor_streak_single_stock_uses_market_trading_day_axis(self):
        # A single screened stock is missing 2026-05-06, which IS a market trading
        # day (passed via market_trading_days). The 3-day streak must break across
        # that gap even though no other stock is present to keep the day on the
        # axis — otherwise a small screen silently bridges gaps (false streak).
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
                "Date": pd.to_datetime(["2026-05-04", "2026-05-05", "2026-05-07"]),
                "BaseCode": ["2330"] * 3,
                "foreign_net": [1, 1, 1],
                "trust_net": [1, 1, 1],
            }
        )
        market_days = pd.to_datetime(
            ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"]
        )

        result = attach_investor_flow_flags(
            bars, flow, consecutive_days=3, market_trading_days=market_days
        )

        self.assertFalse(result.loc[0, "foreign_buy_streak_ok"])
        self.assertFalse(result.loc[0, "trust_buy_streak_ok"])

    def test_direction_filter_short_only_suppresses_long_side(self):
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

        bundle = build_direction_signals(processed, {"direction_filter": "做空"})

        self.assertTrue(bundle["long_signals"].empty)
        self.assertFalse(bundle["short_signals"].empty)

    def test_direction_filter_long_only_suppresses_short_side(self):
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
        processed = run_signal_pipeline(frame, {"lookback_bars": 3, "min_volume": 2000})
        processed = attach_investor_flow_flags(processed, pd.DataFrame(), consecutive_days=3)

        bundle = build_direction_signals(processed, {"direction_filter": "做多"})

        self.assertFalse(bundle["long_signals"].empty)
        self.assertTrue(bundle["short_signals"].empty)

    def test_latest_summary_prefers_breakdown_path_on_same_bar_short(self):
        # Short mirror of the P1>P2 tie-break: on the same bar P3 must win over P4.
        signals = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-05-05", "2026-05-05"]),
                "StockCode": ["2330.TW", "2330.TW"],
                "signal_type": ["P4_NewLine_Reject", "P3_BreakDown_Reject"],
                "direction": ["Short", "Short"],
                "retest_line_type": ["Red Line", "Black Line"],
                "retest_line_price": [100.0, 99.0],
            }
        )

        summary = _compute_latest_summary(signals)

        self.assertEqual(len(summary), 1)
        self.assertEqual(summary.loc[0, "SignalType"], "P3_BreakDown_Reject")


if __name__ == "__main__":
    unittest.main()
