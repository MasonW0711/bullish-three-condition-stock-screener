# 變更紀錄（Changelog）

本檔記錄各版本的重要變更。日期為當地時間。

## v2.2.0 — 2026-06-13

架構審查後的正確性、資料可靠性與封裝強化。

### 正確性修正（影響訊號結果）

- **修正「新建線當根」的假突破／假跌破（P0）。** 突破／跌破偵測原本會把「前一根收盤 vs 舊線」與「當根收盤 vs 新建的線」混在一起比較：當某根 K 棒新建一條紅／黑線、且收盤剛好落在舊線與新線之間時，會觸發一個不存在的突破或跌破，並進一步污染 P1／P3 最終訊號。現在要求該線價在當根維持不變（同價位的重建仍視為同一條線）才成立。
  - 檔案：`signal_engine.py`（`add_breakout_signals`、`add_breakdown_signals`）。
  - 回歸測試：`test_new_lower_red_line_does_not_fake_a_breakout`、`test_new_higher_black_line_does_not_fake_a_breakdown`。
- **法人連續買賣超改以「交易日」為單位，不再橋接缺失日（P1）。** 連續日原本是對「資料列」滾動，會把某檔股票缺資料的日期（當日未成交或單日抓取失敗）無聲跳過，誤判為連續。現在每檔股票會重新索引到全市場觀察到的交易日軸，缺一天即中斷連續（符合 spec §3.7「最近 N 個交易日」）。
  - 檔案：`signal_engine.py`（新增 `_add_consecutive_streak_flags`）。
  - 回歸測試：`test_investor_streak_does_not_bridge_a_missing_trading_day`、`test_investor_streak_holds_across_full_consecutive_days`。
- **股價改用還原（adjusted）價格（P1）。** `yf.download` 由 `auto_adjust=False` 改為 `auto_adjust=True`，避免分割／大額除息缺口被誤判為「大黑攻」並產生幽靈黑線。
  - 檔案：`data_loader.py`（`_download_candidate`）。

### 資料可靠性與診斷

- 法人資料抓取失敗時，除了顯示失敗次數，現在會列出**受影響的日期**，並寫入 Excel「下載失敗清單」的診斷訊息。
  - 檔案：`data_loader.py`（`download_investor_flow_data` 新增 `fetch_failure_dates`）、`app.py`。
- 修正：避免對 Streamlit 快取物件就地追加診斷訊息（改為複製後再追加）。

### UI

- 全市場模式下不再把數百個「無資料」代號（多為下市／流動性不足／新上市）列成警告，改為摘要數量，完整清單保留在 Excel。

### 文件

- README 開始日期預設由錯誤的「今天-2年」更正為實際的「今天-30天」。
- spec §4.3 的「下載失敗清單」欄位名稱更正為實際輸出的中文欄名（「失敗股票代號」「診斷訊息」）。

### 測試與 CI

- 新增端對端「日線→週線重取樣→訊號管線」測試，確認訊號在週 K 上計算。
- 新增 `export_engine` 的工作表內容測試（含全空輸入）。
- CI 單元測試改為在 Ubuntu／Windows／macOS 三平台執行。

### 封裝

- 依賴版本策略寫入 `requirements.txt`；桌面 release 建置會輸出每平台的 `requirements-lock-<os>.txt` 鎖定檔並附加到 GitHub Release，作為可重現建置的依據。
- PyInstaller spec 的 `hiddenimports` 明確列入 `display_utils`。

### 已知限制（本版未變更，刻意保留）

- 全市場 × 長區間的下載仍為單一同步流程、無中途續跑；預設 30 天區間用以降低雲端逾時風險。後續可考慮分塊／非同步與檢查點。
- TWSE／TPEX 請求在 SSL 憑證驗證失敗時會降級為不驗證重試（雲端環境必要的讓步），此行為會留下警告紀錄。
