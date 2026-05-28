"""Signal calculation engine for the Bullish Three-Condition method."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import NO_VOLUME_FILTER


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Safely divide two aligned Series and return NaN on zero denominators."""
    safe_denominator = denominator.where(denominator.notna() & (denominator != 0))
    return numerator / safe_denominator


def _rolling_any(series: pd.Series, window: int) -> pd.Series:
    return (
        series.fillna(False)
        .astype(int)
        .rolling(window=window, min_periods=1)
        .max()
        .fillna(0)
        .astype(bool)
    )


def add_basic_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add grouped prev_close, percentage features, and volume ratios."""
    output = df.sort_values(["StockCode", "Date"]).copy()
    grouped = output.groupby("StockCode", group_keys=False)

    output["prev_close"] = grouped["Close"].shift(1)
    output["gap_pct"] = safe_divide(output["Open"] - output["prev_close"], output["prev_close"]) * 100
    output["close_vs_prev_pct"] = (
        safe_divide(output["Close"] - output["prev_close"], output["prev_close"]) * 100
    )
    output["body_pct"] = safe_divide(output["Close"] - output["Open"], output["Open"]) * 100

    output["volume_ma5"] = grouped["Volume"].transform(
        lambda series: series.rolling(window=5, min_periods=5).mean()
    )
    output["volume_ma20"] = grouped["Volume"].transform(
        lambda series: series.rolling(window=20, min_periods=20).mean()
    )
    output["volume_ratio_5"] = safe_divide(output["Volume"], output["volume_ma5"])
    output["volume_ratio_20"] = safe_divide(output["Volume"], output["volume_ma20"])
    return output


def add_attack_signals(
    df: pd.DataFrame,
    min_gap_pct: float,
    min_close_vs_prev_pct: float,
) -> pd.DataFrame:
    """Add Big Red / Big Black attack state columns."""
    output = df.copy()

    min_gap_pct = float(min_gap_pct)
    min_close_vs_prev_pct = float(min_close_vs_prev_pct)

    has_prev_close = output["prev_close"].notna()
    opens_above_prev = output["Open"] > output["prev_close"]
    opens_below_prev = output["Open"] < output["prev_close"]
    closes_above_prev = output["Close"] > output["prev_close"]
    closes_below_prev = output["Close"] < output["prev_close"]

    output["red_attack_success"] = (
        has_prev_close
        & opens_above_prev
        & closes_above_prev
        & (output["gap_pct"] >= min_gap_pct)
        & (output["close_vs_prev_pct"] >= min_close_vs_prev_pct)
    )
    output["red_attack_failed"] = (
        has_prev_close
        & opens_above_prev
        & closes_below_prev
        & (output["gap_pct"] >= min_gap_pct)
        & (output["close_vs_prev_pct"] <= -min_close_vs_prev_pct)
    )
    output["black_attack_success"] = (
        has_prev_close
        & opens_below_prev
        & closes_below_prev
        & (output["gap_pct"] <= -min_gap_pct)
        & (output["close_vs_prev_pct"] <= -min_close_vs_prev_pct)
    )
    output["black_attack_failed"] = (
        has_prev_close
        & opens_below_prev
        & closes_above_prev
        & (output["gap_pct"] <= -min_gap_pct)
        & (output["close_vs_prev_pct"] >= min_close_vs_prev_pct)
    )

    output["attack_type"] = np.select(
        [
            output["red_attack_success"] | output["red_attack_failed"],
            output["black_attack_success"] | output["black_attack_failed"],
        ],
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
        [
            output["red_attack_success"] | output["red_attack_failed"],
            output["black_attack_success"] | output["black_attack_failed"],
        ],
        ["Bullish", "Bearish"],
        default="None",
    )

    return output


def add_base_lines(df: pd.DataFrame) -> pd.DataFrame:
    """Create grouped support/resistance bases from attack events."""
    output = df.copy()

    output["red_base_raw"] = np.where(output["red_attack_success"], output["prev_close"], np.nan)
    output["black_base_raw"] = np.where(output["black_attack_success"], output["prev_close"], np.nan)
    output["black_failed_base_raw"] = np.where(
        output["black_attack_failed"], output["prev_close"], np.nan
    )
    output["latest_big_red_base_raw"] = np.where(
        output["red_attack_success"] | output["black_attack_failed"],
        output["prev_close"],
        np.nan,
    )
    output["latest_big_black_base_raw"] = np.where(
        output["black_attack_success"] | output["red_attack_failed"],
        output["prev_close"],
        np.nan,
    )

    output["red_base"] = output.groupby("StockCode")["red_base_raw"].ffill()
    output["black_base"] = output.groupby("StockCode")["black_base_raw"].ffill()
    output["black_failed_base"] = output.groupby("StockCode")["black_failed_base_raw"].ffill()
    output["latest_big_red_base"] = output.groupby("StockCode")["latest_big_red_base_raw"].ffill()
    output["latest_big_black_base"] = output.groupby("StockCode")["latest_big_black_base_raw"].ffill()
    return output


def add_long_conditions(
    df: pd.DataFrame,
    lookback_days: int,
    break_buffer_pct: float,
    retest_tolerance_pct: float,
    retest_break_pct: float,
    min_score: int,
) -> pd.DataFrame:
    """Calculate the independent A/B/C conditions and final long score."""
    output = df.copy()
    lookback_days = max(int(lookback_days), 1)

    # Treat the latest qualifying bullish event as the effective big red candle:
    # either a red attack success or a black attack failed.
    output["cond_A_red_attack_daily"] = (
        output["red_attack_success"] | output["black_attack_failed"]
    ).fillna(False)
    output["cond_A_red_attack_window"] = output.groupby("StockCode")[
        "cond_A_red_attack_daily"
    ].transform(lambda series: _rolling_any(series, lookback_days))

    # Break-big-black is measured against the base left by the most recent
    # qualifying bearish event: either a black attack success or a red attack failed.
    output["cond_B_break_black_daily"] = output["latest_big_black_base"].notna() & (
        output["Close"] > output["latest_big_black_base"] * (1 + float(break_buffer_pct) / 100)
    )
    output["cond_B_break_black_window"] = output.groupby("StockCode")[
        "cond_B_break_black_daily"
    ].transform(lambda series: _rolling_any(series, lookback_days))

    tolerance_multiplier = 1 + float(retest_tolerance_pct) / 100
    break_multiplier = 1 - float(retest_break_pct) / 100

    output["retest_red_base_daily"] = output["red_base"].notna() & (
        output["Low"] <= output["red_base"] * tolerance_multiplier
    ) & (output["Close"] >= output["red_base"] * break_multiplier)
    # cond_B uses latest_big_black_base; cond_C must retest the SAME base for consistency.
    output["retest_black_base_daily"] = output["latest_big_black_base"].notna() & (
        output["Low"] <= output["latest_big_black_base"] * tolerance_multiplier
    ) & (output["Close"] >= output["latest_big_black_base"] * break_multiplier)
    output["retest_black_failed_base_daily"] = output["black_failed_base"].notna() & (
        output["Low"] <= output["black_failed_base"] * tolerance_multiplier
    ) & (output["Close"] >= output["black_failed_base"] * break_multiplier)
    output["cond_C_retest_base_daily"] = (
        output["retest_red_base_daily"]
        | output["retest_black_base_daily"]
        | output["retest_black_failed_base_daily"]
    )
    output["cond_C_retest_base_window"] = output.groupby("StockCode")[
        "cond_C_retest_base_daily"
    ].transform(lambda series: _rolling_any(series, lookback_days))

    output["long_signal_score"] = (
        output["cond_A_red_attack_window"].astype(int)
        + output["cond_B_break_black_window"].astype(int)
        + output["cond_C_retest_base_window"].astype(int)
    )
    output["long_signal"] = output["long_signal_score"] >= int(min_score)
    return output


def add_volume_filter(
    df: pd.DataFrame,
    volume_filter_mode: str,
    min_volume_ratio_5: float,
    min_volume_ratio_20: float,
) -> pd.DataFrame:
    """Apply the optional volume filter to the long signal."""
    output = df.copy()

    ratio_5_ok = output["volume_ratio_5"].notna() & (
        output["volume_ratio_5"] >= float(min_volume_ratio_5)
    )
    ratio_20_ok = output["volume_ratio_20"].notna() & (
        output["volume_ratio_20"] >= float(min_volume_ratio_20)
    )

    if volume_filter_mode == NO_VOLUME_FILTER:
        output["volume_filter_pass"] = True
    elif volume_filter_mode == "要求 5 日量比":
        output["volume_filter_pass"] = ratio_5_ok
    elif volume_filter_mode == "要求 20 日量比":
        output["volume_filter_pass"] = ratio_20_ok
    elif volume_filter_mode == "同時要求 5 日與 20 日量比":
        output["volume_filter_pass"] = ratio_5_ok & ratio_20_ok
    else:
        raise ValueError(f"Unsupported volume filter mode: {volume_filter_mode}")

    if volume_filter_mode == NO_VOLUME_FILTER:
        output["final_long_signal"] = output["long_signal"]
    else:
        output["final_long_signal"] = output["long_signal"] & output["volume_filter_pass"]
    return output


def run_signal_pipeline(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Run the full grouped signal pipeline for the selected timeframe."""
    if df is None or df.empty:
        return df.copy()

    output = add_basic_features(df)
    output = add_attack_signals(
        output,
        min_gap_pct=params["min_gap_pct"],
        min_close_vs_prev_pct=params["min_close_vs_prev_pct"],
    )
    output = add_base_lines(output)
    output = add_long_conditions(
        output,
        lookback_days=params["lookback_days"],
        break_buffer_pct=params["break_buffer_pct"],
        retest_tolerance_pct=params["retest_tolerance_pct"],
        retest_break_pct=params["retest_break_pct"],
        min_score=params["min_score"],
    )
    output = add_volume_filter(
        output,
        volume_filter_mode=params["volume_filter_mode"],
        min_volume_ratio_5=params["min_volume_ratio_5"],
        min_volume_ratio_20=params["min_volume_ratio_20"],
    )
    return output.sort_values(["StockCode", "Date"]).reset_index(drop=True)
