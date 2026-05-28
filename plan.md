# Stability Debug Plan

## Objective

Stabilize the Streamlit Taiwan stock screener so that:
- auto-universe loading is reliable
- investor-flow filters are reliable
- timeframe mapping is correct
- UI, internal logic, and export output stay aligned
- future revisions follow a predictable discipline

---

## Confirmed High-Risk Areas

### 1. External schema drift
- TWSE ISIN HTML may change table ordering/shape
- TWSE/TPEX investor JSON may change column layout
- yfinance may change MultiIndex output behavior

### 2. Time alignment
- daily -> weekly/monthly resampling
- `prev_close` calculation after resampling
- investor daily data mapped onto selected K-bar dates

### 3. Streamlit Cloud runtime differences
- parser availability
- SSL/certificate behavior
- pandas merge behavior

### 4. Surface mismatch after rapid hotfixes
- UI text may drift from actual logic
- export sheet parameters may drift from active filters
- field names may drift from current configurable behavior

### 5. Missing regression harness
- current project has smoke checks but not a stable test suite

---

## Execution Phases

### Phase 0 — Freeze baseline
- pin and verify production dependencies
- record tested library versions
- keep startup/runtime assumptions explicit

### Phase 1 — Data ingestion hardening
- harden TWSE ISIN parsing
- harden TWSE/TPEX investor schema checks
- fail loudly on schema mismatch

### Phase 2 — Time alignment validation
- verify weekly/monthly resampling
- verify actual last trading day handling
- verify investor daily flags never leak past data window

### Phase 3 — Signal pipeline validation
- validate attack direction exclusivity
- validate Three Methods exclusivity
- validate configurable investor streak days
- validate pullback rules against explicit examples

### Phase 4 — Surface consistency cleanup
- unify display labels
- unify export parameters
- ensure configurable behavior is reflected in captions/tables/Excel

### Phase 5 — Regression harness
- add repeatable smoke tests
- add focused mock tests for high-risk transformations
- document how each validation is run

### Phase 6 — Future change discipline
- require spec updates when behavior changes
- require plan updates when stability/debug priorities change
- require validation notes before pushing major strategy changes

---

## Immediate Debug Backlog

### A. Universe loading
- guard exact TWSE table shape
- handle multi-table responses safely
- keep valid parser fallback only

### B. Investor flow
- validate TWSE/TPEX expected column counts before `iloc`
- avoid stale flag propagation beyond investor data window
- avoid weekend/holiday underfetch for configurable N-day streaks

### C. Output consistency
- review `Timeframe` value display
- review latest summary label source-of-truth
- decide whether Excel should use Chinese display labels or internal names

### D. Operational resilience
- reduce silent empty-data paths
- make external endpoint failures diagnosable
- keep per-stock failures isolated

---

## Validation Matrix

### Must pass after each stabilization phase
1. `python3 -m py_compile app.py config.py data_loader.py signal_engine.py chart_engine.py export_engine.py`
2. universe loader smoke test
3. investor flow smoke test
4. `_run_screening()` smoke test with manual symbols

### Must be added as durable regression checks
1. weekly resample uses real final trading day
2. investor flags stop after last available investor date
3. equal bullish/bearish Three Methods score => no direction
4. invalid universe table shape raises clear error
5. yfinance MultiIndex normalization still yields OHLCV columns

---

## Files Most Likely To Change

- `data_loader.py`
- `signal_engine.py`
- `app.py`
- `config.py`
- `export_engine.py`
- `requirements.txt`

---

## Change Control Rules

Any future feature/hotfix touching strategy or data loading should update:
1. code
2. UI wording
3. export parameter sheet
4. `spec.md`
5. this `plan.md` when priorities or execution order change
