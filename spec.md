# Taiwan Stock Screener Specification

## 1. Goal

Provide a Streamlit-based Taiwan stock screening system for research only.

The app must find stocks that:

1. broke above the latest Big Red line or Big Black line,
2. later retested that broken line,
3. closed at or above that line,
4. passed the recent-window, volume, and optional institutional-flow filters.

This tool is not investment advice.

## 2. Product Scope

Included:

- Taiwan listed (`.TW`) and OTC (`.TWO`) common stocks
- Daily OHLCV via yfinance
- Weekly/monthly bars resampled from daily bars only
- Big Red / Big Black Attack signal detection
- Red line / black line creation from successful attacks
- Breakout above red or black line
- Retest-hold screening
- Optional institutional flow filters
- Plotly candlestick chart
- Excel export

Excluded:

- order placement
- broker integration
- backtesting engine
- portfolio management
- intraday data
- Three Methods scoring
- breakout buffer, retest tolerance, volume ratio, moving-average volume filters

## 3. Core Domain Rules

### 3.1 Attack Direction

- `Open > prev_close` means only Big Red Attack.
- `Open < prev_close` means only Big Black Attack.
- Failed attacks never convert to the opposite direction.

### 3.2 Attack Success

- Big Red Attack Success: `Open > prev_close` and `Close > prev_close`
- Big Red Attack Failed: `Open > prev_close` and `Close < prev_close`
- Big Black Attack Success: `Open < prev_close` and `Close < prev_close`
- Big Black Attack Failed: `Open < prev_close` and `Close > prev_close`

### 3.3 Lines

- `red_line_raw = prev_close` only when Big Red Attack Success is true.
- `black_line_raw = prev_close` only when Big Black Attack Success is true.
- `red_line` and `black_line` are forward-filled separately within each `StockCode`.

### 3.4 Breakout

- Break red line: previous `Close <= previous red_line` and current `Close > current red_line`.
- Break black line: previous `Close <= previous black_line` and current `Close > current black_line`.
- If both break on the same K-bar, black line has display priority.

### 3.5 Retest Hold

- The latest broken line is forward-filled per stock as the active breakout line.
- Valid retest hold: `Low <= active_breakout_line_price` and `Close >= active_breakout_line_price`.
- If `Close < active_breakout_line_price`, it is not a valid signal.

### 3.6 Timeframe Order

Always:

1. download daily data
2. resample to selected timeframe
3. calculate `prev_close`
4. calculate attack signals
5. create red/black lines
6. detect breakouts
7. detect retest hold
8. apply lookback, volume, and optional institutional filters

Signals must never be calculated on daily bars and then resampled.

### 3.7 Investor Streak Rule

`investor_consecutive_days = N`

For a bar date:

- foreign buy filter passes only if the latest N trading days all have `foreign_net > 0`
- trust buy filter passes only if the latest N trading days all have `trust_net > 0`
- foreign sell filter passes only if the latest N trading days all have `foreign_net < 0`
- trust sell filter passes only if the latest N trading days all have `trust_net < 0`

Investor flow maps to each K-bar using the latest available investor date on or before the bar date, and flags must not propagate beyond the last available investor-flow date.

## 4. Output Contracts

### 4.1 Matching Retest Hold

Rows where `final_signal == True`, sorted by:

1. `Date` descending
2. `StockCode` ascending

### 4.2 Latest Summary

One row per stock showing the latest valid retest-hold signal inside the lookback window.

### 4.3 Excel Export

Sheets:

- `All_Data`
- `Matching_Retest_Hold`
- `Latest_Summary`
- `Failed_Downloads`
- `Parameter_Settings`

## 5. Stability Invariants

- Weekly uses the actual last trading day of the week.
- Monthly uses the actual last trading day of the month.
- Any shift, forward-fill, rolling, and resampling logic must be grouped by stock.
- External source schema drift should fail clearly.
- UI labels, internal parameters, and Excel parameter sheet must describe the same behavior.
