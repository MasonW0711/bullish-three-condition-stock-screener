"""Regression: re-running the same screen must reuse the cached download.

Up to v3.3.0 the progress callback (which writes to sidebar widgets created
outside the cached function) ran inside ``st.cache_data``. Streamlit recorded
those widget calls and, on the next identical run, tried to replay them onto
widgets it could not find, so every same-condition re-run failed with
「股票資料下載失敗」.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
MANUAL_CODES = ["2603.TW", "2382.TW"]


def _daily(codes):
    dates = pd.bdate_range("2026-01-01", periods=40)
    frames = []
    for i, code in enumerate(codes):
        close = 100 + pd.Series(range(40)) * 0.3 + i
        frames.append(
            pd.DataFrame(
                {
                    "Date": dates,
                    "StockCode": code,
                    "Open": close - 0.5,
                    "High": close + 1.0,
                    "Low": close - 1.0,
                    "Close": close,
                    "Volume": 5_000_000,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


class StockDownloadCacheReplayTests(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)
        self.download_calls = []
        self.progress_messages = []
        self.download_error = None
        self.batch_errors = []
        for target, kwargs in (
            ("data_loader.download_stock_data", {"side_effect": self._fake_download}),
            ("data_loader.load_taiwan_stock_universe", {"return_value": pd.DataFrame()}),
            ("data_loader.download_investor_flow_data", {"return_value": pd.DataFrame()}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fake_download(self, stock_codes, start_date, end_date, progress_callback=None, cache_dir=None):
        self.download_calls.append(tuple(stock_codes))
        if self.download_error is not None:
            raise self.download_error
        if progress_callback is not None:
            progress_callback(0.5, "正在下載第 1/1 批股價資料...")
            self.progress_messages.append("batch")
        return _daily(stock_codes), list(stock_codes), [], list(self.batch_errors)

    def _manual_screen_app(self):
        app_test = AppTest.from_file(str(APP_PATH), default_timeout=30)
        app_test.run()
        auto_universe = next(cb for cb in app_test.checkbox if cb.label.startswith("自動抓取"))
        auto_universe.uncheck().run()
        app_test.text_area[0].input("\n".join(MANUAL_CODES)).run()
        return app_test

    def _press_run(self, app_test):
        next(button for button in app_test.button if button.label == "開始篩選").click().run()
        self.assertEqual(list(app_test.exception), [])

    def test_same_condition_rerun_hits_cache_and_succeeds(self):
        app_test = self._manual_screen_app()

        self._press_run(app_test)
        self.assertEqual([e.value for e in app_test.error], [])
        self.assertEqual(app_test.session_state["screening_results"]["success_list"], MANUAL_CODES)
        self.assertEqual(len(self.download_calls), 1)
        self.assertEqual(self.progress_messages, ["batch"], "first run must still report download progress")

        self._press_run(app_test)
        self.assertEqual([e.value for e in app_test.error], [])
        self.assertEqual(app_test.session_state["screening_results"]["success_list"], MANUAL_CODES)
        self.assertEqual(len(self.download_calls), 1, "identical re-run must be served from the cache")

    def test_new_session_with_same_condition_hits_cache(self):
        self._press_run(self._manual_screen_app())

        second_session = self._manual_screen_app()
        self._press_run(second_session)

        self.assertEqual([e.value for e in second_session.error], [])
        self.assertEqual(second_session.session_state["screening_results"]["success_list"], MANUAL_CODES)
        self.assertEqual(len(self.download_calls), 1)

    def test_transient_batch_errors_are_evicted_so_rerun_downloads_again(self):
        self.batch_errors = ["第 1 批：逾時"]
        app_test = self._manual_screen_app()

        self._press_run(app_test)
        self._press_run(app_test)

        self.assertEqual(len(self.download_calls), 2)

    def test_failed_download_does_not_claim_everything_downloaded(self):
        self.download_error = RuntimeError("boom")
        app_test = self._manual_screen_app()

        self._press_run(app_test)

        self.assertIn("股票資料下載失敗：boom", [e.value for e in app_test.error])
        self.assertNotIn("所有要求的股票代號都已成功下載。", [s.value for s in app_test.success])


if __name__ == "__main__":
    unittest.main()
