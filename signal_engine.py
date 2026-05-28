"""Signal calculation engine — Big Red/Black Attack signals + Three Methods conditions.

Attack direction: Open vs prev_close only. Failed attacks never convert to opposite signals.

Three Methods:
  - red_base  : prev_close at the most recent red_attack_success bar (forward-filled)
  - black_base: prev_close at the most recent black_attack_success bar (forward-filled)

Bullish Three Methods conditions (checked within rolling lookback window):
  cond_1: red_attack_success appeared
  cond_2: Open broke above black_base
  cond_3: Low pulled back to black_base OR red_base

Bearish Three Methods conditions:
  cond_1: black_attack_success appeared
  cond_2: Open broke below red_base
  cond_3: High pulled back to black_base OR red_base

At least min_conditions (default 2) must be satisfied for a qualifying signal.
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

    # Each signal is a strictly independent boolean mask — no if/elif conversion.
    output["red_attack_success"] = has_prev & opens_above & closes_above
    output["red_attack_failed"] = has_prev & opens_above & closes_below
    output["black_attack_success"] = has_prev & opens_below & closes_below
    output["black_attack_failed"] = has_prev & opens_below & closes_above

    output["attack_type"] = np.select(
        [opens_above & has_prev, opens_below & has_prev],
        ["Big Red Attack", "Big Black Attack"],
        default="No Attack",
    )
    output["attack_result"] = np.select(
        [
            output["red_attack_success"] | output["black_attack_success"],
            output["red_attack_failed"] | output["black_attack_failed"],
        ],
        ["Success", "Failed"],
        default="None",
    )
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


def add_base_lines(df: pd.DataFrame) -> pd.DataFrame:
    """Compute red_base and black_base reference levels per StockCode.

    red_base   = prev_close at the most recent red_attack_success bar (forward-filled).
    black_base = prev_close at the most recent black_attack_success bar (forward-filled).

    These are the gap-origin levels from which each attack launched, and serve as
    the 'bottom of bullish candle' and 'top of bearish candle' reference prices.
    """
    output = df.copy()

    # Stamp the value at signal bars only, then forward-fill within each stock group.
    output["_red_sig"] = output["prev_close"].where(output["red_attack_success"])
    output["_blk_sig"] = output["prev_close"].where(output["black_attack_success"])

    output["red_base"] = output.groupby("StockCode")["_red_sig"].transform("ffill")
    output["black_base"] = output.groupby("StockCode")["_blk_sig"].transform("ffill")

    return output.drop(columns=["_red_sig", "_blk_sig"])


def add_three_methods_conditions(df: pd.DataFrame, lookback_bars: int) -> pd.DataFrame:
    """Compute Three Methods conditions for each bar using a rolling lookback window.

    Per-bar conditions:
      bull_cond_1 = red_attack_success
      bull_cond_2 = Open > black_base  (broke above bearish attack origin)
      bull_cond_3 = Low  <= black_base OR Low  <= red_base  (pulled back to reference)

      bear_cond_1 = black_attack_success
      bear_cond_2 = Open < red_base    (broke below bullish attack origin)
      bear_cond_3 = High >= black_base OR High >= red_base  (bounced back to reference)

    Each *_in_window column is True if the condition was True in ANY of the last
    lookback_bars K-bars for that stock.  Score columns sum the three window flags.
    """
    output = df.copy()

    has_rb = output["red_base"].notna()
    has_bb = output["black_base"].notna()

    # Per-bar conditions.
    output["bull_cond_1"] = output["red_attack_success"].fillna(False)
    output["bull_cond_2"] = has_bb & (output["Open"] > output["black_base"])
    output["bull_cond_3"] = (
        (has_bb & (output["Low"] <= output["black_base"]))
        | (has_rb & (output["Low"] <= output["red_base"]))
    )

    output["bear_cond_1"] = output["black_attack_success"].fillna(False)
    output["bear_cond_2"] = has_rb & (output["Open"] < output["red_base"])
    output["bear_cond_3"] = (
        (has_bb & (output["High"] >= output["black_base"]))
        | (has_rb & (output["High"] >= output["red_base"]))
    )

    # Rolling aggregation: was the condition True in ANY of the last N bars per stock?
    raw_conds = [
        "bull_cond_1", "bull_cond_2", "bull_cond_3",
        "bear_cond_1", "bear_cond_2", "bear_cond_3",
    ]
    for col in raw_conds:
        output[f"{col}_in_window"] = (
            output.groupby("StockCode")[col]
            .transform(
                lambda x: x.astype(float).rolling(lookback_bars, min_periods=1).max() > 0
            )
        )

    output["bullish_methods_count"] = (
        output["bull_cond_1_in_window"].astype(int)
        + output["bull_cond_2_in_window"].astype(int)
        + output["bull_cond_3_in_window"].astype(int)
    )
    output["bearish_methods_count"] = (
        output["bear_cond_1_in_window"].astype(int)
        + output["bear_cond_2_in_window"].astype(int)
        + output["bear_cond_3_in_window"].astype(int)
    )
    return output


def run_signal_pipeline(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Run the full signal pipeline:

    1. prev_close
    2. Attack signals (independent boolean masks — no opposite-side conversion)
    3. Base lines (red_base, black_base via forward-fill)
    4. Three Methods conditions (rolling lookback aggregation)
    """
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()

    lookback_bars = int(params.get("lookback_bars", 10))

    output = add_prev_close(df)
    output = add_attack_signals(output)
    output = add_base_lines(output)
    output = add_three_methods_conditions(output, lookback_bars)
    return output.sort_values(["StockCode", "Date"]).reset_index(drop=True)
