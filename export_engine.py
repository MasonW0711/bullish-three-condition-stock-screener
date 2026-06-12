"""Excel export helpers for the breakout-and-retest-hold stock screener."""

from __future__ import annotations

import io

import pandas as pd

from config import (
    DISPLAY_COLUMN_LABELS,
    EXCEL_PARAMETER_LABELS,
    EXCEL_SHEET_LABELS,
    TIMEFRAME_LABELS,
)
from display_utils import booleans_to_chinese, sanitize_for_spreadsheet

# Excel（openpyxl）單一工作表上限為 1,048,576 列；超過會直接寫入失敗。
# 留一點餘裕並截斷，同時在診斷訊息中明確告知，不做無聲截斷。
EXCEL_MAX_ROWS_PER_SHEET = 1_000_000


def _localize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """以中文呈現匯出資料：週期名稱、布林值（是／否）與欄位標題。

    未列於 DISPLAY_COLUMN_LABELS 的技術欄位會保留原欄名。
    字串儲存格會先做公式注入消毒再寫入活頁簿。
    """
    if df is None or df.empty:
        return pd.DataFrame()
    frame = df.copy()
    if "Timeframe" in frame.columns:
        frame["Timeframe"] = frame["Timeframe"].map(TIMEFRAME_LABELS).fillna(frame["Timeframe"])
    frame = sanitize_for_spreadsheet(booleans_to_chinese(frame))
    return frame.rename(columns=DISPLAY_COLUMN_LABELS)


def _failed_downloads_frame(failed_list: list[str], download_notes: list[str]) -> pd.DataFrame:
    """Build the failed-downloads sheet with codes and batch-level diagnostics.

    The two columns describe different things (per-stock failures vs batch-level
    network/source errors), so they are padded to equal length and shown side by
    side rather than row-aligned.
    """
    codes = list(failed_list or [])
    notes = list(download_notes or [])
    height = max(len(codes), len(notes))
    return pd.DataFrame(
        {
            "失敗股票代號": codes + [""] * (height - len(codes)),
            "診斷訊息": notes + [""] * (height - len(notes)),
        }
    )


def create_excel_bytes(
    all_data: pd.DataFrame,
    long_signals: pd.DataFrame,
    short_signals: pd.DataFrame,
    latest_summary_long: pd.DataFrame,
    latest_summary_short: pd.DataFrame,
    failed_list: list[str],
    params: dict,
    download_notes: list[str] | None = None,
) -> bytes:
    """Create an in-memory Excel workbook with the v2 long/short sheets (§4.3)."""
    parameter_sheet = pd.DataFrame(
        {
            "參數": [
                EXCEL_PARAMETER_LABELS.get(key, key)
                for key in (
                    "start_date",
                    "end_date",
                    "analysis_timeframe",
                    "direction_filter",
                    "min_volume",
                    "lookback_bars",
                    "new_line_window",
                    "investor_consecutive_days",
                    "foreign_buy_streak",
                    "trust_buy_streak",
                    "foreign_sell_streak",
                    "trust_sell_streak",
                )
            ],
            "設定值": [
                params["start_date"],
                params["end_date"],
                params["analysis_timeframe"],
                params.get("direction_filter", "全部"),
                params["min_volume"],
                params["lookback_bars"],
                params.get("new_line_window", 5),
                params.get("investor_consecutive_days", 3),
                "是" if params.get("foreign_buy_streak", False) else "否",
                "是" if params.get("trust_buy_streak", False) else "否",
                "是" if params.get("foreign_sell_streak", False) else "否",
                "是" if params.get("trust_sell_streak", False) else "否",
            ],
        }
    )

    notes = list(download_notes or [])

    def _truncate_for_excel(df: pd.DataFrame, sheet_key: str) -> pd.DataFrame:
        if df is None or len(df) <= EXCEL_MAX_ROWS_PER_SHEET:
            return df
        sheet_label = EXCEL_SHEET_LABELS.get(sheet_key, sheet_key)
        notes.append(
            f"「{sheet_label}」資料共 {len(df)} 列，超過 Excel 工作表上限，"
            f"僅保留日期較新的 {EXCEL_MAX_ROWS_PER_SHEET} 列。完整資料請改用 CSV 或縮短日期區間。"
        )
        if "Date" in df.columns:
            # 保留最新日期的列，再還原原本的排序。
            return df.sort_values("Date").tail(EXCEL_MAX_ROWS_PER_SHEET).sort_index().reset_index(drop=True)
        return df.tail(EXCEL_MAX_ROWS_PER_SHEET).reset_index(drop=True)

    data_frames = {
        sheet_key: _localize_frame(_truncate_for_excel(frame, sheet_key))
        for sheet_key, frame in (
            ("All_Data", all_data),
            ("Long_Signals", long_signals),
            ("Short_Signals", short_signals),
            ("Latest_Summary_Long", latest_summary_long),
            ("Latest_Summary_Short", latest_summary_short),
        )
    }

    workbook_frames = {
        **data_frames,
        "Failed_Downloads": _failed_downloads_frame(failed_list, notes),
        "Parameter_Settings": parameter_sheet,
    }

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_key, frame in workbook_frames.items():
            sheet_name = EXCEL_SHEET_LABELS.get(sheet_key, sheet_key)
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
    return output.getvalue()
