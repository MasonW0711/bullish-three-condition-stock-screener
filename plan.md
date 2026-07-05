# v3 回測方向性實作計畫

## Context（為什麼要做這個變更）

`spec.md` 已升級為 **v3：多空雙向＋回測方向性**。v2 的回測判定只有「觸線＋收盤守住 / 壓回」（做多 `Low<=線 且 Close>=線`），**沒有方向前提**，造成：

1. 突破棒自己常同時被算成「回測成立」（由下往上穿線的棒盤中觸線、收在線上），P1 訊號經常發在突破當根——但那是突破，不是「由上往下」的回測。
2. 任何跨線棒都算回測，不分「從線上方壓下來測試」還是「從下方衝上來」。

v3 依使用者定義修正：**突破＝由下往上、收盤確認在線上；回測＝由上往下測線、收盤守住**（做空完全鏡像）。

**已與使用者確認的決策（2026-07-05）：**

1. 「由上往下」判定 = **回測棒的前一根收盤在線上**（做多 `prev Close >= L`；做空鏡像 `<= L`）。
2. 突破棒與回測棒**必須不同根**；回測須在突破後 `retest_window` 根內（新參數，預設 5）。
3. 紅線／黑線**不分色**：維持 v2，兩色皆可雙向突破。
4. P2/P4 新線路徑**保留**，同步套用方向前提。
5. 同根同向雙突破的基準線：**做多取較高線、做空取較低線**（不沿用顯示用顏色優先序——沿用會漏掉回測較高線的正當訊號）。
6. **採納提前失效**：窗格內某根收盤跌破基準線（做多 `Close < L`）即殺窗；守住（`Close >= L`）與失效互為補集，等號歸守住。P2/P4 不套提前失效。

以上規則已經過多視角對抗驗證（邊界等號、pandas 可實作性、交易語意、回歸相容、規格一致性；14 個確認缺陷全數收斂進 spec §3.4–§3.5 與本計畫）。

---

## 訊號引擎改動 — [signal_engine.py](signal_engine.py)

所有 `shift / ffill / cumsum / cumcount` 一律以 `StockCode` 分組（spec §5）。

1. **改 `add_breakout_signals` / `add_breakdown_signals`（基準線選擇，spec §3.4）**：
   - 同根雙突破時，`breakout_line_price` 改取**兩條被突破線中較高者**（`np.where(雙突破, max(prev_red, prev_black), 單線值)`）；`breakout_line_type` 對應標示實際選中的線。
   - 鏡像：`breakdown_line_price` 同根雙跌破取**較低者**。
   - 圖表標記用的 `break_red_line_daily` / `break_black_line_daily` 等布林欄不動（顯示優先序僅存在於圖表層，spec §3.4）。

2. **重寫 `add_retest_hold_signals`（P1/P3 核心，spec §3.5a/b）**，做多側配方（做空完全鏡像，`<`/`>`、`Low`/`High` 對調）：
   - 突破事件分組：`_bo_group = breakout_event.groupby(StockCode).cumsum()`（比照 `add_new_line_window_signals` 的 `_new_line_group` 手法）；`bars_since_breakout = groupby([StockCode, _bo_group]).cumcount()`。
   - 基準線凍結：`active_breakout_line_price/type` = 事件棒的 `breakout_line_price/type` 在組內 ffill（事件前的組 0 以 `notna()` 防護，欄值遮蔽為空——不得輸出無意義計數，spec §4.4）。
   - 提前失效（向量化、無前視）：`breach = Close < L`；`invalid = breach.groupby([StockCode, _bo_group]).cummax()` 再 **同鍵** `shift(1, fill_value=False)`——`cummax` 與 `shift` 都必須按（股票, 事件組）分組，否則失效狀態會跨窗格、跨股票洩漏。
   - `breakout_window_valid = L.notna() & bars_since ∈ [1, retest_window] & ~invalid`（單一真值「此棒仍可能成立 P1」，含失效吸收，spec §4.4）。
   - `retest_hold_daily = breakout_window_valid & (prev_Close >= L) & (Low <= L) & (Close >= L)`（`prev_Close` = 組無關的前一根收盤，`groupby(StockCode)["Close"].shift(1)`）。
   - 欄位命名：保留 `retest_hold_daily` / `retest_reject_daily` / `active_breakout_line_*` / `active_breakdown_line_*` 舊名（下游 chart / app / config 消費者不斷鏈），新增 `bars_since_breakout`、`bars_since_breakdown`、`breakout_window_valid`、`breakdown_window_valid`。
   - 註：新突破棒的 `bars_since == 0` 天然被窗格下界排除——「對另一條線的新突破棒不得兼任前一窗格的回測棒」由分組結構自動保證（spec §3.5a 窗格重設）。

3. **改 `add_new_line_window_signals`（P2/P4 方向前提，spec §3.5c）**：
   - `p2_new_line_hold` 增加 `& (prev_Close >= L)`；`p4_new_line_reject` 增加 `& (prev_Close <= L)`。
   - 其餘（窗格 1..N、不含出現當根、單一 `active_new_line`、無提前失效）不變。

4. **`run_signal_pipeline`**：`add_retest_hold_signals` 增加 `retest_window` 參數（`params.get("retest_window", 5)`）；串接順序不變。

## 設定改動 — [config.py](config.py)

- `APP_VERSION = "3.0.0"`、更新 `APP_UPDATED`。
- `DEFAULT_PARAMETERS` 加 `"retest_window": 5`（app.py 約定預設值唯一正本在此，漏加會 KeyError）。
- `EXCEL_PARAMETER_LABELS` 加 `"retest_window": "突破回測窗格（K 棒數）"`；既有 `new_line_window` 標籤由「（交易日）」改為「（K 棒數）」（程式一直是數根，v2 標籤誤寫，spec §3.5f）。
- `DISPLAY_COLUMN_LABELS` 加四個新欄的中文標籤：`bars_since_breakout → 突破後第幾根`、`bars_since_breakdown → 跌破後第幾根`、`breakout_window_valid → 突破回測窗有效`、`breakdown_window_valid → 跌破回測窗有效`。
- `RESULT_COLUMNS`（All_Data）加上述四欄。

## App 改動 — [app.py](app.py)

- 側邊欄加 `retest_window` 數字輸入（`min_value=1`，value 取 `DEFAULT_PARAMETERS["retest_window"]`，比照 `new_line_window` 寫法）；`_build_params` 帶入。
- `new_line_window` 側欄標籤、help 文字、結果摘要字串（「新線窗格 X 日」）單位由「日」改「根／K 棒」。
- 其餘流程（`build_direction_signals`、分頁、摘要）不變。

## Excel 匯出改動 — [export_engine.py](export_engine.py)

- `Parameter_Settings` 加 `retest_window` 列。**注意**：該表為「key tuple ＋ 逐位置 values list」兩處平行硬編碼，必須同步加列，只改一處會因欄長不等直接 ValueError。

## 圖表改動 — [chart_engine.py](chart_engine.py)

- 結構不變（回測標記仍讀 `retest_hold_daily` / `retest_reject_daily`，語意收緊後自動只標真回測棒）。標題或圖例如有「回測」說明文字，補「突破後窗格內」措辭即可。

## 文件改動

- [README.md](README.md)：訊號邏輯說明同步 v3（回測方向前提、`retest_window`、單位「根」）。
- [CHANGELOG.md](CHANGELOG.md) 新增 v3.0.0，**行為變更清單**（必列，訊號數會顯著縮水）：
  1. P1/P3 訊號不再發在突破／跌破當根（決策 2）；v2「跌破當根即壓回」的經典做空樣態一律消失，須窗格內再度觸線且前一根收在線下才成立。
  2. 回測加方向前提（決策 1）：由下往上衝的跨線棒不再算回測。
  3. 突破後回測有 `retest_window` 期限＋提前失效（決策 6）。
  4. 黑線 P2／紅線 P4 於窗格第 1 根數學上不可能（spec §3.5d），異色首根樣態消失。
  5. 週／月線「突破與回測同棒完成」不再出訊號（spec §3.5f）。
  6. 同根雙突破基準線改取較高（多）／較低（空）線（決策 5）。

---

## 測試改動 — [tests/test_stability.py](tests/test_stability.py)

沿用既有「字典自建合成 OHLCV → `run_signal_pipeline`」模式。

**既有測試——會失敗、需改寫的四個（皆做空側或回測語意）**：

- `test_long_retest_failure_is_not_a_long_signal`（~232 行）：跌破棒當根自算 P3 的斷言改為「當根 P3 為 False、窗格內回測棒為 True」。
- `test_breakdown_sets_active_line_and_p3_reject`（~539 行）：同上，跌破當根不再自算壓回，需在棒序後補一根「前收在線下、反彈觸線、收回線下」的回測棒。
- `test_direction_signals_explode_into_multiple_rows`（~709 行）：共用棒序同步修改。
- `test_direction_filter_short_only_suppresses_long_side`（~1015 行）：同上。

（註：現行套件沒有任何測試斷言「突破棒當根自算 P1」，做多側自算行為的移除不會壞既有測試，但仍需新測試鎖定新行為。）

**新增測試（依 spec §3.5d/e 判準案例）**：

1. 突破棒當根不算 P1；B+1 正常回測（前提天然成立）觸線守住 → P1。
2. 窗格過期：第 `retest_window`+1 根觸線守住 → 無 P1。
3. 方向前提：前一根收在線下（先跌破再由下往上跨線收上）→ 無 P1。
4. 提前失效判準案例二（spec §3.5e 的 D1–D8 線價中途移動序列）→ D8 無 P1。
5. 同根雙突破判準案例一（紅 100／黑 98，D2 觸較高線回測）→ P1 成立且 `retest_line_price == 100`；做空鏡像（雙跌破取較低線）。
6. 跳空穿線棒仍算回測（spec §3.5d 收盤對收盤近似；L=100、B 收 100.5、次棒開 97 低 96.5 收 100.0 → P1）——鎖定文件化行為。
7. `Close == L` 刀鋒：窗內收盤恰等於 L → 守住、不失效、亦非新突破棒。
8. 異色首根：黑線出現後窗格第 1 根 P2 必不成立、第 2 根（前收已站上 L）成立；紅線 P4 鏡像。
9. P2+P4 同根雙發退化案例（spec §3.8：前收 == L 且收 == L）→ 恰輸出 Long P2 ＋ Short P4 兩列。
10. 週線重取樣回歸：突破與回測同棒完成 → v3 無訊號（時框行為變更鎖定）。
11. `breakout_window_valid` 欄位級測試：失效後窗格根數仍在範圍內 → 欄值 False；首事件前 `bars_since_breakout` 為空。

---

## 驗證方式

1. **語法**：`python3 -m py_compile app.py config.py data_loader.py signal_engine.py chart_engine.py export_engine.py`。
2. **單元／回歸測試**：`pytest tests/test_stability.py`（改寫四個＋新增十一個全綠，其餘既有全綠）。
3. **本機端到端**：`streamlit run app.py`，用 `2330.TW`、`2454.TW` 跑日 K 與週 K：
   - 確認 P1 訊號不再落在突破當根；調 `retest_window`（1 vs 10）觀察 P1/P3 數量變化。
   - Excel `Parameter_Settings` 含 `retest_window` 且單位標示「K 棒數」；All_Data 含四個新欄。
4. **不變式抽查**（spec §5）：方向前提、事件當根不計回測、守住／失效互補、窗格以根計。
