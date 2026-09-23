"""Signal calculation engine for the multi-direction (long/short) screener.

Four signal paths, routed by direction and grouped into two 路徑:
- 突破回測路徑 (secondary):
  - P1 BreakUp_Hold   (Long)  : upward breakout of red/black line, then within the
    retest window a 由上往下 retest that holds at/above the broken line.
  - P3 BreakDown_Reject (Short): downward break, then within the window a 由下往上
    retest rejected at/below the broken line.
- 新線路徑 (primary):
  - P2 NewLine_Hold   (Long)  : a freshly-appeared RED line, then a 由上往下 hold
    within the new-line window.
  - P4 NewLine_Reject (Short) : a freshly-appeared BLACK line, then a 由下往上 reject.

v3 added to every retest a direction precondition (the previous bar closed on the
approach side of the line), a post-event window (the event bar itself never
self-counts), and early invalidation (a bar closing through the line kills that
window). v4 binds each new line's colour to one direction and tracks the two
colours independently (§3.5c), stops a bar that OPENS well past the line from
counting as a test (開盤穿線不算測線, §3.5f), and drops any signal whose line a
later bar closed through (訊號後破線, §3.5g).
"""

from __future__ import annotations

import logging
from typing import NamedTuple

import numpy as np
import pandas as pd

from config import (
    BREAKOUT_RETEST_PATH,
    DEFAULT_PARAMETERS,
    INVESTOR_FLAG_COLUMNS as _INVESTOR_FLAG_COLUMNS,
    NEW_LINE_PATH,
    SIGNAL_COLUMNS,
)

logger = logging.getLogger(__name__)


class _PathSpec(NamedTuple):
    """How one path's per-bar flag becomes signal rows.

    The line a signal is judged against is ``line_price_col`` on the signal bar.
    Its type comes from ``line_type_col`` for the breakout paths (either colour
    can be broken) and is the fixed ``line_type`` for the new-line paths (the
    colour decides the direction).
    """

    raw_col: str
    broken_col: str
    final_col: str
    side: str
    direction: str
    signal_type: str
    path: str
    line_price_col: str
    line_type_col: str | None = None
    line_type: str | None = None


_PATH_SPECS = [
    _PathSpec(
        raw_col="p1_break_up_hold", broken_col="p1_broken_after", final_col="p1_final",
        side="long", direction="Long", signal_type="P1_BreakUp_Hold", path=BREAKOUT_RETEST_PATH,
        line_price_col="active_breakout_line_price", line_type_col="active_breakout_line_type",
    ),
    _PathSpec(
        raw_col="p2_new_line_hold", broken_col="p2_broken_after", final_col="p2_final",
        side="long", direction="Long", signal_type="P2_NewLine_Hold", path=NEW_LINE_PATH,
        line_price_col="red_line", line_type="Red Line",
    ),
    _PathSpec(
        raw_col="p3_break_down_reject", broken_col="p3_broken_after", final_col="p3_final",
        side="short", direction="Short", signal_type="P3_BreakDown_Reject", path=BREAKOUT_RETEST_PATH,
        line_price_col="active_breakdown_line_price", line_type_col="active_breakdown_line_type",
    ),
    _PathSpec(
        raw_col="p4_new_line_reject", broken_col="p4_broken_after", final_col="p4_final",
        side="short", direction="Short", signal_type="P4_NewLine_Reject", path=NEW_LINE_PATH,
        line_price_col="black_line", line_type="Black Line",
    ),
]


def _open_cross_tolerance(params: dict) -> float:
    """The 開盤穿線容許度 as a fraction of the line (param is in %, floored at 0)."""
    pct = params.get("open_cross_tolerance_pct", DEFAULT_PARAMETERS["open_cross_tolerance_pct"])
    return max(float(pct), 0.0) / 100.0


def _is_retest(output: pd.DataFrame, line: pd.Series, *, upward: bool, tolerance: float) -> pd.Series:
    """守住 (upward) / 壓回 (downward) of ``line`` on each bar — shared by all four paths.

    A 守住 is a 由上往下 test that holds: the previous bar closed on/above the
    line, this bar's Low reaches it, and it closes on/above it. The v4 open
    condition (§3.5f) adds that the bar did not OPEN below the line by more than
    ``tolerance`` (a fraction of the line): a bar that opens well past the line
    and only trades back during the session CROSSED the line rather than tested
    it. 壓回 is the exact mirror. The open condition only decides whether this
    bar is a test; it never invalidates a window — only a close through the line
    does that.
    """
    prev_close = output["prev_close"]
    if upward:
        return (
            (prev_close >= line)
            & (output["Low"] <= line)
            & (output["Close"] >= line)
            & (output["Open"] >= line * (1 - tolerance))
        )
    return (
        (prev_close <= line)
        & (output["High"] >= line)
        & (output["Close"] <= line)
        & (output["Open"] <= line * (1 + tolerance))
    )


def _breached_earlier(breach: pd.Series, keys: list) -> pd.Series:
    """True once an EARLIER bar of the same (stock, window) group breached.

    Prospective early invalidation: the breaching bar itself stays eligible and
    only LATER bars lose the window. Both the cumsum and the shift are taken per
    group, so a breach never leaks across windows or stocks.
    """
    return breach.astype("int64").groupby(keys).cumsum().groupby(keys).shift(1).fillna(0) > 0


def add_prev_close(df: pd.DataFrame) -> pd.DataFrame:
    """Add grouped prev_close = previous K-bar close, per StockCode."""
    # sort_values already returns a fresh, independent frame, so this is the
    # single defensive copy that shields the caller's input; the later pipeline
    # stages then mutate this owned frame in place (no further per-stage copies).
    output = df.sort_values(["StockCode", "Date"])
    output["prev_close"] = output.groupby("StockCode")["Close"].shift(1)
    return output


def add_attack_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Detect Big Red / Big Black attacks with independent boolean masks."""
    output = df  # pipeline stage: mutates the frame add_prev_close already owns

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
    output = df  # pipeline stage: mutates the owned frame in place
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
    close never clears the OLD level, so it fails this test. A previous close
    sitting exactly ON the line is eligible in BOTH directions (both
    preconditions admit equality); the strict comparison on the CURRENT close
    then decides which side fires, so the two can never both fire on one
    line/bar. Keep the equality handling symmetric — assigning it to one side
    only would break the long/short mirror.
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
    output = df  # pipeline stage: mutates the owned frame in place

    previous_close = output.groupby("StockCode")["Close"].shift(1)
    previous_red_line = output.groupby("StockCode")["red_line"].shift(1)
    previous_black_line = output.groupby("StockCode")["black_line"].shift(1)

    output["break_red_line_daily"] = _crosses_line(
        previous_close, previous_red_line, output["Close"], upward=True
    )
    output["break_black_line_daily"] = _crosses_line(
        previous_close, previous_black_line, output["Close"], upward=True
    )

    # Retest baseline L is the PREVIOUS (in-force) level that was crossed. When a
    # single line breaks, L is that line. When BOTH lines break on one bar, the
    # retest baseline is the HIGHER line — on a pullback that is the level price
    # touches first (§3.4). This is a signal rule, not the display priority
    # (which stays black-first and only tints the chart marker).
    both = output["break_red_line_daily"] & output["break_black_line_daily"]
    red_higher = previous_red_line >= previous_black_line
    output["breakout_line_type"] = np.select(
        [both & red_higher, both, output["break_red_line_daily"], output["break_black_line_daily"]],
        ["Red Line", "Black Line", "Red Line", "Black Line"],
        default="None",
    )
    output["breakout_line_price"] = np.select(
        [both & red_higher, both, output["break_red_line_daily"], output["break_black_line_daily"]],
        [previous_red_line, previous_black_line, previous_red_line, previous_black_line],
        default=np.nan,
    )
    return output


def add_breakdown_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Detect strict closes below the previous red_line or black_line (P3 trigger).

    Mirror of add_breakout_signals via _crosses_line(upward=False).
    """
    output = df  # pipeline stage: mutates the owned frame in place

    previous_close = output.groupby("StockCode")["Close"].shift(1)
    previous_red_line = output.groupby("StockCode")["red_line"].shift(1)
    previous_black_line = output.groupby("StockCode")["black_line"].shift(1)

    output["break_down_red_line"] = _crosses_line(
        previous_close, previous_red_line, output["Close"], upward=False
    )
    output["break_down_black_line"] = _crosses_line(
        previous_close, previous_black_line, output["Close"], upward=False
    )

    # Mirror of add_breakout_signals: when BOTH lines break down on one bar, the
    # retest baseline is the LOWER line — on a bounce that is the level price
    # touches first (§3.4). Display priority (red-first) is separate.
    both_down = output["break_down_red_line"] & output["break_down_black_line"]
    red_lower = previous_red_line <= previous_black_line
    output["breakdown_line_type"] = np.select(
        [both_down & red_lower, both_down, output["break_down_red_line"], output["break_down_black_line"]],
        ["Red Line", "Black Line", "Red Line", "Black Line"],
        default="None",
    )
    output["breakdown_line_price"] = np.select(
        [both_down & red_lower, both_down, output["break_down_red_line"], output["break_down_black_line"]],
        [previous_red_line, previous_black_line, previous_red_line, previous_black_line],
        default=np.nan,
    )
    return output


def _windowed_retest(
    output: pd.DataFrame,
    *,
    event_price_col: str,
    event_type_col: str,
    upward: bool,
    window: int,
    tolerance: float,
    active_price_out: str,
    active_type_out: str,
    bars_out: str,
    valid_out: str,
    retest_out: str,
) -> None:
    """Freeze L per breakout/breakdown event, window the retest, invalidate early.

    Vectorized, per (StockCode, event-group), no lookahead (§3.5a/b):
    - event group = cumsum of the event flag, so each group starts at its event
      bar; L = the event's line price, ffilled within the group. Before the first
      event the group is 0 and L is NaN (bars_since is masked to NaN there, §4.4).
    - bars_since = cumcount within the group (event bar = 0, excluded from the
      window so a breakout never self-counts as its own retest, §3.5a).
    - early invalidation: any bar in the group closing on the wrong side of L
      (long: Close < L; short: Close > L) kills the window for every LATER bar in
      that group. cumsum→shift(1) is taken WITHIN the group so the flag never
      leaks across groups/stocks.
    - the retest itself is _is_retest: the 由上往下 / 由下往上 approach, the touch,
      the close, and the v4 open condition (§3.5a/b/f).
    """
    stock = output["StockCode"]

    event = output[event_price_col].notna()
    group = event.groupby(stock).cumsum()
    keys = [stock, group]

    # Build the (StockCode, event-group) grouping once and reuse it for every
    # per-group op below instead of re-factorizing the same keys five times.
    grouped = output.groupby(keys)
    bars_since = grouped.cumcount()
    active_price = grouped[event_price_col].ffill()
    active_type = (
        output[event_type_col]
        .where(output[event_type_col] != "None")
        .groupby(keys)
        .ffill()
    )

    if upward:
        breach = active_price.notna() & (output["Close"] < active_price)
    else:
        breach = active_price.notna() & (output["Close"] > active_price)
    breach_before = _breached_earlier(breach, keys)

    window_valid = (
        active_price.notna()
        & (bars_since >= 1)
        & (bars_since <= window)
        & ~breach_before
    )
    retest = window_valid & _is_retest(output, active_price, upward=upward, tolerance=tolerance)

    output[active_price_out] = active_price
    output[active_type_out] = active_type
    output[bars_out] = bars_since.where(group >= 1)
    output[valid_out] = window_valid.fillna(False).astype(bool)
    output[retest_out] = retest.fillna(False).astype(bool)


def add_retest_hold_signals(
    df: pd.DataFrame,
    retest_window: int,
    open_cross_tolerance: float = 0.0,
) -> pd.DataFrame:
    """Directional, windowed retest of the broken / broken-down line (§3.5a/b).

    P1 long hold: within ``retest_window`` bars AFTER an upward breakout (the
    breakout bar itself excluded), the previous bar closed on/above the frozen
    line L (由上往下 precondition), this bar's Low touches L, Close holds at/above
    L, and it did not open below L by more than ``open_cross_tolerance`` (a
    fraction of L, §3.5f); the window dies early if any bar in it closes strictly
    below L. P3 short reject is the exact mirror. See _windowed_retest.
    """
    output = df  # pipeline stage: mutates the owned frame in place
    window = max(int(retest_window), 1)

    _windowed_retest(
        output,
        event_price_col="breakout_line_price",
        event_type_col="breakout_line_type",
        upward=True,
        window=window,
        tolerance=open_cross_tolerance,
        active_price_out="active_breakout_line_price",
        active_type_out="active_breakout_line_type",
        bars_out="bars_since_breakout",
        valid_out="breakout_window_valid",
        retest_out="retest_hold_daily",
    )
    _windowed_retest(
        output,
        event_price_col="breakdown_line_price",
        event_type_col="breakdown_line_type",
        upward=False,
        window=window,
        tolerance=open_cross_tolerance,
        active_price_out="active_breakdown_line_price",
        active_type_out="active_breakdown_line_type",
        bars_out="bars_since_breakdown",
        valid_out="breakdown_window_valid",
        retest_out="retest_reject_daily",
    )
    return output


def _new_line_window(
    output: pd.DataFrame,
    *,
    appeared: pd.Series,
    line_col: str,
    upward: bool,
    window: int,
    tolerance: float,
    bars_out: str,
    valid_out: str,
    retest_out: str,
) -> None:
    """Window one colour's newest line and flag its one-direction retest (§3.5c).

    Per (StockCode, same-colour line): the appearance bar is bar 0 and the window
    is bars 1..``window``. Only a newer line of the SAME colour starts a new
    group, so the other colour's lines never touch this window. The line level is
    ``line_col`` itself: the forward-filled red/black line only moves on a fresh
    same-colour attack success, which is exactly what starts a new group.
    Early invalidation: a window bar closing through the line (long: below,
    short: above) kills the window for every LATER bar of the same line.
    """
    stock = output["StockCode"]
    group = appeared.groupby(stock).cumsum()
    keys = [stock, group]
    bars_since = output.groupby(keys).cumcount()
    line = output[line_col]

    in_window = (group >= 1) & (bars_since >= 1) & (bars_since <= window)
    breach = in_window & ((output["Close"] < line) if upward else (output["Close"] > line))
    valid = in_window & ~_breached_earlier(breach, keys)
    retest = valid & _is_retest(output, line, upward=upward, tolerance=tolerance)

    output[bars_out] = bars_since.where(group >= 1)
    output[valid_out] = valid.fillna(False).astype(bool)
    output[retest_out] = retest.fillna(False).astype(bool)


def add_new_line_window_signals(
    df: pd.DataFrame,
    new_line_window: int,
    open_cross_tolerance: float = 0.0,
) -> pd.DataFrame:
    """Flag P2 on the newest red line and P4 on the newest black line (§3.5c).

    v4: a new line's colour decides its direction — a new red line only looks
    for a long 守住 (P2), a new black line only for a short 壓回 (P4) — and the two
    colours are tracked independently, so a new black line never ends a red
    line's window or vice versa. The appearance bar itself (bars_since == 0) is
    excluded: it closes on the line's own side by construction and would merely
    duplicate the attack.
    """
    output = df  # pipeline stage: mutates the owned frame in place
    window = max(int(new_line_window), 1)

    _new_line_window(
        output,
        appeared=output["red_attack_success"].fillna(False).astype(bool),
        line_col="red_line",
        upward=True,
        window=window,
        tolerance=open_cross_tolerance,
        bars_out="bars_since_new_red_line",
        valid_out="new_red_line_window_valid",
        retest_out="p2_new_line_hold",
    )
    _new_line_window(
        output,
        appeared=output["black_attack_success"].fillna(False).astype(bool),
        line_col="black_line",
        upward=False,
        window=window,
        tolerance=open_cross_tolerance,
        bars_out="bars_since_new_black_line",
        valid_out="new_black_line_window_valid",
        retest_out="p4_new_line_reject",
    )
    return output


def add_path_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the four per-bar path booleans (pre volume / lookback gating)."""
    output = df  # pipeline stage: mutates the owned frame in place
    output["p1_break_up_hold"] = output["retest_hold_daily"].fillna(False).astype(bool)
    output["p2_new_line_hold"] = output["p2_new_line_hold"].fillna(False).astype(bool)
    output["p3_break_down_reject"] = output["retest_reject_daily"].fillna(False).astype(bool)
    output["p4_new_line_reject"] = output["p4_new_line_reject"].fillna(False).astype(bool)
    return output


def add_broken_after_signals(df: pd.DataFrame) -> pd.DataFrame:
    """訊號後破線 (§3.5g): flag signal bars whose line a LATER bar closed through.

    For each path's signal bar, look at every later bar of the same stock up to
    its latest bar: a long signal is broken once any of them closes below the
    line the signal used, a short signal once any closes above it. This is not
    bounded by the observation window or the lookback — a signal is only listed
    while it still stands. Computed from per-stock suffix min/max of Close
    (reverse cummin/cummax, shifted so the signal bar itself is excluded).
    Positional (numpy) on the [StockCode, Date]-sorted frame, so the index plays
    no part in the alignment.
    """
    output = df  # pipeline stage: mutates the owned frame in place
    close_rev = pd.Series(output["Close"].to_numpy()[::-1])
    stock_rev = pd.Series(output["StockCode"].to_numpy()[::-1])

    def _later(running: pd.Series) -> np.ndarray:
        # cummin/cummax leave NaN on a bar with no Close; ffill carries the
        # running extreme across it so a missing close never hides a later
        # breach from the bars before it.
        return running.groupby(stock_rev).ffill().groupby(stock_rev).shift(1).to_numpy()[::-1]

    later_min = _later(close_rev.groupby(stock_rev).cummin())
    later_max = _later(close_rev.groupby(stock_rev).cummax())

    for spec in _PATH_SPECS:
        line = output[spec.line_price_col].to_numpy(dtype=float)
        with np.errstate(invalid="ignore"):
            crossed = later_min < line if spec.side == "long" else later_max > line
        output[spec.broken_col] = output[spec.raw_col].fillna(False).astype(bool).to_numpy() & crossed
    return output


def add_final_filters(df: pd.DataFrame, lookback_bars: int, min_volume: int) -> pd.DataFrame:
    """Gate each path by volume + lookback and drop signals broken afterwards."""
    # No re-sort: add_prev_close already ordered the frame by [StockCode, Date]
    # and no stage reorders it, so sorting again only paid for another whole-frame
    # copy of the widest frame in the pipeline.
    output = df
    group_sizes = output.groupby("StockCode")["Date"].transform("size")
    row_number = output.groupby("StockCode").cumcount()

    output["volume_pass"] = output["Volume"].fillna(0) >= int(min_volume)
    output["lookback_rank"] = group_sizes - row_number
    gate = output["volume_pass"] & (output["lookback_rank"] <= int(lookback_bars))

    for spec in _PATH_SPECS:
        output[spec.final_col] = output[spec.raw_col].fillna(False) & gate & ~output[spec.broken_col]
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

    # Vectorized across ALL stocks at once (was a per-BaseCode Python loop doing
    # reindex + four rollings each): pivot the net flows to a
    # (trading_day x BaseCode) matrix, reindex onto the shared axis (a day the
    # stock is missing becomes NaN, which fails both > 0 and < 0 and so breaks
    # the streak), then roll the >0 / <0 condition down the DATE axis per column.
    # Identical semantics to the old _streaks, one grouping instead of N.
    deduped = investor.drop_duplicates(["BaseCode", "Date"], keep="last")
    foreign_mat = deduped.pivot(index="Date", columns="BaseCode", values="foreign_net").reindex(trading_days)
    trust_mat = deduped.pivot(index="Date", columns="BaseCode", values="trust_net").reindex(trading_days)

    def _streak_matrix(matrix: pd.DataFrame, positive: bool) -> pd.DataFrame:
        condition = matrix.gt(0) if positive else matrix.lt(0)
        return condition.rolling(window, min_periods=window).sum().eq(window)

    flag_matrices = {
        "foreign_buy_streak_ok": _streak_matrix(foreign_mat, True),
        "trust_buy_streak_ok": _streak_matrix(trust_mat, True),
        "foreign_sell_streak_ok": _streak_matrix(foreign_mat, False),
        "trust_sell_streak_ok": _streak_matrix(trust_mat, False),
    }
    flags_long = pd.DataFrame(
        {name: matrix.stack() for name, matrix in flag_matrices.items()}
    )
    flags_long.index = flags_long.index.set_names(["Date", "BaseCode"])
    flags_long = flags_long.reset_index()

    merged = investor.merge(flags_long, on=["BaseCode", "Date"], how="left")
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
    # .strip() matches the investor side (below) and the pre-v3.2.0 per-stock
    # loop, which looked the group up with str(base_code).strip(). Dropping it in
    # the merge_asof rewrite made a StockCode carrying stray whitespace silently
    # match nothing, zeroing every investor flag for that stock.
    output["BaseCode"] = output["StockCode"].astype(str).str.split(".").str[0].str.strip()
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

    # Single grouped as-of merge (was a per-BaseCode Python loop, O(stocks) each
    # scanning its own flow slice): attach each bar the most recent investor row
    # on/before its date, per BaseCode, in one pd.merge_asof(by="BaseCode") call.
    flow_flags = (
        investor[["BaseCode", "Date", *flag_columns]]
        .drop_duplicates(subset=["BaseCode", "Date"], keep="last")
        .sort_values("Date")
        .reset_index(drop=True)
    )
    output = output.drop(columns=[col for col in flag_columns if col in output.columns])
    output_sorted = output.sort_values("Date").reset_index(drop=True)
    try:
        merged = pd.merge_asof(
            output_sorted, flow_flags, on="Date", by="BaseCode", direction="backward"
        )
        # A flag must not extend past each stock's last investor date (§3.7): the
        # backward as-of would otherwise carry the last streak value forward
        # indefinitely. Blank out bars dated after the stock's last flow row.
        last_flow_date = flow_flags.groupby("BaseCode")["Date"].max()
        future_mask = merged["Date"] > merged["BaseCode"].map(last_flow_date)
        for col in flag_columns:
            merged[col] = pd.array(merged[col], dtype="boolean").fillna(False).astype(bool)
        if future_mask.any():
            merged.loc[future_mask.fillna(False), flag_columns] = False
    except Exception as exc:
        # merge_asof can raise on pandas version / dtype edge cases. Degrade ALL
        # investor flags to False (conservative — can only drop signals, never
        # invent them) instead of aborting the whole run, but never silently.
        logger.warning("法人旗標合併失敗，全部法人條件降級為未達成：%s", exc)
        merged = output_sorted
        for col in flag_columns:
            merged[col] = False

    for col in flag_columns:
        merged[col] = pd.array(merged[col], dtype="boolean").fillna(False).astype(bool)
    return merged.sort_values(["StockCode", "Date"]).reset_index(drop=True)


def build_direction_signals(processed_df: pd.DataFrame, params: dict) -> dict:
    """Explode the wide per-bar frame into long/short signal rows (§3.8).

    Each path that passes (with its direction-aware investor gate, §3.7) becomes
    rows in either ``long_signals`` (P1/P2) or ``short_signals`` (P3/P4), each row
    tagged with its ``path`` (新線路徑 / 突破回測路徑). A single bar matching
    multiple paths produces multiple rows. Returns a dict with keys
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
    for spec in _PATH_SPECS:
        if spec.final_col not in df.columns:
            continue
        gate = long_gate if spec.side == "long" else short_gate
        mask = df[spec.final_col].fillna(False).astype(bool) & gate
        if not mask.any():
            continue
        subset = df.loc[mask].copy()
        subset["direction"] = spec.direction
        subset["path"] = spec.path
        subset["signal_type"] = spec.signal_type
        subset["retest_line_type"] = subset[spec.line_type_col] if spec.line_type_col else spec.line_type
        subset["retest_line_price"] = subset[spec.line_price_col]
        frame = subset.reindex(columns=SIGNAL_COLUMNS)
        (long_rows if spec.side == "long" else short_rows).append(frame)

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
    open_cross_tolerance = _open_cross_tolerance(params)
    output = add_retest_hold_signals(
        output,
        retest_window=int(params.get("retest_window", 5)),
        open_cross_tolerance=open_cross_tolerance,
    )
    output = add_new_line_window_signals(
        output,
        new_line_window=int(params.get("new_line_window", 5)),
        open_cross_tolerance=open_cross_tolerance,
    )
    output = add_path_signals(output)
    output = add_broken_after_signals(output)
    output = add_final_filters(
        output,
        lookback_bars=int(params.get("lookback_bars", 10)),
        min_volume=int(params.get("min_volume", 2000)),
    )
    # Already ordered by add_prev_close; only the index needs normalizing.
    return output.reset_index(drop=True)
