# Taiwan Stock Screener Specification

## 1. Goal

Provide a Streamlit-based Taiwan stock screening system for research only.

The app must:
- auto-load Taiwan listed and OTC common-stock symbols
- download daily OHLCV data from public internet sources
- resample into 日 K / 週 K / 月 K
- compute Big Red / Big Black Attack signals
- compute Three Methods conditions
- optionally filter by institutional flow streaks
- export reproducible results

This tool is **not investment advice**.

---

## 2. Product Scope

### Included
- Taiwan listed (`.TW`) and OTC (`.TWO`) common stocks
- Daily OHLCV via yfinance
- Weekly/monthly bars resampled from daily bars only
- Big Red / Big Black Attack
- Bullish / Bearish Three Methods
- Pullback tolerance (`pullback_pct`)
- Institutional flow filters:
  - foreign recent N-day consecutive net buy
  - trust recent N-day consecutive net buy
  - foreign recent N-day consecutive net sell
  - trust recent N-day consecutive net sell
- Excel export

### Excluded
- order placement
- broker integration
- backtesting engine
- portfolio management
- intraday data

---

## 3. Core Domain Rules

### 3.1 Attack direction
- `Open > prev_close` => only **Big Red Attack**
- `Open < prev_close` => only **Big Black Attack**
- failed attacks never convert to the opposite direction

### 3.2 Timeframe order
Always:
1. download daily data
2. resample to selected timeframe
3. calculate `prev_close`
4. calculate attack signals
5. calculate base lines / Three Methods

Signals must never be calculated on daily bars and then resampled.

### 3.3 Grouping invariant
Any rolling / shift / ffill / streak / resample logic must be grouped by stock identity.
Different stocks must never be mixed.

### 3.4 Three Methods exclusivity
For final Three Methods output:
- bullish if `bullish_methods_count > bearish_methods_count`
- bearish if `bearish_methods_count > bullish_methods_count`
- none if equal

The same stock must not appear as both bullish and bearish at the same time.

### 3.5 Investor streak rule
`investor_consecutive_days = N`

For a bar date:
- bullish foreign filter passes only if the **latest N trading days** all have `foreign_net > 0`
- bullish trust filter passes only if the **latest N trading days** all have `trust_net > 0`
- bearish foreign filter passes only if the **latest N trading days** all have `foreign_net < 0`
- bearish trust filter passes only if the **latest N trading days** all have `trust_net < 0`

This is not "any streak in a recent window".  
It is strictly the **latest N trading days ending at the mapped investor date**.

---

## 4. Data Contracts

### 4.1 TWSE ISIN universe page
Source:
- `https://isin.twse.com.tw/isin/C_public.jsp?strMode=2`
- `https://isin.twse.com.tw/isin/C_public.jsp?strMode=4`

Expected:
- HTML table exists
- target table has exactly 7 columns
- first column contains `4-digit code + stock name`
- common stock CFICode = `ESVUFR`

Failure policy:
- if table schema mismatches, raise a clear parsing error
- do not silently return an empty universe

### 4.2 TWSE institutional flow
Source:
- `TWSE /rwd/zh/fund/T86`

Expected:
- JSON `stat == "OK"`
- code column in position 0
- foreign net column in position 4
- trust net column in position 10

### 4.3 TPEX institutional flow
Source:
- `TPEX /web/stock/3insti/daily_trade/3itrade_hedge_result.php?...&o=json`

Expected:
- `tables[0]["data"]` exists
- code column in position 0
- foreign net column in position 4
- trust net column in position 13

### 4.4 yfinance OHLCV
Expected after normalization:
- `Date, Open, High, Low, Close, Volume, StockCode`

MultiIndex responses must be normalized before validation.

---

## 5. Stability Invariants

### 5.1 Resampling
- weekly uses actual last trading day of the week
- monthly uses actual last trading day of the month
- `Date` on resampled bars must be the last actual trading day, not calendar boundary

### 5.2 Investor mapping
- investor flow is daily
- mapping to K-bars must use the latest available investor date **on or before** the bar date
- investor flags must not propagate beyond the last available investor-flow date

### 5.3 External dependency stability
- supported parser path for TWSE universe must use valid pandas parser strategies only
- dependencies used in production must be declared in `requirements.txt`

### 5.4 UI / export consistency
- UI labels, internal parameters, and Excel parameter sheet must describe the same behavior
- configurable N-day investor streaks must never be labeled as fixed "3-day"

---

## 6. Required Output Contracts

### 6.1 Matching signals table
Must remain stable and sortable by:
1. `Date` descending
2. `StockCode` ascending

### 6.2 Three Methods result tables
Must include:
- stock identity
- final direction
- final score
- relevant investor streak pass flags

### 6.3 Excel export
Must include:
- all data
- matching signals
- latest summary
- bullish Three Methods
- bearish Three Methods
- failed downloads
- parameter settings

---

## 7. Failure Handling

The system must fail safely and explicitly for:
- invalid stock symbols
- empty OHLCV downloads
- missing required OHLCV columns
- external schema drift
- missing universe table
- investor endpoint failures

If one stock fails, remaining stocks must continue.

---

## 8. Validation Requirements

Minimum recurring validation after any strategy/data-layer change:
- syntax check on all Python entry files
- universe loader smoke test
- investor flow fetch smoke test
- screening path smoke test with manual symbols
- mock test for investor streak mapping
- mock test for exclusive bullish/bearish final direction

---

## 9. Change Discipline

Any future revision must update all affected surfaces together:
1. implementation
2. UI text
3. export parameter sheet
4. spec.md if behavior changed
5. plan.md execution phases if stability/debug priorities changed
