# Bullish Three-Condition Stock Screener

A complete Python + Streamlit stock screening app for **Taiwanese stock research and screening only**. It uses publicly available information from the Taiwan Stock Exchange (TWSE) or other online public sources, downloads daily OHLCV data, resamples it into Daily / Weekly / Monthly K-bars, calculates the **Big Red Attack / Big Black Attack / Bullish Three-Condition Method**, shows interactive charts, and exports results to Excel for reference when considering purchases.

> **Important:** This tool uses publicly available information for screening reference only. It is **not investment advice**.

## Project structure

```text
app.py
config.py
data_loader.py
signal_engine.py
chart_engine.py
export_engine.py
requirements.txt
README.md
sample_stock_list.csv
```

## Features

- Upload a stock list by textarea or CSV
- Download daily OHLCV with `yfinance`
- Resample daily data into:
  - **Daily K**
  - **Weekly K**
  - **Monthly K**
- Calculate:
  - `prev_close`
  - Big Red Attack / Big Black Attack
  - `red_base`
  - `black_base`
  - `black_failed_base`
  - Bullish three-condition score
  - Optional volume filter
- Show:
  - download status
  - summary metrics
  - result table
  - interactive Plotly chart
  - Excel download

## Requirements

- Python 3.11+
- Streamlit
- pandas
- numpy
- yfinance
- plotly
- openpyxl

Install dependencies with:

```bash
pip install -r requirements.txt
```

## Run locally

From the project folder:

```bash
streamlit run app.py
```

## Deploy to Streamlit Cloud

1. Push this project to a Git repository.
2. Open [Streamlit Cloud](https://streamlit.io/cloud).
3. Create a new app and select your repository.
4. Set the main file path to:

   ```text
   app.py
   ```

5. Streamlit Cloud will install dependencies from `requirements.txt`.
6. Deploy.

This project does not use local absolute paths, so it is suitable for Streamlit Cloud deployment.

## Input stock format

The app accepts stock symbols in two ways:

1. **Textarea**  
   One stock symbol per line

2. **CSV upload**  
   The CSV must contain a column named:

   ```text
   StockCode
   ```

Example:

```csv
StockCode
2330.TW
2317.TW
2382.TW
2474.TW
6182.TWO
```

### Taiwan stock symbol handling

- If you enter a full symbol such as `2330.TW` or `6182.TWO`, the app uses it directly.
- If you enter a bare 4-digit Taiwan code such as `2330`, the app tries:
  1. `2330.TW`
  2. `2330.TWO`

## Timeframe behavior

The strategy logic stays the same across all timeframes. Only the K-bar timeframe changes.

- **Daily K**
  - Uses original daily OHLCV
  - `prev_close` = previous trading day close

- **Weekly K**
  - Built by resampling daily OHLCV with **W-FRI**
  - `Open` = first trading day open of the week
  - `High` = highest high of the week
  - `Low` = lowest low of the week
  - `Close` = last trading day close of the week
  - `Volume` = weekly summed volume
  - `Date` = actual last trading day of the week
  - `prev_close` = previous weekly close

- **Monthly K**
  - Built by resampling daily OHLCV with **month-end**
  - `Open` = first trading day open of the month
  - `High` = highest high of the month
  - `Low` = lowest low of the month
  - `Close` = last trading day close of the month
  - `Volume` = monthly summed volume
  - `Date` = actual last trading day of the month
  - `prev_close` = previous monthly close

> The app always downloads **daily** data first and resamples **before** calculating any signal. It does **not** calculate signals on daily data and then resample.

## Signal calculation flow

1. Download daily OHLCV
2. Resample by selected timeframe: Daily / Weekly / Monthly
3. Calculate `prev_close`
4. Calculate Big Red Attack / Big Black Attack
5. Calculate `red_base`, `black_base`, `black_failed_base`
6. Calculate the bullish three-condition method
7. Apply optional volume filter
8. Screen stocks

All `rolling`, `shift`, `ffill`, and resampling logic is grouped by `StockCode` to avoid mixing data from different stocks.

## Strategy definitions

### Basic derived fields

- `prev_close = previous K-bar close`
- `gap_pct = (Open - prev_close) / prev_close * 100`
- `close_vs_prev_pct = (Close - prev_close) / prev_close * 100`
- `body_pct = (Close - Open) / Open * 100`
- `volume_ma5 = Volume.rolling(5).mean()`
- `volume_ma20 = Volume.rolling(20).mean()`
- `volume_ratio_5 = Volume / volume_ma5`
- `volume_ratio_20 = Volume / volume_ma20`

### Big Red Attack / Big Black Attack

This strategy does **not** use ordinary red/green candle definitions.  
It uses **previous close** as the key reference point.

#### Big Red Attack Success

- `Open > prev_close`
- `Close > prev_close`
- If `min_gap_pct` is set:
  - `gap_pct >= min_gap_pct`
- If `min_close_vs_prev_pct` is set:
  - `close_vs_prev_pct >= min_close_vs_prev_pct`

Meaning: bulls opened above the previous close and still closed above it.

#### Big Red Attack Failed

- `Open > prev_close`
- `Close < prev_close`
- If `min_gap_pct` is set:
  - `gap_pct >= min_gap_pct`
- If `min_close_vs_prev_pct` is set:
  - `close_vs_prev_pct <= -min_close_vs_prev_pct`

Meaning: bulls opened above the previous close but failed to hold it.

#### Big Black Attack Success

- `Open < prev_close`
- `Close < prev_close`
- If `min_gap_pct` is set:
  - `gap_pct <= -min_gap_pct`
- If `min_close_vs_prev_pct` is set:
  - `close_vs_prev_pct <= -min_close_vs_prev_pct`

Meaning: bears opened below the previous close and still closed below it.

#### Big Black Attack Failed

- `Open < prev_close`
- `Close > prev_close`
- If `min_gap_pct` is set:
  - `gap_pct <= -min_gap_pct`
- If `min_close_vs_prev_pct` is set:
  - `close_vs_prev_pct >= min_close_vs_prev_pct`

Meaning: bears opened below the previous close but bulls recovered the level.

### Base lines

#### `red_base`

When `red_attack_success` is `True`:

- `red_base_raw = prev_close`
- Then forward-fill within each `StockCode`

Meaning: the support base left by a successful bullish attack.

#### `black_base`

When `black_attack_success` is `True`:

- `black_base_raw = prev_close`
- Then forward-fill within each `StockCode`

Meaning: the base price left by a successful bearish attack.

#### `black_failed_base`

When `black_attack_failed` is `True`:

- `black_failed_base_raw = prev_close`
- Then forward-fill within each `StockCode`

Meaning: support left behind when a bearish attack fails.

## Bullish three-condition method

A stock becomes a long-side candidate when at least two of the following three conditions appear within the recent lookback window.

### Condition A: Big Red appears

- `cond_A_red_attack_daily = red_attack_success`
- `cond_A_red_attack_window = rolling max of cond_A_red_attack_daily within lookback_days`

### Condition B: Break Big Black

- `cond_B_break_black_daily = Close > black_base * (1 + break_buffer_pct / 100)`
- If `black_base` is `NaN`, the result is `False`
- `cond_B_break_black_window = rolling max of cond_B_break_black_daily within lookback_days`

### Condition C: Retest base

Retest bases:

- `red_base`
- `black_base`
- `black_failed_base`

Retest rule:

- `Low <= base_price * (1 + retest_tolerance_pct / 100)`
- `Close >= base_price * (1 - retest_break_pct / 100)`

Then:

- `cond_C_retest_base_daily = retest_red_base_daily OR retest_black_base_daily OR retest_black_failed_base_daily`
- `cond_C_retest_base_window = rolling max of cond_C_retest_base_daily within lookback_days`

### Long score

```text
long_signal_score =
    cond_A_red_attack_window
  + cond_B_break_black_window
  + cond_C_retest_base_window
```

- Score range: **0 to 3**
- `long_signal = long_signal_score >= min_score`

## Volume filter modes

- **No volume filter**
- **Require 5-day volume ratio**
- **Require 20-day volume ratio**
- **Require both 5-day and 20-day volume ratios**

If volume filtering is enabled:

```text
final_long_signal = long_signal AND volume_filter_pass
```

If volume filtering is disabled:

```text
final_long_signal = long_signal
```

## Parameter meanings

- `start_date`: start date for daily download
- `end_date`: end date for daily download
- `analysis_timeframe`: Daily K / Weekly K / Monthly K
- `lookback_days`: number of recent K-bars used by the windowed conditions
- `min_gap_pct`: minimum open gap relative to `prev_close`
- `min_close_vs_prev_pct`: minimum close move relative to `prev_close`
- `break_buffer_pct`: required breakout margin above `black_base`
- `retest_tolerance_pct`: allowed overshoot above a base during retest detection
- `retest_break_pct`: allowed close below a base before the retest is considered broken
- `volume_filter_mode`: selected volume filter behavior
- `min_volume_ratio_5`: minimum ratio for `Volume / volume_ma5`
- `min_volume_ratio_20`: minimum ratio for `Volume / volume_ma20`
- `min_score`: required score for `long_signal`
- `only_latest_day`: show only the latest K-bar result for each stock
- `show_recent_signals`: show stocks with `final_long_signal` appearing within the recent lookback window

## Excel output

The download workbook contains these sheets:

- `All_Data`
- `Latest_Result`
- `Final_Long_Signal`
- `Score_3`
- `Score_2`
- `Red_Attack_Success`
- `Black_Attack_Success`
- `Black_Attack_Failed`
- `Parameter_Settings`

The `Parameter_Settings` sheet includes:

- `start_date`
- `end_date`
- `analysis_timeframe`
- `lookback_days`
- `min_gap_pct`
- `min_close_vs_prev_pct`
- `break_buffer_pct`
- `retest_tolerance_pct`
- `retest_break_pct`
- `volume_filter_mode`
- `min_volume_ratio_5`
- `min_volume_ratio_20`
- `min_score`

## Defensive design notes

- Empty stock list is handled safely
- Duplicate stock symbols are deduplicated
- Invalid symbols do not crash the app
- Failed downloads are isolated per stock
- Empty downloaded data is handled safely
- NaN `prev_close` rows do not create false attack signals
- NaN bases do not create false break/retest signals
- Zero denominators return `NaN`, not crashes or infinities
- Rolling calculations handle insufficient history safely
- Resampling is done per `StockCode`
- Weekly/monthly signals are calculated only **after** resampling
- Excel export is generated in memory and works on Streamlit Cloud

## Sample stock list

See `sample_stock_list.csv`.

## Disclaimer

This project is for **stock research and screening only**.  
It does **not** provide investment advice, trading advice, or recommendations.
