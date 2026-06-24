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

from config import INVESTOR_FLAG_COLUMNS as _INVESTOR_FLAG_COLUMNS, SIGNAL_COLUMNS

logger = logging.getLogger(__name__)

_PATH_SPECS = [
    # (final_col, direction, signal_type, line_type_col, line_price_col, side)
    ("p1_final", "Long", "P1_BreakUp_Hold", "active_breakout_line_type", "active_breakout_line_price", "long"),
    ("p2_final", "Long", "P2_NewLine_Hold", "active_new_line_type", "active_new_line_price", "long"),
    ("p3_final", "Short", "P3_BreakDown_Reject", "active_breakdown_line_type", "active_breakdown_line_price", "short"),
    ("p4_final", "Short", "P4_NewLine_Reject", "active_new_line_type", "active_new_line_price", "short"),
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


def _crosses_line(
    previous_close: pd.Series,
    previous_line: pd.Series,
    current_close: pd.Series,
    *,
    upward: bool,
) -> pd.Series:
    """Detect a strict close-cross of the PREVIOUS bar's line level (§3.4).

    Both halves of the test reference ``previous_line`` (the line that was in
    force on the prior bar). A bar that itself moves the line this period — a
    fresh attack success resets the line to this bar's prev_close, a DIFFERENT
    level — is therefore still judged against the level that was actually
    crossed. This keeps a genuine breakout/breakdown that coincides with a
    new-line bar (the over-suppression bug of v2.2.0), while still rejecting the
    "close lands between the old and new line" fake of §3.4a/§3.4b: that fake
    close never clears the OLD level, so it fails this test. Equality of the
    previous close to the line counts as a breakout (``<=``), never a breakdown
    (``>=`` + strict ``<``), so the two can never both fire on one line/bar.
    """
    if upward:
        return (
            previous_line.notna()
            & (previous_close <= previous_line)
            & (current_close > previous_line)
        )
    return (
        previous_line.notna()
        & (previous_close >= previous_line)
        & (current_close < previous_line)
    )


def add_breakout_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Detect strict closes above the previous red_line or black_line (P1 trigger)."""
    output = df.copy()

    previous_close = output.groupby("StockCode")["Close"].shift(1)
    previous_red_line = output.groupby("StockCode")["red_line"].shift(1)
    previous_black_line = output.groupby("StockCode")["black_line"].shift(1)

    output["break_red_line_daily"] = _crosses_line(
        previous_close, previous_red_line, output["Close"], upward=True
    )
    output["break_black_line_daily"] = _crosses_line(
        previous_close, previous_black_line, output["Close"], upward=True
    )

    # Black line takes display priority on upward breaks. The broken line is the
    # PREVIOUS (in-force) level, so the retest baseline is previous_*_line.
    output["breakout_line_type"] = np.select(
        [output["break_black_line_daily"], output["break_red_line_daily"]],
        ["Black Line", "Red Line"],
        default="None",
    )
    output["breakout_line_price"] = np.select(
        [output["break_black_line_daily"], output["break_red_line_daily"]],
        [previous_black_line, previous_red_line],
        default=np.nan,
    )
    return output


def add_breakdown_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Detect strict closes below the previous red_line or black_line (P3 trigger).

    Mirror of add_breakout_signals via _crosses_line(upward=False).
    """
    output = df.copy()

    previous_close = output.groupby("StockCode")["Close"].shift(1)
    previous_red_line = output.groupby("StockCode")["red_line"].shift(1)
    previous_black_line = output.groupby("StockCode")["black_line"].shift(1)

    output["break_down_red_line"] = _crosses_line(
        previous_close, previous_red_line, output["Close"], upward=False
    )
    output["break_down_black_line"] = _crosses_line(
        previous_close, previous_black_line, output["Close"], upward=False
    )

    # Red line takes display priority on downward breaks (opposite of breakout).
    output["breakdown_line_type"] = np.select(
        [output["break_down_red_line"], output["break_down_black_line"]],
        ["Red Line", "Black Line"],
        default="None",
    )
    output["breakdown_line_price"] = np.select(
        [output["break_down_red_line"], output["break_down_black_line"]],
        [previous_red_line, previous_black_line],
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


def _add_consecutive_streak_flags(
    investor: pd.DataFrame,
    consecutive_days: int,
    market_trading_days=None,
) -> pd.DataFrame:
    """Add the four N-day buy/sell streak flags, counting CALENDAR trading days.

    The streak must be N *consecutive trading days*, not N consecutive rows. A
    rolling window over rows silently bridges a day the stock has no record for
    (it did not trade, or that day's fetch failed) and reports a fake streak
    (§3.7 "最近 N 個交易日").

    The trading-day axis must be STOCK-INDEPENDENT. ``market_trading_days`` is
    the whole-market set of real trading days in range (passed from the caller
    BEFORE the flow frame is narrowed to the screened symbols). Deriving the
    axis from this already-filtered frame instead would shrink it to the dates
    the screened stocks happen to report, so in a single-/few-stock screen a day
    that stock is missing would silently drop off the axis and the gap would be
    bridged — a data-dependent fake streak. Each stock is reindexed onto the
    shared axis, so a day it is missing becomes a NaN net (neither >0 nor <0)
    that breaks both buy and sell streaks; holidays — absent from the whole
    market — are never invented. Only the dates the stock actually reported are
    returned, so downstream date alignment is unchanged.
    """
    flag_columns = _INVESTOR_FLAG_COLUMNS
    if investor.empty:
        for col in flag_columns:
            investor[col] = pd.Series(dtype=bool)
        return investor

    own_days = pd.DatetimeIndex(investor["Date"].unique())
    if market_trading_days is not None and len(market_trading_days) > 0:
        market_days = pd.DatetimeIndex(pd.to_datetime(pd.unique(market_trading_days)))
        # Union guards the (impossible-by-construction but cheap to defend) case
        # of a stock date absent from the market set, so no reported row is lost.
        trading_days = own_days.union(market_days).sort_values()
    else:
        trading_days = own_days.sort_values()
    window = max(int(consecutive_days), 1)

    def _streaks(group: pd.DataFrame) -> pd.DataFrame:
        deduped = group.drop_duplicates("Date", keep="last")
        indexed = deduped.set_index("Date").reindex(trading_days)
        foreign = indexed["foreign_net"]
        trust = indexed["trust_net"]

        def _ok(series: pd.Series, positive: bool) -> pd.Series:
            condition = series.gt(0) if positive else series.lt(0)
            return condition.rolling(window, min_periods=window).sum().eq(window)

        flags = pd.DataFrame(
            {
                "foreign_buy_streak_ok": _ok(foreign, True),
                "trust_buy_streak_ok": _ok(trust, True),
                "foreign_sell_streak_ok": _ok(foreign, False),
                "trust_sell_streak_ok": _ok(trust, False),
            },
            index=trading_days,
        )
        # Keep only the dates this stock actually reported; reindexed gap rows
        # were placeholders that must not become flow records of their own.
        return flags.reindex(deduped["Date"])

    flag_frames: list[pd.DataFrame] = []
    for base_code, group in investor.groupby("BaseCode", sort=False):
        flags = _streaks(group)
        flags = flags.reset_index().rename(columns={"index": "Date"})
        flags["BaseCode"] = base_code
        flag_frames.append(flags)

    flags_df = pd.concat(flag_frames, ignore_index=True)
    merged = investor.merge(flags_df, on=["BaseCode", "Date"], how="left")
    for col in flag_columns:
        merged[col] = merged[col].fillna(False).astype(bool)
    return merged


def attach_investor_flow_flags(
    df: pd.DataFrame,
    investor_flow_df: pd.DataFrame,
    consecutive_days: int = 3,
    market_trading_days=None,
) -> pd.DataFrame:
    """Attach recent N-day institutional buy/sell flags to bars by stock and date.

    ``market_trading_days`` is the whole-market trading-day axis (before the flow
    frame was filtered to screened symbols); it is threaded into the streak
    computation so the consecutive-day test does not silently bridge gaps in a
    small screen (§3.7). See _add_consecutive_streak_flags.
    """
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
    investor = investor.dropna(subset=["Date"]).sort_values(["BaseCode", "Date"]).reset_index(drop=True)
    investor = _add_consecutive_streak_flags(investor, consecutive_days, market_trading_days)

    # 先把法人表按 BaseCode 分組建索引，避免之後逐檔股票全表掃描
    # （全市場 1800 檔時是 O(檔數 × 法人列數) 的劣化點）。
    investor_groups: dict[str, pd.DataFrame] = {
        str(key).strip(): group for key, group in investor.groupby("BaseCode", sort=False)
    }

    merged_groups: list[pd.DataFrame] = []
    for base_code, stock_df in output.sort_values(["BaseCode", "Date"]).groupby("BaseCode", sort=False):
        flow_group = investor_groups.get(str(base_code).strip())
        flow_df = (
            flow_group[["Date", *flag_columns]]
            .drop_duplicates(subset=["Date"], keep="last")
            .sort_values("Date")
            .reset_index(drop=True)
            if flow_group is not None
            else pd.DataFrame(columns=["Date", *flag_columns])
        )
        if flow_df.empty:
            stock_output = stock_df.copy()
            for col in flag_columns:
                stock_output[col] = False
        else:
            try:
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
            except Exception as exc:
                # merge_asof can raise on pandas version / dtype edge cases.
                # Degrade this stock's investor flags to False instead of
                # aborting the whole screening run — but never silently.
                logger.warning("法人旗標合併失敗，%s 的法人條件降級為未達成：%s", base_code, exc)
                stock_output = stock_df.copy()
                for col in flag_columns:
                    stock_output[col] = False
        merged_groups.append(stock_output)

    merged = pd.concat(merged_groups, ignore_index=True)
    for col in flag_columns:
        merged[col] = pd.array(merged[col], dtype="boolean").fillna(False).astype(bool)
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
