"""Excel export helpers for the Big Red / Big Black Attack Stock Screener."""

from __future__ import annotations

import io

import pandas as pd


def _sheet_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return df.copy()


def create_excel_bytes(
    all_data: pd.DataFrame,
    matching_signals: pd.DataFrame,
    latest_summary: pd.DataFrame,
    three_methods_bullish: pd.DataFrame,
    three_methods_bearish: pd.DataFrame,
    failed_list: list[str],
    params: dict,
) -> bytes:
    """Create an in-memory Excel workbook with seven sheets."""
    parameter_sheet = pd.DataFrame(
        {
            "Parameter": [
                "start_date",
                "end_date",
                "analysis_timeframe",
                "min_volume",
                "lookback_bars",
                "min_conditions",
                "pullback_pct",
                "foreign_buy_3d",
                "trust_buy_3d",
                "foreign_sell_3d",
                "trust_sell_3d",
            ],
            "Value": [
                params["start_date"],
                params["end_date"],
                params["analysis_timeframe"],
                params["min_volume"],
                params["lookback_bars"],
                params.get("min_conditions", 2),
                params.get("pullback_pct", 2.0),
                params.get("foreign_buy_3d", False),
                params.get("trust_buy_3d", False),
                params.get("foreign_sell_3d", False),
                params.get("trust_sell_3d", False),
            ],
        }
    )

    failed_sheet = pd.DataFrame({"FailedStockCode": failed_list})

    workbook_frames = {
        "All_Data": _sheet_frame(all_data),
        "Matching_Signals": _sheet_frame(matching_signals),
        "Latest_Summary": _sheet_frame(latest_summary),
        "ThreeMethods_Bullish": _sheet_frame(three_methods_bullish),
        "ThreeMethods_Bearish": _sheet_frame(three_methods_bearish),
        "Failed_Downloads": failed_sheet,
        "Parameter_Settings": parameter_sheet,
    }

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, frame in workbook_frames.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
    return output.getvalue()
