"""Signal calculation engine — detects Big Red Attack and Big Black Attack signals only.

Attack direction is determined ONLY by Open vs prev_close.
A failed attack is NEVER converted to the opposite-side attack.

Validation examples:
  prev=100, Open=105, Close=110 → red_attack_success=True, all others False
  prev=100, Open=105, Close=98  → red_attack_failed=True,  black_attack_success=False (!)
  prev=100, Open=95,  Close=90  → black_attack_success=True, all others False
  prev=100, Open=95,  Close=103 → black_attack_failed=True,  red_attack_success=False (!)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_prev_close(df: pd.DataFrame) -> pd.DataFrame:
    """Add grouped prev_close = previous K-bar close, per StockCode."""
    output = df.sort_values(["StockCode", "Date"]).copy()
    output["prev_close"] = output.groupby("StockCode")["Close"].shift(1)
    return output


def add_attack_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Detect Big Red / Big Black attack signals using explicit boolean masks.

    Each of the four signals is calculated independently.
    A failed attack is NOT converted to an opposite-side attack.
    """
    output = df.copy()

    has_prev = output["prev_close"].notna()
    opens_above = output["Open"] > output["prev_close"]
    opens_below = output["Open"] < output["prev_close"]
    closes_above = output["Close"] > output["prev_close"]
    closes_below = output["Close"] < output["prev_close"]

    # Each signal is a strictly independent boolean mask.
    output["red_attack_success"] = has_prev & opens_above & closes_above
    output["red_attack_failed"] = has_prev & opens_above & closes_below
    output["black_attack_success"] = has_prev & opens_below & closes_below
    output["black_attack_failed"] = has_prev & opens_below & closes_above

    # attack_type: based solely on Open vs prev_close (not Close).
    output["attack_type"] = np.select(
        [opens_above & has_prev, opens_below & has_prev],
        ["Big Red Attack", "Big Black Attack"],
        default="No Attack",
    )
    # attack_result: did the attack succeed or fail?
    output["attack_result"] = np.select(
        [
            output["red_attack_success"] | output["black_attack_success"],
            output["red_attack_failed"] | output["black_attack_failed"],
        ],
        ["Success", "Failed"],
        default="None",
    )
    # attack_direction mirrors attack_type (Open-side only).
    output["attack_direction"] = np.select(
        [opens_above & has_prev, opens_below & has_prev],
        ["Bullish", "Bearish"],
        default="None",
    )
    output["signal_summary"] = np.select(
        [
            output["red_attack_success"],
            output["red_attack_failed"],
            output["black_attack_success"],
            output["black_attack_failed"],
        ],
        [
            "Big Red Attack Success",
            "Big Red Attack Failed",
            "Big Black Attack Success",
            "Big Black Attack Failed",
        ],
        default="No Attack",
    )
    return output


def run_signal_pipeline(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Run the simplified signal pipeline: prev_close + attack signals."""
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()

    output = add_prev_close(df)
    output = add_attack_signals(output)
    return output.sort_values(["StockCode", "Date"]).reset_index(drop=True)
