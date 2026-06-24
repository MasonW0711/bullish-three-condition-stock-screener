# 變更紀錄（Changelog）

本檔記錄各版本的重要變更。日期為當地時間。

## v2.3.0 — 2026-06-24

第二輪深度審查後的正確性、資料可靠性、安全與封裝強化。

### 正確性修正（影響訊號結果）

- **修正「同根攻擊成功」的真突破／真跌破被誤殺（漏訊號）。** v2.2.0 為了擋假突破而要求「當根線價與前一根相等」，卻把「收盤穿越舊線、但當根本身又是攻擊成功（線價因此移動）」的真訊號一併丟掉：強勢跳空突破／跌破因此不產生 P1／P3，連帶 `active_breakout/breakdown_line_price` 不設立、後續回測也啞掉。現改為突破／跌破兩端都比對「前一根（在場）的線價」，既保留 v2.2.0 的假突破防護（收盤落在新舊線之間仍過不了舊線），又能正確偵測真訊號。
  - 檔案：`signal_engine.py`（新增 `_crosses_line`，重寫 `add_breakout_signals`、`add_breakdown_signals`）。
  - 回歸測試：`test_upward_gap_breakout_on_attack_success_fires_p1`、`test_downward_gap_breakdown_on_attack_success_fires_p3`；並更新 `test_long_retest_failure_is_not_a_long_signal`。
- **修正法人連續日在「少股票篩選」時靜默橋接缺失交易日。** 連續日的交易日軸原本由「已過濾成已篩選股票」的法人表建立，單股／少股票模式下缺一天無法形成 NaN 缺口而被橋接，產生資料相依的假連續。現由 `app.py` 在過濾前擷取「全市場交易日軸」並傳入 `attach_investor_flow_flags` / `_add_consecutive_streak_flags`。
  - 回歸測試：`test_investor_streak_single_stock_uses_market_trading_day_axis`。
- **修正法人抓取視窗在大 N 時不足而靜默過度過濾。** 視窗改以「交易日」並綁定 `investor_consecutive_days + lookback_bars`，使連續日條件能在整個回看窗內評估，而非只在最新一根成立。

### 資料可靠性

- 還原（adjusted）日期改以 Asia/Taipei 時區處理，避免未來 yfinance 回傳 tz-aware 時間戳時整體日期退一天（影響月 K 重取樣與法人日期對齊）。
- HTTP 429／5xx 暫時性狀態納入指數退避重試（原本只重試逾時／連線中斷）。
- `_select_isin_table` 改選「含最多 `\d{4} 名稱` 列」的 7 欄表，降低誤選版面雜訊表的風險。
- `normalize_symbol` 支援 5～6 位數代號（如 ETF `00878`），不再無聲落空。
- `_download_candidate` 對全域 logger level 與 stdout/stderr 重導以鎖序列化，避免多 session 併發時把 logger 永久留在 CRITICAL。

### 安全

- Excel 匯出對「每一張工作表」消毒公式注入（先前 `Failed_Downloads`／`Parameter_Settings` 會繞過，使用者貼上的 `=...`／`@...` 代號原樣寫入）。
  - 回歸測試：`test_failed_downloads_sheet_escapes_formula_injection`。
- `verify=False` 的 TLS 降級僅限已知 TWSE／TPEX 主機，其他主機改為直接拋出 `SSLError`（不再無條件信任）。

### 效能

- 週／月 K 重取樣由 ~1800 次 per-stock 迴圈改為單次 groupby + `pd.Grouper` 聚合（驗證輸出位元相同，含 `TradeDate=max` 最後交易日語意）。

### UI

- 法人資料「抓到但篩選股票無對應」時改顯示正確訊息（不再誤報「無法取得」）。
- CSV 下載按鈕標籤依方向過濾動態顯示（做多／做空／做多＋做空）。

### 設計與封裝

- 移除無用的 `RESULT_COLUMNS`、`app.py` 死綁定與 `DISPLAY_COLUMN_LABELS` 死標籤；`signal_engine` 改 import `config.INVESTOR_FLAG_COLUMNS`（消除重複定義漂移風險）。
- `requirements.txt` 明確宣告直接相依的 `certifi`；PyInstaller spec 關閉 `upx`（避免破壞原生套件／誤報）。
- spec §6 參數表更正為 `analysis_timeframe` / `lookback_bars`（與實作一致）；修正 `config.py` 一處簡體「选项」。
- CI 新增 `ruff`（pyflakes + 語法）與 `coverage`；新增 `pyproject.toml`。

### 測試

- 桌面啟動器新增閒置監看 monitor（連線後閒置關閉、未連線不關閉、狀態讀取例外時乾淨退出）、idle-timeout 環境變數驗證、`_on_server_start` 缺失時的降級啟動，以及修正過度 mock 的 frozen 路徑測試。

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
