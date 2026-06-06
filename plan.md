# v2 多空雙向篩選器實作計畫

## Context（為什麼要做這個變更）

`spec.md` 已升級為 **v2：多空雙向**。現行程式（v1）只做「做多」的單一路徑：向上突破紅／黑線後，回測守在線上（`final_signal` 即此單一訊號）。v2 要把篩選器擴充為**四條訊號路徑、依方向（做多／做空）分流**：

| 路徑 | 方向 | 觸發 | 回測判定 |
|------|------|------|----------|
| P1 突破回測 | 做多 | 向上突破紅／黑線 | 守在線上：`Low<=線` 且 `Close>=線`（= 現行 v1 行為） |
| P2 新線回測 | 做多 | 新紅／黑線出現（不需突破） | 守在線上 |
| P3 跌破回測 | 做空 | 向下跌破紅／黑線 | 壓在線下：`High>=線` 且 `Close<=線` |
| P4 新線回測 | 做空 | 新紅／黑線出現（不需突破） | 壓在線下 |

一根 K 棒可同時符合多條路徑，需依方向**分別輸出為多列**（§3.8）。輸出契約改為做多／做空兩組（§4），Excel 工作表與 Streamlit 分頁皆分流（§4.3、§4.4）。新增兩個參數：`new_line_window`（預設 5）與 `direction_filter`（全部／做多／做空）。

**已與使用者確認的決策：**
1. **保留** v1 既有的「最小成交量（張）」過濾（對應 §3.6 步驟 8 的「量能」）。
2. P2/P4 的新線窗格**不含出現當根**，有效範圍為出現後第 1～N 根 K 棒。

目標：在不破壞 v1 既有穩定性修補（B1–B6）與資料載入流程的前提下，把訊號引擎、UI、Excel、圖表、測試一致地升級到 v2，並讓 UI 文字／內部參數／Excel 參數表三者描述相同行為（§5 不變式）。

---

## 訊號引擎改動 — [signal_engine.py](signal_engine.py)

設計原則：**保留所有 v1 欄位名**（`break_red_line_daily`、`break_black_line_daily`、`breakout_line_type/price`、`active_breakout_line_type/price`、`retest_hold_daily`）對應做多／向上側 → 即 P1。新增做空／向下與新線側欄位。所有 `shift / ffill / cumcount / rolling` 一律以 `StockCode` 分組（§5）。

1. **改 `add_attack_lines`**：不再 drop `red_line_raw` / `black_line_raw`。在 raw 仍在時新增「新線出現」欄位（§3.3）：
   - `new_line_appeared = red_line_raw.notna() | black_line_raw.notna()`（紅黑攻在同一根互斥，故無歧義）。
   - `new_line_type`、`new_line_price` 以 `np.select` 取出當根出現的線型與線價（沿用 v1 `"None"`/`NaN` 哨兵慣例）。

2. **保留 `add_breakout_signals`**（P1 觸發源，向上突破，黑線優先）。

3. **新增 `add_breakdown_signals(df)`**（P3 觸發源，§3.4b，**紅線優先**，嚴格 `<`）：
   - `break_down_red_line = prev_red_line.notna() & red_line.notna() & (prev_close >= prev_red_line) & (Close < red_line)`；黑線同理。
   - `breakdown_line_type = np.select([break_down_red_line, break_down_black_line], ["Red Line","Black Line"], default="None")`（紅優先）。`breakdown_line_price` 對應取值。
   - 等號邊界：`prev_close == 線` 時，因收盤檢定 `>` 與 `<` 互斥，自然歸為「突破」而非「跌破」（§3.4 衝突序、§5）。

4. **改 `add_retest_hold_signals`**：保留 P1（`active_breakout_line_*` + `retest_hold_daily`）不動；新增 P3：
   - `active_breakdown_line_type/price = breakdown_line_type/price` 以 `StockCode` 向前填補。
   - `retest_reject_daily = active_breakdown_line_price.notna() & (High >= price) & (Close <= price)`（§3.5b）。

5. **新增 `add_new_line_window_signals(df, new_line_window)`**（P2/P4 基準，§3.5c）：
   - `new_line_group_id = groupby(StockCode)["new_line_appeared"].cumsum()`（先 `fillna(False).astype(bool)`）。
   - `bars_since_new_line = groupby([StockCode, new_line_group_id]).cumcount()`（出現當根=0、次根=1…）。
   - `active_new_line_type/price = new_line_type/price` 在出現處向前填補。
   - `new_line_window_valid = active_new_line_price.notna() & (bars_since_new_line >= 1) & (bars_since_new_line <= new_line_window)`（**不含當根**，1..N）。
   - `p2_new_line_hold = new_line_window_valid & (Low <= price) & (Close >= price)`。
   - `p4_new_line_reject = new_line_window_valid & (High >= price) & (Close <= price)`。

6. **新增 `add_path_signals(df)`**：彙整四條路徑的「未過量能／回看」布林：
   - `p1_break_up_hold = retest_hold_daily`、`p2_new_line_hold`、`p3_break_down_reject = retest_reject_daily`、`p4_new_line_reject`。

7. **改 `add_final_filters(df, lookback_bars, min_volume)`**：量能與回看為方向無關，計算一次後對四路徑各自 AND：
   - `gate = volume_pass & (lookback_rank <= lookback_bars)`（`volume_pass`、`lookback_rank` 邏輯不變）。
   - `p1_final = p1_break_up_hold & gate`…（p2/p3/p4 同）。
   - `final_signal = p1_final | p2_final | p3_final | p4_final`（供 `All_Data` 與摘要計數使用）。**注意：此定義由「僅做多回測」改為「任一路徑」，是主要回歸風險點（見測試節）。**

8. **新增 `build_direction_signals(processed_df, params) -> dict`**：方向感知的法人過濾 + 展開為多列。**取代** app 層舊的 `_apply_selected_investor_filters`。
   - 法人過濾方向感知（§3.7）：做多（P1/P2）只 AND 已勾選的**連買**旗標（`foreign_buy_streak_ok`/`trust_buy_streak_ok`）；做空（P3/P4）只 AND 已勾選的**連賣**旗標。某方向未勾選任何法人條件 → 該方向不過濾。
   - 每條路徑取通過列，正規化為共用 schema 後 `concat`：P1+P2→`long_signals`，P3+P4→`short_signals`。
   - 共用欄位（順序）：`Date, Timeframe, StockCode, StockName, Open, High, Low, Close, Volume, prev_close, direction, signal_type, retest_line_type, retest_line_price, foreign_buy_streak_ok, trust_buy_streak_ok, foreign_sell_streak_ok, trust_sell_streak_ok`。
   - `signal_type ∈ {P1_BreakUp_Hold, P2_NewLine_Hold, P3_BreakDown_Reject, P4_NewLine_Reject}`；`direction ∈ {Long, Short}`。
   - 依 `direction_filter`（全部／做多／做空）保留對應側；各組依 `Date` 由新到舊、`StockCode` 由小到大排序（§4.1）。
   - 無符合時回傳帶完整欄位的空表（避免下游崩潰）。

9. **改 `run_signal_pipeline` 串接順序（對齊 §3.6）**：
   `add_prev_close → add_attack_signals → add_attack_lines → add_breakout_signals → add_breakdown_signals → add_retest_hold_signals → add_new_line_window_signals(window) → add_path_signals → add_final_filters`。
   `new_line_window` 由 `params.get("new_line_window", 5)` 取得。重取樣仍在 app 層於 pipeline 前完成（訊號絕不在日線算完後才重取樣，§3.6/§5）。

---

## 設定改動 — [config.py](config.py)

- `APP_VERSION = "2.0.0"`、更新 `APP_UPDATED`；`APP_TITLE` 改為反映「多空雙向」。
- `DEFAULT_PARAMETERS`：新增 `new_line_window: 5`、`direction_filter: "全部"`；**保留** `min_volume: 2000`。
- 新增 `DIRECTION_FILTER_OPTIONS = ["全部", "做多", "做空"]`（或 dict 對應 Long/Short）。
- `RESULT_COLUMNS`（`All_Data` 用）：加入 `break_down_red_line, break_down_black_line, breakdown_line_type, breakdown_line_price, active_breakdown_line_type, active_breakdown_line_price, retest_reject_daily, new_line_type, new_line_price, bars_since_new_line, active_new_line_type, active_new_line_price, p1_break_up_hold, p2_new_line_hold, p3_break_down_reject, p4_new_line_reject`。
- 新增 `SIGNAL_COLUMNS`（`Long_Signals`/`Short_Signals` 用，即 `build_direction_signals` 共用 schema）。
- `LATEST_SUMMARY_COLUMNS`：加入 `Direction`、`SignalType`，回測線改用 `retest_line_type/price`。
- `DISPLAY_COLUMN_LABELS`：補上以上所有新欄位的中文標籤（含 `direction→方向`、`signal_type→訊號路徑`、`retest_line_type→回測線類型`、`retest_line_price→回測線價格`、`bars_since_new_line→新線後第幾根` 等）。
- `EXCEL_SHEET_LABELS`：移除 `Matching_Retest_Hold`/`Latest_Summary`，新增 `Long_Signals→做多訊號`、`Short_Signals→做空訊號`、`Latest_Summary_Long→做多最新摘要`、`Latest_Summary_Short→做空最新摘要`。
- `EXCEL_PARAMETER_LABELS`：新增 `new_line_window→新線回測窗格（交易日）`、`direction_filter→方向過濾`。

---

## App 改動 — [app.py](app.py)

- `_build_params`：新增 `new_line_window`、`direction_filter` 兩參數並帶入。
- 側邊欄「篩選條件」新增：`new_line_window` 數字輸入（`min_value=1`，預設 5），`direction_filter` selectbox（全部／做多／做空）。保留「最小成交量（張）」。法人條件四個勾選維持（買×2、賣×2），並在說明文字標註對應做多／做空（§3.7）。
- `_run_screening`：pipeline params 帶入 `new_line_window`；`attach_investor_flow_flags` 後 join `StockName`，改呼叫 `build_direction_signals(processed, params)` 取得 `long_signals`/`short_signals`；以 `_compute_latest_summary` 分別對兩組產生 `latest_summary_long`/`latest_summary_short`（依 `StockCode` 取最新，讀 `signal_type`/`retest_line_*`）。移除舊 `_apply_selected_investor_filters` 與 `_compute_matching_retest_hold`。
- 結果區改用 `st.tabs(["做多", "做空"])`（§4.4）：每個分頁顯示該方向的訊號表、最新摘要表、與該方向 K 線圖選股。`direction_filter` 為單一方向時，另一分頁顯示空表提示。
- 摘要 metric 擴充：突破棒數、跌破棒數、守住棒數、壓回棒數（以 `break_*`、`break_down_*`、`retest_hold_daily`、`retest_reject_daily` 計）。
- Excel 下載呼叫改傳 `long_signals/short_signals/latest_summary_long/latest_summary_short`。CSV 下載依目前分頁方向匯出。
- session_state 結果字典欄位同步更新（`long_signals`/`short_signals`/兩個摘要）。

---

## 圖表改動 — [chart_engine.py](chart_engine.py)

- `create_stock_chart` 新增 `direction` 參數（或一律顯示全部標記）。
- `_MARKER_STYLES` 新增向下跌破標記（`break_down_red_line`/`break_down_black_line`，`triangle-down`，置於 `Low` 下方）。
- 回測標記區塊新增做空壓回（`retest_reject_daily`，依 `active_breakdown_line_type` 紅／黑，置於 `High` 上方）與新線回測（P2/P4，可選）。
- 標題反映方向（做多／做空）。紅黑線繪製不變。

---

## Excel 匯出改動 — [export_engine.py](export_engine.py)

- `create_excel_bytes` 參數改為 `long_signals, short_signals, latest_summary_long, latest_summary_short`（取代 `matching_retest_hold, latest_summary`）。
- 工作表固定為（§4.3）：`All_Data`、`Long_Signals`、`Short_Signals`、`Latest_Summary_Long`、`Latest_Summary_Short`、`Failed_Downloads`、`Parameter_Settings`。
- 參數工作表新增 `new_line_window`、`direction_filter`（保留 `min_volume`）。`Failed_Downloads` 的失敗代號欄與 `DiagnosticNote` 維持 v1 行為（§4.3、§5.1）。

---

## 測試改動 — [tests/test_stability.py](tests/test_stability.py)

沿用既有「以字典自建合成 OHLCV → `run_signal_pipeline(frame, {...})`」模式（無 fixture／conftest）。

- **更新 `test_breakout_and_retest_hold_are_final_signal`**：保留既有斷言；加 `p1_break_up_hold == True`、`p3_break_down_reject == False`。
- **更新 `test_retest_failure_is_not_final_signal`**（回歸熱點）：在 v2 下，跌破基準線的失敗回測棒會合理觸發 P4（新線壓回），`final_signal`（OR）變 True。改為斷言路徑層級：`retest_hold_daily == False` 且 `p1_break_up_hold == False`，並明確斷言 `p4_new_line_reject == True`（記錄 v2 正確行為），不再斷言 `final_signal == False`。
- **新增測試**：
  1. 向下跌破設定 `active_breakdown_line_price`（紅優先）。
  2. P3 壓回：`High>=線 & Close<=線` → `p3_break_down_reject == True`。
  3. P3 壓回失敗：`Close>線` → False。
  4. 新線窗格：出現後第 N 根內 P2/P4 有效、第 N+1 根失效；出現當根不觸發（不含當根）。
  5. P4 壓回於窗格內成立。
  6. 方向分流：同根同時符合多路徑 → `build_direction_signals` 產出多列且 `signal_type`/`direction` 正確、跨方向不去重。
  7. 等號邊界：`prev_close == 線` 歸「突破」不歸「跌破」。
  8. 法人方向感知：只勾連買時做空側不被該條件過濾，反之亦然。

---

## 文件改動

- 視需要更新 [README.md](README.md) 說明多空雙向與新參數。

---

## 驗證方式

1. **語法**：`python3 -m py_compile app.py config.py data_loader.py signal_engine.py chart_engine.py export_engine.py`。
2. **單元／回歸測試**：`pytest tests/test_stability.py`（既有全綠 + 新增四路徑測試全綠）。
3. **本機端到端**：`streamlit run app.py`
   - 用 `2330.TW`、`2454.TW` 與一個不存在的 `9999` 跑一次：失敗代號仍進 `Failed_Downloads`／`st.warning`，不整頁崩潰（守住 v1 §5.1）。
   - 確認「做多」「做空」兩分頁各自顯示訊號表、最新摘要與 K 線圖；切換 `direction_filter` 驗證單向時另一分頁為空。
   - 調整 `new_line_window`（如 1 vs 10）觀察 P2/P4 結果數量隨窗格變化。
   - 下載 Excel：確認 7 張工作表齊全、`Parameter_Settings` 含 `new_line_window`／`direction_filter`／最小成交量，且 UI／參數表行為一致（§5）。
4. **不變式抽查**：週／月線用實際最後交易日；法人旗標不延伸至最後法人日期之後；做多一律 `Close>=線`、做空一律 `Close<=線`（§5）。
