"""Signal calculation engine — Big Red/Black Attack signals + Three Methods conditions.

Attack direction: Open vs prev_close only. Failed attacks never convert to opposite signals.

Three Methods:
  - red_base  : prev_close at the most recent red_attack_success bar (forward-filled)
  - black_base: prev_close at the most recent black_attack_success bar (forward-filled)

Bullish Three Methods conditions (checked within rolling lookback window):
  cond_1: red_attack_success appeared
  cond_2: Open broke above black_base
  cond_3: Low touched within ±pullback_pct of (black_base or red_base),
          AND Close did not close below the reference (pullback not failed)

Bearish Three Methods conditions:
  cond_1: black_attack_success appeared
  cond_2: Open broke below red_base
  cond_3: High touched within ±pullback_pct of (black_base or red_base),
          AND Close did not close above the reference (pullback not failed)

At least min_conditions (default 2) must be satisfied for a qualifying signal.
pullback_pct (default 2%) controls the valid zone around the reference price.
Final Three Methods direction is exclusive:
  - Bullish if bullish_methods_count > bearish_methods_count
  - Bearish if bearish_methods_count > bullish_methods_count
  - None if equal
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


def add_three_methods_conditions(df: pd.DataFrame, lookback_bars: int, pullback_pct: float = 0.02) -> pd.DataFrame:
    """Compute Three Methods conditions for each bar using a rolling lookback window.

    Per-bar conditions:
      bull_cond_1 = red_attack_success
      bull_cond_2 = Open > black_base  (broke above bearish attack origin)
      bull_cond_3 = Low within ±pullback_pct of (black_base or red_base)
                    AND Close >= ref  (close did not close below reference)

      bear_cond_1 = black_attack_success
      bear_cond_2 = Open < red_base    (broke below bullish attack origin)
      bear_cond_3 = High within ±pullback_pct of (black_base or red_base)
                    AND Close <= ref  (close did not close above reference)

    Each *_in_window column is True if the condition was True in ANY of the last
    lookback_bars K-bars for that stock.  Score columns sum the three window flags.

    Args:
        pullback_pct: fraction (e.g. 0.02 = 2%). Low/High must be within this
                      percentage of the reference price, AND the close must not
                      close beyond the reference.
    """
    output = df.copy()

    has_rb = output["red_base"].notna()
    has_bb = output["black_base"].notna()

    # Per-bar conditions.
    output["bull_cond_1"] = output["red_attack_success"].fillna(False)
    output["bull_cond_2"] = has_bb & (output["Open"] > output["black_base"])

    # Bullish pullback helper: Low within ±pct of ref AND Close does not close below ref.
    def _bull_pb(ref_col: str) -> pd.Series:
        has_ref = output[ref_col].notna()
        ref = output[ref_col]
        low_in_zone = (
            (output["Low"] >= ref * (1 - pullback_pct))
            & (output["Low"] <= ref * (1 + pullback_pct))
        )
        close_ok = output["Close"] >= ref
        return has_ref & low_in_zone & close_ok

    output["bull_cond_3"] = _bull_pb("black_base") | _bull_pb("red_base")

    output["bear_cond_1"] = output["black_attack_success"].fillna(False)
    output["bear_cond_2"] = has_rb & (output["Open"] < output["red_base"])

    # Bearish pullback helper: High within ±pct of ref AND Close does not close above ref.
    def _bear_pb(ref_col: str) -> pd.Series:
        has_ref = output[ref_col].notna()
        ref = output[ref_col]
        high_in_zone = (
            (output["High"] >= ref * (1 - pullback_pct))
            & (output["High"] <= ref * (1 + pullback_pct))
        )
        close_ok = output["Close"] <= ref
        return has_ref & high_in_zone & close_ok

    output["bear_cond_3"] = _bear_pb("black_base") | _bear_pb("red_base")

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

    output["final_methods_direction"] = np.select(
        [
            output["bullish_methods_count"] > output["bearish_methods_count"],
            output["bearish_methods_count"] > output["bullish_methods_count"],
        ],
        ["Bullish", "Bearish"],
        default="None",
    )
    output["final_methods_count"] = output[
        ["bullish_methods_count", "bearish_methods_count"]
    ].max(axis=1)

    return output


def attach_investor_flow_flags(
    df: pd.DataFrame,
    investor_flow_df: pd.DataFrame,
    consecutive_days: int = 3,
) -> pd.DataFrame:
    """Attach recent N-day institutional buy/sell flags to bars by stock and date.

    Institutional flow is calculated from daily public data and mapped to each bar
    using the latest available daily record on or before that bar's Date.
    """
    output = df.copy()
    output["Date"] = pd.to_datetime(output["Date"], errors="coerce")
    output = output.dropna(subset=["Date"]).copy()
    output["BaseCode"] = output["StockCode"].astype(str).str.split(".").str[0]
    consecutive_days = max(int(consecutive_days), 1)

    flag_columns = [
        "foreign_buy_streak_ok",
        "trust_buy_streak_ok",
        "foreign_sell_streak_ok",
        "trust_sell_streak_ok",
    ]
    if investor_flow_df is None or investor_flow_df.empty:
        for col in flag_columns:
            output[col] = False
        return output

    investor = investor_flow_df.copy()
    investor["Date"] = pd.to_datetime(investor["Date"], errors="coerce")
    investor["BaseCode"] = investor["BaseCode"].astype(str).str.strip()
    investor = investor.dropna(subset=["Date"]).sort_values(["BaseCode", "Date"]).reset_index(drop=True)

    investor["foreign_buy_streak_ok"] = investor.groupby("BaseCode")["foreign_net"].transform(
        lambda x: x.gt(0).rolling(consecutive_days, min_periods=consecutive_days).sum().eq(consecutive_days)
    )
    investor["foreign_sell_streak_ok"] = investor.groupby("BaseCode")["foreign_net"].transform(
        lambda x: x.lt(0).rolling(consecutive_days, min_periods=consecutive_days).sum().eq(consecutive_days)
    )
    investor["trust_buy_streak_ok"] = investor.groupby("BaseCode")["trust_net"].transform(
        lambda x: x.gt(0).rolling(consecutive_days, min_periods=consecutive_days).sum().eq(consecutive_days)
    )
    investor["trust_sell_streak_ok"] = investor.groupby("BaseCode")["trust_net"].transform(
        lambda x: x.lt(0).rolling(consecutive_days, min_periods=consecutive_days).sum().eq(consecutive_days)
    )

    merged_groups: list[pd.DataFrame] = []
    for base_code, stock_df in output.sort_values(["BaseCode", "Date"]).groupby("BaseCode", sort=False):
        flow_df = (
            investor[investor["BaseCode"] == str(base_code).strip()][["Date", *flag_columns]]
            .drop_duplicates(subset=["Date"], keep="last")
            .sort_values("Date")
            .reset_index(drop=True)
        )
        if flow_df.empty:
            stock_output = stock_df.copy()
            for col in flag_columns:
                stock_output[col] = False
        else:
            stock_output = stock_df.sort_values("Date").copy()
            last_flow_date = flow_df["Date"].max()
            mapped_flags = (
                flow_df.set_index("Date")[flag_columns]
                .sort_index()
                .reindex(stock_output["Date"], method="ffill")
                .reset_index(drop=True)
            )
            mapped_flags.columns = flag_columns
            future_mask = stock_output["Date"] > last_flow_date
            if future_mask.any():
                mapped_flags.loc[future_mask, flag_columns] = False
            for col in flag_columns:
                stock_output[col] = mapped_flags[col].to_numpy()
        merged_groups.append(stock_output)

    merged = pd.concat(merged_groups, ignore_index=True)
    for col in flag_columns:
        merged[col] = pd.array(merged[col], dtype="boolean").fillna(False).astype(bool)
    return merged.sort_values(["StockCode", "Date"]).reset_index(drop=True)


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
    pullback_pct = float(params.get("pullback_pct", 2.0)) / 100.0  # convert % to fraction

    output = add_prev_close(df)
    output = add_attack_signals(output)
    output = add_base_lines(output)
    output = add_three_methods_conditions(output, lookback_bars, pullback_pct)
    return output.sort_values(["StockCode", "Date"]).reset_index(drop=True)
