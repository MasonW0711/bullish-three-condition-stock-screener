"""Excel export helpers for the breakout-and-retest-hold stock screener."""

from __future__ import annotations

import io

import pandas as pd


def _sheet_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return df.copy()


def create_excel_bytes(
    all_data: pd.DataFrame,
    matching_retest_hold: pd.DataFrame,
    latest_summary: pd.DataFrame,
    failed_list: list[str],
    params: dict,
) -> bytes:
    """Create an in-memory Excel workbook with the required sheets."""
    parameter_sheet = pd.DataFrame(
        {
            "Parameter": [
                "start_date",
                "end_date",
                "analysis_timeframe",
                "min_volume",
                "lookback_bars",
                "investor_consecutive_days",
                "foreign_buy_streak",
                "trust_buy_streak",
                "foreign_sell_streak",
                "trust_sell_streak",
            ],
            "Value": [
                params["start_date"],
                params["end_date"],
                params["analysis_timeframe"],
                params["min_volume"],
                params["lookback_bars"],
                params.get("investor_consecutive_days", 3),
                params.get("foreign_buy_streak", False),
                params.get("trust_buy_streak", False),
                params.get("foreign_sell_streak", False),
                params.get("trust_sell_streak", False),
            ],
        }
    )

    workbook_frames = {
        "All_Data": _sheet_frame(all_data),
        "Matching_Retest_Hold": _sheet_frame(matching_retest_hold),
        "Latest_Summary": _sheet_frame(latest_summary),
        "Failed_Downloads": pd.DataFrame({"FailedStockCode": failed_list}),
        "Parameter_Settings": parameter_sheet,
    }

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, frame in workbook_frames.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
    return output.getvalue()
