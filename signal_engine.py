"""Signal calculation engine for breakout-and-retest-hold screening."""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_prev_close(df: pd.DataFrame) -> pd.DataFrame:
    """Add grouped prev_close = previous K-bar close, per StockCode."""
    output = df.sort_values(["StockCode", "Date"]).copy()
    output["prev_close"] = output.groupby("StockCode")["Close"].shift(1)
    return output


def add_attack_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Detect Big Red / Big Black attacks with independent boolean masks."""
    output = df.copy()

    has_prev = output["prev_close"].notna()
    red_attack_attempt = has_prev & (output["Open"] > output["prev_close"])
    black_attack_attempt = has_prev & (output["Open"] < output["prev_close"])

    output["red_attack_success"] = red_attack_attempt & (output["Close"] > output["prev_close"])
    output["red_attack_failed"] = red_attack_attempt & (output["Close"] < output["prev_close"])
    output["black_attack_success"] = black_attack_attempt & (output["Close"] < output["prev_close"])
    output["black_attack_failed"] = black_attack_attempt & (output["Close"] > output["prev_close"])

    output["attack_type"] = np.select(
        [red_attack_attempt, black_attack_attempt],
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


def add_attack_lines(df: pd.DataFrame) -> pd.DataFrame:
    """Create and forward-fill red_line and black_line per StockCode."""
    output = df.copy()
    output["red_line_raw"] = output["prev_close"].where(output["red_attack_success"])
    output["black_line_raw"] = output["prev_close"].where(output["black_attack_success"])
    output["red_line"] = output.groupby("StockCode")["red_line_raw"].ffill()
    output["black_line"] = output.groupby("StockCode")["black_line_raw"].ffill()
    return output.drop(columns=["red_line_raw", "black_line_raw"])


def add_breakout_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Detect strict closes above the latest red_line or black_line."""
    output = df.copy()

    previous_close = output.groupby("StockCode")["Close"].shift(1)
    previous_red_line = output.groupby("StockCode")["red_line"].shift(1)
    previous_black_line = output.groupby("StockCode")["black_line"].shift(1)

    output["break_red_line_daily"] = (
        previous_red_line.notna()
        & output["red_line"].notna()
        & (previous_close <= previous_red_line)
        & (output["Close"] > output["red_line"])
    )
    output["break_black_line_daily"] = (
        previous_black_line.notna()
        & output["black_line"].notna()
        & (previous_close <= previous_black_line)
        & (output["Close"] > output["black_line"])
    )

    output["breakout_line_type"] = np.select(
        [output["break_black_line_daily"], output["break_red_line_daily"]],
        ["Black Line", "Red Line"],
        default="None",
    )
    output["breakout_line_price"] = np.select(
        [output["break_black_line_daily"], output["break_red_line_daily"]],
        [output["black_line"], output["red_line"]],
        default=np.nan,
    )
    return output


def add_retest_hold_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Forward-fill the latest broken line and flag retests that close above it."""
    output = df.copy()

    active_type = output["breakout_line_type"].where(output["breakout_line_type"] != "None")
    active_price = output["breakout_line_price"].where(output["breakout_line_price"].notna())

    output["active_breakout_line_type"] = active_type.groupby(output["StockCode"]).ffill()
    output["active_breakout_line_price"] = active_price.groupby(output["StockCode"]).ffill()
    output["retest_hold_daily"] = (
        output["active_breakout_line_price"].notna()
        & (output["Low"] <= output["active_breakout_line_price"])
        & (output["Close"] >= output["active_breakout_line_price"])
    )
    return output


def add_final_filters(df: pd.DataFrame, lookback_bars: int, min_volume: int) -> pd.DataFrame:
    """Apply recent-window and volume filters to retest-hold rows."""
    output = df.sort_values(["StockCode", "Date"]).copy()
    group_sizes = output.groupby("StockCode")["Date"].transform("size")
    row_number = output.groupby("StockCode").cumcount()

    output["volume_pass"] = output["Volume"].fillna(0) >= int(min_volume)
    output["lookback_rank"] = group_sizes - row_number
    output["final_signal"] = (
        output["retest_hold_daily"].fillna(False)
        & output["volume_pass"]
        & (output["lookback_rank"] <= int(lookback_bars))
    )
    return output


def attach_investor_flow_flags(
    df: pd.DataFrame,
    investor_flow_df: pd.DataFrame,
    consecutive_days: int = 3,
) -> pd.DataFrame:
    """Attach recent N-day institutional buy/sell flags to bars by stock and date."""
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
    investor["foreign_net"] = pd.to_numeric(investor["foreign_net"], errors="coerce").fillna(0)
    investor["trust_net"] = pd.to_numeric(investor["trust_net"], errors="coerce").fillna(0)
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
            stock_output = (
                stock_df.drop(columns=[col for col in flag_columns if col in stock_df.columns])
                .sort_values("Date")
                .reset_index(drop=True)
            )
            last_flow_date = flow_df["Date"].max()
            stock_output = pd.merge_asof(stock_output, flow_df, on="Date", direction="backward")
            future_mask = stock_output["Date"] > last_flow_date
            for col in flag_columns:
                stock_output[col] = pd.array(stock_output[col], dtype="boolean").fillna(False).astype(bool)
                if future_mask.any():
                    stock_output.loc[future_mask, col] = False
        merged_groups.append(stock_output)

    merged = pd.concat(merged_groups, ignore_index=True)
    for col in flag_columns:
        merged[col] = pd.array(merged[col], dtype="boolean").fillna(False).astype(bool)
    return merged.sort_values(["StockCode", "Date"]).reset_index(drop=True)


def run_signal_pipeline(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Run the breakout-and-retest-hold signal pipeline."""
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()

    output = add_prev_close(df)
    output = add_attack_signals(output)
    output = add_attack_lines(output)
    output = add_breakout_signals(output)
    output = add_retest_hold_signals(output)
    output = add_final_filters(
        output,
        lookback_bars=int(params.get("lookback_bars", 10)),
        min_volume=int(params.get("min_volume", 2000)),
    )
    return output.sort_values(["StockCode", "Date"]).reset_index(drop=True)
