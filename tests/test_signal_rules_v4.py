"""v4 篩選規則（issue #10）的使用者可見行為測試。

每個情境都用逐根手工建構的 OHLCV，只斷言使用者看得到的結果：哪一天、哪個方向、
哪條路徑、用哪條線，出現在訊號表或最新摘要。不斷言內部逐根輔助欄位。

建構慣例：開盤等於前一根收盤的 K 棒不構成紅攻／黑攻，不會產生新線。
"""

import contextlib
import unittest
from datetime import date
from io import BytesIO, StringIO

import pandas as pd

with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
    from app import _build_params, _compute_latest_summary, _filter_by_path
from chart_engine import create_stock_chart
from config import DEFAULT_PARAMETERS, EXCEL_SHEET_LABELS, LATEST_SUMMARY_COLUMNS, SIGNAL_COLUMNS
from export_engine import create_excel_bytes
from signal_engine import attach_investor_flow_flags, build_direction_signals, run_signal_pipeline

NEW_LINE = "新線路徑"
BREAKOUT = "突破回測路徑"


def _frame(bars, code="2330.TW"):
    """bars: list of (Open, High, Low, Close); one business day each from 2026-05-01."""
    dates = pd.bdate_range("2026-05-01", periods=len(bars))
    return pd.DataFrame(
        {
            "Date": dates,
            "StockCode": [code] * len(bars),
            "Open": [b[0] for b in bars],
            "High": [b[1] for b in bars],
            "Low": [b[2] for b in bars],
            "Close": [b[3] for b in bars],
            "Volume": [1000] * len(bars),
        }
    )


def _signals(frame, **params):
    """Run the whole engine and return (long_signals, short_signals)."""
    run_params = {"lookback_bars": 50, "min_volume": 0, **params}
    processed = run_signal_pipeline(frame, run_params)
    processed = attach_investor_flow_flags(processed, pd.DataFrame(), consecutive_days=3)
    bundle = build_direction_signals(processed, {"direction_filter": "全部", **params})
    return bundle["long_signals"], bundle["short_signals"]


def _rows(signals):
    """(bar index, signal_type, path, line type, line price) for each signal row."""
    start = pd.Timestamp("2026-05-01")
    dates = pd.bdate_range(start, periods=60)
    index_of = {d: i for i, d in enumerate(dates)}
    return sorted(
        (
            index_of[row.Date],
            row.signal_type,
            row.path,
            row.retest_line_type,
            float(row.retest_line_price),
        )
        for row in signals.itertuples(index=False)
    )


# 常用開場：b0 收 100；b1 紅攻成功（新紅線 100）或黑攻成功（新黑線 100）。
RED_LINE_100 = [(100, 100, 100, 100), (101, 103.5, 101, 103)]
BLACK_LINE_100 = [(100, 100, 100, 100), (99, 99, 96.5, 97)]


class NewLineColorDecidesDirectionTests(unittest.TestCase):
    def test_black_line_reclaimed_then_held_is_not_a_new_line_long(self):
        # 情境 1：新黑線 100 出現後，b2 收回線上（突破黑線），b3 回落守住。
        # 新黑線只找做空，所以新線路徑不產生做多；這個型態歸突破回測路徑（P1）。
        bars = BLACK_LINE_100 + [(97, 102, 97, 101), (101, 101.5, 99.8, 100.5)]

        long_signals, _ = _signals(_frame(bars))

        self.assertEqual(_rows(long_signals), [(3, "P1_BreakUp_Hold", BREAKOUT, "Black Line", 100.0)])

    def test_red_line_reclaimed_after_breakdown_then_rejected_is_not_a_new_line_short(self):
        # 情境 1 的做空鏡像：新紅線 100 後 b2 跌破，b3 反彈壓回，只算 P3。
        bars = RED_LINE_100 + [(103, 103, 98.5, 99), (99, 100.2, 98.8, 99.5)]

        _, short_signals = _signals(_frame(bars))

        self.assertEqual(
            _rows(short_signals), [(3, "P3_BreakDown_Reject", BREAKOUT, "Red Line", 100.0)]
        )

    def test_new_black_line_does_not_end_the_red_line_window(self):
        # 情境 2：紅線 100；第 1 天（b2）黑攻收 102（新黑線 103，收盤仍在紅線上）；
        # 第 2 天（b3）最低 99.8、收 100.5 -> P2 做多，用的是新紅線 100。
        bars = RED_LINE_100 + [(102.5, 102.8, 101.5, 102), (102, 102, 99.8, 100.5)]

        long_signals, _ = _signals(_frame(bars))

        self.assertEqual(_rows(long_signals), [(3, "P2_NewLine_Hold", NEW_LINE, "Red Line", 100.0)])

    def test_new_red_line_does_not_end_the_black_line_window(self):
        # 情境 2 的鏡像：黑線 100；b2 紅攻（新紅線 97，收盤仍在黑線下）；
        # b3 最高 100.2、收 99.5 -> P4 做空，用的是新黑線 100。
        bars = BLACK_LINE_100 + [(97.5, 98.5, 97.2, 98), (98, 100.2, 98, 99.5)]

        _, short_signals = _signals(_frame(bars))

        self.assertEqual(
            _rows(short_signals), [(3, "P4_NewLine_Reject", NEW_LINE, "Black Line", 100.0)]
        )

    def test_newer_red_line_replaces_the_older_one(self):
        # 情境 3：b1 新紅線 100，b2 再一次紅攻成功 -> 新紅線 105。b3 的下影線同時
        # 碰到 100 與 105、收 106：只能以最新的 105 產生 P2，舊線 100 不再追蹤。
        bars = [(100, 100, 100, 100), (101, 106, 101, 105), (106, 111, 106, 110), (110, 110, 99.9, 106)]

        long_signals, _ = _signals(_frame(bars))

        self.assertEqual(_rows(long_signals), [(3, "P2_NewLine_Hold", NEW_LINE, "Red Line", 105.0)])

    def test_close_below_new_red_line_ends_its_long_window(self):
        # 情境 4（v3.3.0 既有行為）：b2 收 99 破新紅線 100；b4 雖符合守住的每個
        # 條件，這條線已不再產生做多。b3 收回線上是突破，b4 因此只算 P1。
        bars = RED_LINE_100 + [(103, 103, 98.5, 99), (99, 101, 98.8, 100.5), (100.5, 101, 99.9, 100.2)]

        long_signals, _ = _signals(_frame(bars))

        self.assertEqual(_rows(long_signals), [(4, "P1_BreakUp_Hold", BREAKOUT, "Red Line", 100.0)])

    def test_close_above_new_black_line_ends_its_short_window(self):
        # 情境 4 的鏡像：b2 收 102 破新黑線 100；b4 符合壓回條件，只算 P3。
        bars = BLACK_LINE_100 + [(97, 102.5, 97, 102), (102, 102, 99.5, 99.8), (99.8, 100.1, 99, 99.6)]

        _, short_signals = _signals(_frame(bars))

        self.assertEqual(
            _rows(short_signals), [(4, "P3_BreakDown_Reject", BREAKOUT, "Black Line", 100.0)]
        )


class BrokenAfterSignalTests(unittest.TestCase):
    """情境 5：訊號後任何一根收盤破線，該訊號就不在訊號表；沒破線則保留。"""

    def _assert_kept_and_dropped(self, prefix, kept_bar, broken_bar, side, expected):
        kept = _frame(prefix + [kept_bar], code="1111.TW")
        broken = _frame(prefix + [broken_bar], code="2222.TW")

        long_signals, short_signals = _signals(pd.concat([kept, broken], ignore_index=True))
        signals = long_signals if side == "long" else short_signals

        self.assertEqual(_rows(signals[signals["StockCode"] == "1111.TW"]), [expected])
        self.assertEqual(_rows(signals[signals["StockCode"] == "2222.TW"]), [])

    def test_p1_removed_once_a_later_bar_closes_below_the_line(self):
        prefix = BLACK_LINE_100 + [(97, 102, 97, 101), (101, 101, 99.8, 100.5)]
        self._assert_kept_and_dropped(
            prefix,
            kept_bar=(100.5, 101, 100.2, 100.8),
            broken_bar=(100.5, 100.6, 99, 99.5),
            side="long",
            expected=(3, "P1_BreakUp_Hold", BREAKOUT, "Black Line", 100.0),
        )

    def test_p2_removed_once_a_later_bar_closes_below_the_line(self):
        prefix = RED_LINE_100 + [(103, 103, 99.8, 100.5)]
        self._assert_kept_and_dropped(
            prefix,
            kept_bar=(100.5, 101, 100.1, 100.8),
            broken_bar=(100.5, 100.6, 99, 99.5),
            side="long",
            expected=(2, "P2_NewLine_Hold", NEW_LINE, "Red Line", 100.0),
        )

    def test_p3_removed_once_a_later_bar_closes_above_the_line(self):
        prefix = RED_LINE_100 + [(103, 103, 98.5, 99), (99, 100.2, 98.8, 99.5)]
        self._assert_kept_and_dropped(
            prefix,
            kept_bar=(99.5, 99.8, 98, 99),
            broken_bar=(99.5, 101, 99.4, 100.5),
            side="short",
            expected=(3, "P3_BreakDown_Reject", BREAKOUT, "Red Line", 100.0),
        )

    def test_p4_removed_once_a_later_bar_closes_above_the_line(self):
        prefix = BLACK_LINE_100 + [(97, 100.2, 97, 99.5)]
        self._assert_kept_and_dropped(
            prefix,
            kept_bar=(99.5, 99.8, 98, 99),
            broken_bar=(99.5, 101, 99.4, 100.5),
            side="short",
            expected=(2, "P4_NewLine_Reject", NEW_LINE, "Black Line", 100.0),
        )

    def test_breach_long_after_the_signal_still_removes_it(self):
        # 破線不限於觀察窗內：P2 在 b2，觀察窗 2 根早已結束，b6 才收盤破線，
        # 訊號仍然失效（看到該股最新一根為止）。
        bars = RED_LINE_100 + [
            (103, 103, 99.8, 100.5),
            (100.5, 102, 100.5, 101.5),
            (101.5, 103, 101.5, 102.5),
            (102.5, 103, 101, 101.5),
            (101.5, 101.5, 99, 99.6),
        ]

        long_signals, _ = _signals(_frame(bars), new_line_window=2)

        self.assertEqual(_rows(long_signals), [])

    def test_breach_after_a_bar_with_missing_close_still_removes_it(self):
        # 中間一根缺收盤（NaN）不能遮住它之後的破線。
        bars = RED_LINE_100 + [
            (103, 103, 99.8, 100.5),
            (100.5, 101, 100.2, float("nan")),
            (100.8, 101, 99, 99.5),
        ]

        long_signals, _ = _signals(_frame(bars))

        self.assertEqual(_rows(long_signals), [])

    def test_latest_summary_excludes_signals_broken_afterwards(self):
        bars = RED_LINE_100 + [(103, 103, 99.8, 100.5), (100.5, 100.6, 99, 99.5)]

        long_signals, _ = _signals(_frame(bars))

        self.assertTrue(_compute_latest_summary(long_signals).empty)


class OpenCrossIsNotATestTests(unittest.TestCase):
    def test_reject_bar_that_opens_far_above_the_line_is_not_p4(self):
        # 情境 6（類似 6173 信昌電）：新黑線 100；b2 開盤 105.3（線上 5.3%）、
        # 收 96.5（線下 3.5%）是穿線不是測線 -> 不產生 P4。b3 開盤在線下、盤中
        # 碰線、收在線下，同一個觀察窗內再回測一次 -> P4。
        bars = BLACK_LINE_100 + [(105.3, 105.5, 96, 96.5), (96.5, 100.3, 96, 98)]

        _, short_signals = _signals(_frame(bars))

        self.assertEqual(
            _rows(short_signals), [(3, "P4_NewLine_Reject", NEW_LINE, "Black Line", 100.0)]
        )

    def test_open_within_tolerance_still_counts_short(self):
        # 情境 7：黑線 100；b2 開盤 100.7（線上 0.7%）、最高 101、收 99.5。
        bars = [(100, 100, 100, 100), (99.9, 100, 99.5, 99.8), (100.7, 101, 99, 99.5)]
        expected = [(2, "P4_NewLine_Reject", NEW_LINE, "Black Line", 100.0)]

        _, with_default = _signals(_frame(bars))
        _, with_one_pct = _signals(_frame(bars), open_cross_tolerance_pct=1.0)
        _, with_zero = _signals(_frame(bars), open_cross_tolerance_pct=0)

        self.assertEqual(_rows(with_default), expected)
        self.assertEqual(_rows(with_one_pct), expected)
        self.assertEqual(_rows(with_zero), [])

    def test_open_within_tolerance_still_counts_long(self):
        # 情境 7 的做多鏡像：紅線 100；b2 開盤 99.3（線下 0.7%）、最低 99、收 100.5。
        bars = [(100, 100, 100, 100), (100.1, 100.5, 100, 100.2), (99.3, 100.8, 99, 100.5)]
        expected = [(2, "P2_NewLine_Hold", NEW_LINE, "Red Line", 100.0)]

        long_default, _ = _signals(_frame(bars))
        long_zero, _ = _signals(_frame(bars), open_cross_tolerance_pct=0)

        self.assertEqual(_rows(long_default), expected)
        self.assertEqual(_rows(long_zero), [])

    def test_zero_tolerance_still_counts_an_open_exactly_on_the_line(self):
        # 容許度 0 時，開盤剛好在線上（或同一側）的 K 棒仍算測線。
        bars = [(100, 100, 100, 100), (100.1, 100.5, 100, 100.2), (100, 100.8, 99.5, 100.5)]

        long_signals, _ = _signals(_frame(bars), open_cross_tolerance_pct=0)

        self.assertEqual(_rows(long_signals), [(2, "P2_NewLine_Hold", NEW_LINE, "Red Line", 100.0)])

    def test_open_cross_bar_does_not_end_the_window(self):
        # 情境 8：紅線 100；b2 開盤 97（線下 3%）拉回收 100.5：不算守住，但收盤
        # 沒破線，觀察窗仍有效；b3 在窗內再回測一次 -> P2。
        bars = RED_LINE_100 + [(97, 101, 96.5, 100.5), (100.5, 101, 99.9, 100.3)]

        long_signals, _ = _signals(_frame(bars))

        self.assertEqual(_rows(long_signals), [(3, "P2_NewLine_Hold", NEW_LINE, "Red Line", 100.0)])

    def test_open_cross_bar_that_also_closes_through_ends_the_window(self):
        # 情境 8 的另一半：b2 開盤跳到線下、收盤也在線下（破線）-> 這條紅線不再
        # 產生做多。b3 收回線上是突破，b4 符合守住，只算 P1。
        bars = RED_LINE_100 + [(97, 100.5, 96.5, 99.5), (99.5, 101, 99.5, 100.8), (100.8, 101, 99.9, 100.4)]

        long_signals, _ = _signals(_frame(bars))

        self.assertEqual(_rows(long_signals), [(4, "P1_BreakUp_Hold", BREAKOUT, "Red Line", 100.0)])

    def test_p1_retest_bar_obeys_the_open_condition(self):
        # 情境 9：黑線 100，b2 突破；b3 開盤 98（線下 2%）拉回收 100.5：容許度 1%
        # 不算 P1，b4 再回測才算。容許度 3% 時 b3 就算。
        bars = BLACK_LINE_100 + [(97, 102, 97, 101), (98, 101, 97.5, 100.5), (100.5, 100.9, 99.9, 100.4)]

        long_default, _ = _signals(_frame(bars))
        long_wide, _ = _signals(_frame(bars), open_cross_tolerance_pct=3)

        self.assertEqual(_rows(long_default), [(4, "P1_BreakUp_Hold", BREAKOUT, "Black Line", 100.0)])
        self.assertIn((3, "P1_BreakUp_Hold", BREAKOUT, "Black Line", 100.0), _rows(long_wide))

    def test_p3_retest_bar_obeys_the_open_condition(self):
        # 情境 9 的鏡像：紅線 100，b2 跌破；b3 開盤 102（線上 2%）跌回收 99.5：
        # 不算 P3；b4 開盤在線下再回測 -> P3。
        bars = RED_LINE_100 + [(103, 103, 98.5, 99), (102, 102.5, 99, 99.5), (99.5, 100.1, 99, 99.6)]

        _, short_default = _signals(_frame(bars))
        _, short_wide = _signals(_frame(bars), open_cross_tolerance_pct=3)

        self.assertEqual(
            _rows(short_default), [(4, "P3_BreakDown_Reject", BREAKOUT, "Red Line", 100.0)]
        )
        self.assertIn((3, "P3_BreakDown_Reject", BREAKOUT, "Red Line", 100.0), _rows(short_wide))

    def test_default_tolerance_is_one_percent(self):
        # 未給參數時引擎用這個預設值（見上方各情境的 default 呼叫）。
        self.assertEqual(DEFAULT_PARAMETERS["open_cross_tolerance_pct"], 1.0)


class PathColumnTests(unittest.TestCase):
    def test_signal_tables_carry_a_path_column(self):
        self.assertIn("path", SIGNAL_COLUMNS)
        self.assertIn("Path", LATEST_SUMMARY_COLUMNS)

    def test_path_values_match_the_signal_type(self):
        # 情境 10：同一檔同時有 P1（突破回測）與 P2（新線）做多訊號。
        bars = RED_LINE_100 + [
            (103, 103, 98.5, 99),       # b2 破新紅線（P2 觀察窗失效）、跌破紅線
            (99, 101, 98.8, 100.5),     # b3 收回紅線上：突破事件
            (100.5, 101, 99.9, 100.2),  # b4 P1 守住
            (100.3, 103.5, 100.3, 103), # b5 紅攻成功：新紅線 100.2
            (103, 103, 99.9, 100.4),    # b6 守住新紅線 100.2（P2），也守住凍結線 100（P1）
        ]

        long_signals, _ = _signals(_frame(bars))

        paths = dict(zip(long_signals["signal_type"], long_signals["path"]))
        self.assertEqual(paths["P1_BreakUp_Hold"], BREAKOUT)
        self.assertEqual(paths["P2_NewLine_Hold"], NEW_LINE)


def _summary_input(rows):
    return pd.DataFrame(
        rows,
        columns=["Date", "StockCode", "signal_type", "path", "direction", "retest_line_type", "retest_line_price"],
    ).assign(Date=lambda df: pd.to_datetime(df["Date"]))


class LatestSummaryPerPathTests(unittest.TestCase):
    def test_one_row_per_stock_and_path_each_its_latest(self):
        signals = _summary_input(
            [
                ("2026-05-01", "2330.TW", "P2_NewLine_Hold", NEW_LINE, "Long", "Red Line", 98.0),
                ("2026-05-04", "2330.TW", "P1_BreakUp_Hold", BREAKOUT, "Long", "Black Line", 99.0),
                ("2026-05-05", "2330.TW", "P2_NewLine_Hold", NEW_LINE, "Long", "Red Line", 100.0),
            ]
        )

        summary = _compute_latest_summary(signals)

        by_path = summary.set_index("Path")
        self.assertEqual(len(summary), 2)
        self.assertEqual(by_path.loc[NEW_LINE, "LatestSignalDate"], pd.Timestamp("2026-05-05"))
        self.assertEqual(by_path.loc[NEW_LINE, "RetestLinePrice"], 100.0)
        self.assertEqual(by_path.loc[BREAKOUT, "LatestSignalDate"], pd.Timestamp("2026-05-04"))
        self.assertEqual(by_path.loc[BREAKOUT, "SignalType"], "P1_BreakUp_Hold")

    def test_path_column_is_in_the_summary(self):
        signals = _summary_input(
            [("2026-05-05", "2330.TW", "P2_NewLine_Hold", NEW_LINE, "Long", "Red Line", 100.0)]
        )

        summary = _compute_latest_summary(signals)

        self.assertEqual(summary.columns.tolist(), LATEST_SUMMARY_COLUMNS)
        self.assertEqual(summary.loc[0, "Path"], NEW_LINE)


class PathFilterTests(unittest.TestCase):
    def test_filter_keeps_only_the_selected_path(self):
        signals = _summary_input(
            [
                ("2026-05-05", "2330.TW", "P1_BreakUp_Hold", BREAKOUT, "Long", "Black Line", 99.0),
                ("2026-05-05", "2317.TW", "P2_NewLine_Hold", NEW_LINE, "Long", "Red Line", 100.0),
            ]
        )
        summary = _compute_latest_summary(signals)

        self.assertEqual(list(_filter_by_path(signals, NEW_LINE, "path")["StockCode"]), ["2317.TW"])
        self.assertEqual(list(_filter_by_path(summary, BREAKOUT, "Path")["StockCode"]), ["2330.TW"])
        self.assertEqual(len(_filter_by_path(signals, "全部", "path")), 2)

    def test_filter_tolerates_an_empty_frame(self):
        self.assertTrue(_filter_by_path(pd.DataFrame(), NEW_LINE, "path").empty)



class OutputSurfaceTests(unittest.TestCase):
    """路徑欄與新參數出現在使用者看得到的輸出：Excel、參數組裝、K 線圖。"""

    def _params(self, **overrides):
        params = {
            **DEFAULT_PARAMETERS,
            "start_date": date(2026, 5, 1),
            "end_date": date(2026, 5, 29),
            "min_volume": 0,
            "lookback_bars": 50,
        }
        params.update(overrides)
        return params

    def test_excel_signal_and_summary_sheets_carry_the_path_column(self):
        bars = RED_LINE_100 + [(102.5, 102.8, 101.5, 102), (102, 102, 99.8, 100.5)]
        params = self._params(open_cross_tolerance_pct=0.5)
        processed = attach_investor_flow_flags(run_signal_pipeline(_frame(bars), params), pd.DataFrame())
        bundle = build_direction_signals(processed, params)
        long_signals = bundle["long_signals"]

        data = create_excel_bytes(
            all_data=processed,
            long_signals=long_signals,
            short_signals=bundle["short_signals"],
            latest_summary_long=_compute_latest_summary(long_signals),
            latest_summary_short=_compute_latest_summary(bundle["short_signals"]),
            failed_list=[],
            params=params,
        )

        sheets = pd.read_excel(BytesIO(data), sheet_name=None)
        self.assertEqual(list(sheets), list(EXCEL_SHEET_LABELS.values()))
        self.assertEqual(list(sheets[EXCEL_SHEET_LABELS["Long_Signals"]]["路徑"]), [NEW_LINE])
        self.assertEqual(list(sheets[EXCEL_SHEET_LABELS["Latest_Summary_Long"]]["路徑"]), [NEW_LINE])
        settings = sheets[EXCEL_SHEET_LABELS["Parameter_Settings"]].set_index("參數")["設定值"]
        self.assertEqual(float(settings["開盤穿線容許度（%）"]), 0.5)

    def test_build_params_carries_the_tolerance_floored_at_zero(self):
        base = dict(
            start_date=date(2026, 5, 1), end_date=date(2026, 5, 29), analysis_timeframe="Daily K",
            lookback_bars=10, min_volume=0, new_line_window=5, retest_window=5, direction_filter="全部",
            investor_consecutive_days=3, foreign_buy_streak=False, trust_buy_streak=False,
            foreign_sell_streak=False, trust_sell_streak=False,
        )

        self.assertEqual(_build_params(**base, open_cross_tolerance_pct=1.5)["open_cross_tolerance_pct"], 1.5)
        self.assertEqual(_build_params(**base, open_cross_tolerance_pct=-1)["open_cross_tolerance_pct"], 0.0)

    def test_chart_marks_new_line_signals_with_the_line_they_used(self):
        # 新紅線 100 在 b3 守住（P2）；新黑線 103 在 b2 出現。圖上要有新線路徑的
        # 標記，且標記的提示看得出用的是哪條線與線價。
        bars = RED_LINE_100 + [(102.5, 102.8, 101.5, 102), (102, 102, 99.8, 100.5)]
        processed = run_signal_pipeline(_frame(bars), {"lookback_bars": 50, "min_volume": 0})

        figure, message = create_stock_chart(processed, timeframe_label="Daily K")

        self.assertIsNone(message)
        traces = {trace.name: trace for trace in figure.data}
        self.assertIn("新紅線守住（新線路徑）", traces)
        marker = traces["新紅線守住（新線路徑）"]
        self.assertEqual(list(marker.x), [pd.Timestamp("2026-05-06")])
        self.assertEqual(list(marker.customdata), [100.0])
        self.assertIn("新紅線", marker.hovertemplate)

    def test_chart_marks_new_black_line_rejects(self):
        bars = BLACK_LINE_100 + [(97, 100.2, 97, 99.5)]
        processed = run_signal_pipeline(_frame(bars), {"lookback_bars": 50, "min_volume": 0})

        figure, _ = create_stock_chart(processed, timeframe_label="Daily K")

        traces = {trace.name: trace for trace in figure.data}
        self.assertIn("新黑線壓回（新線路徑）", traces)
        self.assertEqual(list(traces["新黑線壓回（新線路徑）"].customdata), [100.0])


if __name__ == "__main__":
    unittest.main()
