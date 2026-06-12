"""Signal calculation engine for the multi-direction (long/short) screener.

v2 detects four signal paths and routes them by direction:
- P1 BreakUp_Hold   (Long)  : upward breakout of red/black line, then hold above.
- P2 NewLine_Hold   (Long)  : a freshly-appeared line, then hold above (within window).
- P3 BreakDown_Reject (Short): downward break of red/black line, then reject below.
- P4 NewLine_Reject (Short) : a freshly-appeared line, then reject below (within window).

The long/up side keeps every v1 column name (break_red_line_daily,
break_black_line_daily, breakout_line_type/price, active_breakout_line_*,
retest_hold_daily) so existing consumers (charts, tests) keep working; the
short/down and new-line columns are additive.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from config import SIGNAL_COLUMNS

logger = logging.getLogger(__name__)

_PATH_SPECS = [
    # (final_col, direction, signal_type, line_type_col, line_price_col, side)
    ("p1_final", "Long", "P1_BreakUp_Hold", "active_breakout_line_type", "active_breakout_line_price", "long"),
    ("p2_final", "Long", "P2_NewLine_Hold", "active_new_line_type", "active_new_line_price", "long"),
    ("p3_final", "Short", "P3_BreakDown_Reject", "active_breakdown_line_type", "active_breakdown_line_price", "short"),
    ("p4_final", "Short", "P4_NewLine_Reject", "active_new_line_type", "active_new_line_price", "short"),
]

_INVESTOR_FLAG_COLUMNS = [
    "foreign_buy_streak_ok",
    "trust_buy_streak_ok",
    "foreign_sell_streak_ok",
    "trust_sell_streak_ok",
]


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
    """Create/forward-fill red_line & black_line and mark new-line appearances.

    A bar is either a red attack OR a black attack (Open>prev_close XOR
    Open<prev_close), so red_line_raw and black_line_raw can never both be
    non-null on the same bar — the new-line appearance is unambiguous (§3.3).
    """
    output = df.copy()
    red_line_raw = output["prev_close"].where(output["red_attack_success"])
    black_line_raw = output["prev_close"].where(output["black_attack_success"])
    output["red_line"] = red_line_raw.groupby(output["StockCode"]).ffill()
    output["black_line"] = black_line_raw.groupby(output["StockCode"]).ffill()

    red_appeared = red_line_raw.notna()
    black_appeared = black_line_raw.notna()
    output["new_line_appeared"] = (red_appeared | black_appeared).fillna(False).astype(bool)
    output["new_line_type"] = np.select(
        [red_appeared, black_appeared],
        ["Red Line", "Black Line"],
        default="None",
    )
    output["new_line_price"] = np.select(
        [red_appeared, black_appeared],
        [red_line_raw, black_line_raw],
        default=np.nan,
    )
    return output


def add_breakout_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Detect strict closes above the latest red_line or black_line (P1 trigger)."""
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

    # Black line takes display priority on upward breaks.
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


def add_breakdown_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Detect strict closes below the latest red_line or black_line (P3 trigger).

    Mirror of add_breakout_signals. Strict ``<`` so a bar where the previous
    close sits exactly on the line is still resolved by the current close
    (``>`` breakout vs ``<`` breakdown); equality is therefore classified as a
    breakout, never a breakdown (§3.4b).
    """
    output = df.copy()

    previous_close = output.groupby("StockCode")["Close"].shift(1)
    previous_red_line = output.groupby("StockCode")["red_line"].shift(1)
    previous_black_line = output.groupby("StockCode")["black_line"].shift(1)

    output["break_down_red_line"] = (
        previous_red_line.notna()
        & output["red_line"].notna()
        & (previous_close >= previous_red_line)
        & (output["Close"] < output["red_line"])
    )
    output["break_down_black_line"] = (
        previous_black_line.notna()
        & output["black_line"].notna()
        & (previous_close >= previous_black_line)
        & (output["Close"] < output["black_line"])
    )

    # Red line takes display priority on downward breaks (opposite of breakout).
    output["breakdown_line_type"] = np.select(
        [output["break_down_red_line"], output["break_down_black_line"]],
        ["Red Line", "Black Line"],
        default="None",
    )
    output["breakdown_line_price"] = np.select(
        [output["break_down_red_line"], output["break_down_black_line"]],
        [output["red_line"], output["black_line"]],
        default=np.nan,
    )
    return output


def add_retest_hold_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Forward-fill the latest broken / broken-down line and flag retests.

    Long hold (P1): Low <= line and Close >= line, against the broken line.
    Short reject (P3): High >= line and Close <= line, against the broken-down line.
    """
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

    active_down_type = output["breakdown_line_type"].where(output["breakdown_line_type"] != "None")
    active_down_price = output["breakdown_line_price"].where(output["breakdown_line_price"].notna())
    output["active_breakdown_line_type"] = active_down_type.groupby(output["StockCode"]).ffill()
    output["active_breakdown_line_price"] = active_down_price.groupby(output["StockCode"]).ffill()
    output["retest_reject_daily"] = (
        output["active_breakdown_line_price"].notna()
        & (output["High"] >= output["active_breakdown_line_price"])
        & (output["Close"] <= output["active_breakdown_line_price"])
    )
    return output


def add_new_line_window_signals(df: pd.DataFrame, new_line_window: int) -> pd.DataFrame:
    """Track the most-recent new line and flag P2/P4 retests within its window.

    The window covers the ``new_line_window`` bars AFTER appearance (bars 1..N);
    the appearance bar itself (bars_since == 0) is excluded — on that bar the
    close is, by construction, on one side of the line, which would otherwise
    produce a degenerate signal identical to the attack (§3.5c).
    """
    output = df.copy()
    window = max(int(new_line_window), 1)
    appeared = output["new_line_appeared"].fillna(False).astype(bool)

    output["_new_line_group"] = appeared.groupby(output["StockCode"]).cumsum()
    output["bars_since_new_line"] = output.groupby(["StockCode", "_new_line_group"]).cumcount()
    output = output.drop(columns=["_new_line_group"])

    active_new_type = output["new_line_type"].where(appeared)
    active_new_price = output["new_line_price"].where(appeared)
    output["active_new_line_type"] = active_new_type.groupby(output["StockCode"]).ffill()
    output["active_new_line_price"] = active_new_price.groupby(output["StockCode"]).ffill()

    window_valid = (
        output["active_new_line_price"].notna()
        & (output["bars_since_new_line"] >= 1)
        & (output["bars_since_new_line"] <= window)
    )
    output["new_line_window_valid"] = window_valid
    output["p2_new_line_hold"] = (
        window_valid
        & (output["Low"] <= output["active_new_line_price"])
        & (output["Close"] >= output["active_new_line_price"])
    )
    output["p4_new_line_reject"] = (
        window_valid
        & (output["High"] >= output["active_new_line_price"])
        & (output["Close"] <= output["active_new_line_price"])
    )
    return output


def add_path_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the four per-bar path booleans (pre volume / lookback gating)."""
    output = df.copy()
    output["p1_break_up_hold"] = output["retest_hold_daily"].fillna(False).astype(bool)
    output["p2_new_line_hold"] = output["p2_new_line_hold"].fillna(False).astype(bool)
    output["p3_break_down_reject"] = output["retest_reject_daily"].fillna(False).astype(bool)
    output["p4_new_line_reject"] = output["p4_new_line_reject"].fillna(False).astype(bool)
    return output


def add_final_filters(df: pd.DataFrame, lookback_bars: int, min_volume: int) -> pd.DataFrame:
    """Apply direction-agnostic volume + lookback gating to each of the four paths."""
    output = df.sort_values(["StockCode", "Date"]).copy()
    group_sizes = output.groupby("StockCode")["Date"].transform("size")
    row_number = output.groupby("StockCode").cumcount()

    output["volume_pass"] = output["Volume"].fillna(0) >= int(min_volume)
    output["lookback_rank"] = group_sizes - row_number
    gate = output["volume_pass"] & (output["lookback_rank"] <= int(lookback_bars))

    output["p1_final"] = output["p1_break_up_hold"].fillna(False) & gate
    output["p2_final"] = output["p2_new_line_hold"].fillna(False) & gate
    output["p3_final"] = output["p3_break_down_reject"].fillna(False) & gate
    output["p4_final"] = output["p4_new_line_reject"].fillna(False) & gate
    output["final_signal"] = (
        output["p1_final"] | output["p2_final"] | output["p3_final"] | output["p4_final"]
    )
    return output


def _compute_investor_streak_flags(
    investor: pd.DataFrame, consecutive_days: int, flag_columns: list[str]
) -> pd.DataFrame:
    """Compute N-consecutive-day buy/sell streak flags per stock.

    The streak must run over *consecutive trading days*. The market-wide set of
    observed dates is used as the trading calendar: each stock is reindexed onto
    it so a day where this stock has no row — no institutional activity, or a
    failed fetch — becomes a NaN that breaks the streak, instead of being
    silently bridged. Genuine market holidays never appear in this calendar (no
    stock has them), so they cannot cause a spurious break.
    """
    trading_days = pd.DatetimeIndex(sorted(pd.to_datetime(investor["Date"].unique())))

    def _streaks_for_one(group: pd.DataFrame) -> pd.DataFrame:
        reindexed = group.set_index("Date").reindex(trading_days)
        foreign = reindexed["foreign_net"]
        trust = reindexed["trust_net"]

        def _streak(series: pd.Series, positive: bool) -> pd.Series:
            hit = series.gt(0) if positive else series.lt(0)  # NaN → False, breaks the run
            return hit.rolling(consecutive_days, min_periods=consecutive_days).sum().eq(consecutive_days)

        flags = pd.DataFrame(index=trading_days)
        flags["foreign_buy_streak_ok"] = _streak(foreign, True)
        flags["foreign_sell_streak_ok"] = _streak(foreign, False)
        flags["trust_buy_streak_ok"] = _streak(trust, True)
        flags["trust_sell_streak_ok"] = _streak(trust, False)
        flags["BaseCode"] = group["BaseCode"].iloc[0]
        return flags.reset_index(names="Date")

    frames = [_streaks_for_one(group) for _, group in investor.groupby("BaseCode", sort=False)]
    combined = pd.concat(frames, ignore_index=True)
    for col in flag_columns:
        combined[col] = combined[col].fillna(False).astype(bool)
    return combined[["Date", "BaseCode", *flag_columns]]


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

    flag_columns = _INVESTOR_FLAG_COLUMNS
    if investor_flow_df is None or investor_flow_df.empty:
        for col in flag_columns:
            output[col] = False
        return output

    investor = investor_flow_df.copy()
    investor["Date"] = pd.to_datetime(investor["Date"], errors="coerce")
    investor["BaseCode"] = investor["BaseCode"].astype(str).str.strip()
    investor["foreign_net"] = pd.to_numeric(investor["foreign_net"], errors="coerce")
    investor["trust_net"] = pd.to_numeric(investor["trust_net"], errors="coerce")
    anomaly_count = int(investor[["foreign_net", "trust_net"]].isna().sum().sum())
    if anomaly_count:
        # 無法解析的買賣超數值：保守補 0（不會成立連買／連賣），但留下紀錄。
        logger.warning("法人買賣超資料含 %d 筆無法解析的數值，相關日期的法人條件以未達成處理。", anomaly_count)
    investor["foreign_net"] = investor["foreign_net"].fillna(0)
    investor["trust_net"] = investor["trust_net"].fillna(0)
    investor = (
        investor.dropna(subset=["Date"])
        .drop_duplicates(subset=["BaseCode", "Date"], keep="last")
        .sort_values(["BaseCode", "Date"])
        .reset_index(drop=True)
    )
    if investor.empty:
        for col in flag_columns:
            output[col] = False
        return output

    investor_flags = _compute_investor_streak_flags(investor, consecutive_days, flag_columns)

    # 法人資料只到最後一個可得交易日；其後的 K 棒（merge_asof backward 會把
    # 最後一筆旗標往後帶）狀態其實未知，保守視為未達成。
    last_flow_date = investor_flags["Date"].max()

    try:
        merged = pd.merge_asof(
            output.sort_values("Date"),
            investor_flags.sort_values("Date"),
            on="Date",
            by="BaseCode",
            direction="backward",
        )
        future_mask = merged["Date"] > last_flow_date
        for col in flag_columns:
            merged[col] = pd.array(merged[col], dtype="boolean").fillna(False).astype(bool)
            if future_mask.any():
                merged.loc[future_mask, col] = False
    except Exception as exc:
        # merge_asof can raise on pandas version / dtype edge cases. Degrade all
        # investor flags to False instead of aborting the run — but never silently.
        logger.warning("法人旗標合併失敗，全部法人條件降級為未達成：%s", exc)
        merged = output.copy()
        for col in flag_columns:
            merged[col] = False

    return merged.sort_values(["StockCode", "Date"]).reset_index(drop=True)


def build_direction_signals(processed_df: pd.DataFrame, params: dict) -> dict:
    """Explode the wide per-bar frame into long/short signal rows (§3.8).

    Each path that passes (with its direction-aware investor gate, §3.7) becomes
    rows in either ``long_signals`` (P1/P2) or ``short_signals`` (P3/P4). A single
    bar matching multiple paths produces multiple rows. Returns a dict with keys
    ``long_signals`` and ``short_signals``; each is sorted by Date desc, StockCode
    asc (§4.1) and carries the unified SIGNAL_COLUMNS schema.
    """
    empty = pd.DataFrame(columns=SIGNAL_COLUMNS)
    if processed_df is None or processed_df.empty:
        return {"long_signals": empty.copy(), "short_signals": empty.copy()}

    df = processed_df.copy()
    if "StockName" not in df.columns:
        df["StockName"] = df["StockCode"]
    if "Timeframe" not in df.columns:
        df["Timeframe"] = pd.NA
    for col in _INVESTOR_FLAG_COLUMNS:
        if col not in df.columns:
            df[col] = False

    long_gate = pd.Series(True, index=df.index)
    if params.get("foreign_buy_streak", False):
        long_gate &= df["foreign_buy_streak_ok"].fillna(False).astype(bool)
    if params.get("trust_buy_streak", False):
        long_gate &= df["trust_buy_streak_ok"].fillna(False).astype(bool)
    short_gate = pd.Series(True, index=df.index)
    if params.get("foreign_sell_streak", False):
        short_gate &= df["foreign_sell_streak_ok"].fillna(False).astype(bool)
    if params.get("trust_sell_streak", False):
        short_gate &= df["trust_sell_streak_ok"].fillna(False).astype(bool)

    long_rows: list[pd.DataFrame] = []
    short_rows: list[pd.DataFrame] = []
    for final_col, direction, signal_type, type_col, price_col, side in _PATH_SPECS:
        if final_col not in df.columns:
            continue
        gate = long_gate if side == "long" else short_gate
        mask = df[final_col].fillna(False).astype(bool) & gate
        if not mask.any():
            continue
        subset = df.loc[mask].copy()
        subset["direction"] = direction
        subset["signal_type"] = signal_type
        subset["retest_line_type"] = subset[type_col]
        subset["retest_line_price"] = subset[price_col]
        frame = subset.reindex(columns=SIGNAL_COLUMNS)
        (long_rows if side == "long" else short_rows).append(frame)

    direction_filter = params.get("direction_filter", "全部")

    def _finalize(rows: list[pd.DataFrame]) -> pd.DataFrame:
        if not rows:
            return empty.copy()
        combined = pd.concat(rows, ignore_index=True)
        return combined.sort_values(["Date", "StockCode"], ascending=[False, True]).reset_index(drop=True)

    long_signals = _finalize(long_rows) if direction_filter in ("全部", "做多") else empty.copy()
    short_signals = _finalize(short_rows) if direction_filter in ("全部", "做空") else empty.copy()
    return {"long_signals": long_signals, "short_signals": short_signals}


def run_signal_pipeline(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Run the four-path multi-direction signal pipeline (§3.6 ordering)."""
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()

    output = add_prev_close(df)
    output = add_attack_signals(output)
    output = add_attack_lines(output)
    output = add_breakout_signals(output)
    output = add_breakdown_signals(output)
    output = add_retest_hold_signals(output)
    output = add_new_line_window_signals(output, new_line_window=int(params.get("new_line_window", 5)))
    output = add_path_signals(output)
    output = add_final_filters(
        output,
        lookback_bars=int(params.get("lookback_bars", 10)),
        min_volume=int(params.get("min_volume", 2000)),
    )
    return output.sort_values(["StockCode", "Date"]).reset_index(drop=True)
