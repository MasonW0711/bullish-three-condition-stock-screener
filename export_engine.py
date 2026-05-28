"""Excel export helpers for the stock screener."""

from __future__ import annotations

import io

import pandas as pd


def _sheet_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return df.copy()


def create_excel_bytes(all_data: pd.DataFrame, latest_result: pd.DataFrame, params: dict) -> bytes:
    """Create an in-memory Excel workbook containing the required sheets."""
    parameter_sheet = pd.DataFrame(
        {
            "Parameter": [
                "start_date",
                "end_date",
                "analysis_timeframe",
                "lookback_days",
                "min_gap_pct",
                "min_close_vs_prev_pct",
                "break_buffer_pct",
                "retest_tolerance_pct",
                "retest_break_pct",
                "volume_filter_mode",
                "min_volume_ratio_5",
                "min_volume_ratio_20",
                "min_daily_volume_lots",
                "min_score",
            ],
            "Value": [
                params["start_date"],
                params["end_date"],
                params["analysis_timeframe"],
                params["lookback_days"],
                params["min_gap_pct"],
                params["min_close_vs_prev_pct"],
                params["break_buffer_pct"],
                params["retest_tolerance_pct"],
                params["retest_break_pct"],
                params["volume_filter_mode"],
                params["min_volume_ratio_5"],
                params["min_volume_ratio_20"],
                params.get("min_daily_volume_lots", 0),
                params["min_score"],
            ],
        }
    )

    workbook_frames = {
        "All_Data": _sheet_frame(all_data),
        "Latest_Result": _sheet_frame(latest_result),
        "Final_Long_Signal": _sheet_frame(all_data[all_data["final_long_signal"].fillna(False)]),
        "Score_3": _sheet_frame(all_data[all_data["long_signal_score"] == 3]),
        "Score_2": _sheet_frame(all_data[all_data["long_signal_score"] == 2]),
        "Red_Attack_Success": _sheet_frame(all_data[all_data["red_attack_success"].fillna(False)]),
        "Black_Attack_Success": _sheet_frame(all_data[all_data["black_attack_success"].fillna(False)]),
        "Black_Attack_Failed": _sheet_frame(all_data[all_data["black_attack_failed"].fillna(False)]),
        "Parameter_Settings": parameter_sheet,
    }

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, frame in workbook_frames.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
    return output.getvalue()
