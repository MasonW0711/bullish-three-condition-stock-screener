# 變更紀錄（Changelog）

本檔記錄各版本的重要變更。日期為當地時間。

## v3.2.0 — 2026-07-11

效能、可靠性與可測性強化。訊號邏輯（引擎輸出）逐格 byte-identical，未改變任何篩選結果；差分測試（合成資料＋真實台股）驗證。

### 效能

- **訊號 pipeline 記憶體：9 次 `df.copy()` → 單次 owned copy。** `run_signal_pipeline` 過去每階段都複製整個成長中的大 frame；現在 `add_prev_close` 的排序產生唯一 owned frame，後續階段就地寫入。`_windowed_retest` 的 `(StockCode, event-group)` 分組改為建立一次重用。（`signal_engine.py`）
  - 回歸測試：`test_run_signal_pipeline_does_not_mutate_input`＋差分 byte-identical 驗證。
- **法人流程兩個 per-stock 迴圈向量化。** `_add_consecutive_streak_flags` 改為 pivot 成（交易日 × 股票）矩陣、沿日期軸一次 rolling；`attach_investor_flow_flags` 改用單一 `pd.merge_asof(by="BaseCode")`。1800 檔約 190ms。（`signal_engine.py`）
- **股價下載本機快照快取（`price_cache.py`）。** 因下載採 `auto_adjust=True`（除息／分割會回溯調整整段歷史），不可做增量續抓（會混用調整基準、製造假突破）；改為以「整份結果」為單位的磁碟快照，鍵為 (代號, 起訖)，含新鮮度守則（歷史區間 7 天、含當日的區間 30 分），fail-open。桌面版閒置 15 秒即關閉、每次開啟都是冷啟動，此快取讓「近期已下載過的相同條件」由數分鐘變數秒（實測 523ms→29ms）。
  - 回歸測試：`PriceCacheTests`（round-trip、新鮮度、fail-open、磁碟命中、批次錯誤不入快取）。
- **Excel 匯出改為延遲建構。** 含 All_Data 全量工作表的 Excel 只在使用者按「準備 Excel」時才建，避免從未下載時白白耗費 CPU／記憶體。CSV（僅訊號）維持即時。

### 可靠性 / 可測性

- **暫時性下載失敗不再污染快取。** 股價批次的限流／逾時（`download_errors`）會清除 `st.cache_data`，避免殘缺結果被整個 TTL 重複回傳；per-stock 無資料失敗（多為下市）則保留。法人部分抓取失敗同理。
- **`_run_screening` 依賴注入化。** 資料存取與快取清除改為可注入，整個編排流程可用假資料單元測試。（`ScreeningServiceTests`）

### 封裝 / CI

- `build-desktop-executables.yml` 加 `timeout-minutes: 30`（直接上限化過去 v2.2/v2.3 卡到 24 小時的 build）與 `cache: pip`。
- `requirements.txt` 新增 `pyarrow`（price_cache 的 parquet 引擎）。

## v3.1.0 — 2026-07-07

K 線圖繪製修正與股票名稱顯示。引擎（訊號邏輯）零變更；本版僅改圖表層與 UI。

### 圖表正確性（`chart_engine.py`）

- **紅／黑線改以水平階梯（`line_shape="hv"`）繪製。** 紅黑線是逐根 forward-fill 的階梯函數，舊版用 Plotly 預設的線性插值，導致每次線價變動都畫成斜線，線會漂在從未存在過的價位上（實測真實資料約 30–40% 線段是斜的）——這是「紅黑線圖表不正確」的主因。線的定義與數值完全未動，只修畫法。
  - 回歸測試：`test_red_line_uses_step_shape_not_diagonal`。
- **強制淺色底圖。** 近黑色的黑線（`#111827`）在深色 Streamlit 佈景下對比僅 1.07:1、幾乎不可見；改為固定白底（`plotly_white`）使黑線與所有標記在任何佈景下都清楚，且不必犧牲「黑線」語意去改色。
- **K 棒與成交量改台股慣例（紅漲綠跌）。** 舊版沿用 Plotly／美國慣例（綠漲紅跌），與「紅線＝多方」語意衝突；改為漲紅（`#dc2626`）、跌綠（`#16a34a`）。
  - 回歸測試：`test_candles_use_taiwan_colors`。
- **同根雙突破標記錯開。** 同一根同時突破紅、黑兩線時，兩個三角形不再重疊（黑色標記過去被壓在紅色下方而看不到）。

### 股票名稱顯示

- **圖表標題與選股下拉選單顯示「代號＋名稱」**（例：`2330.TW 台積電`）。名稱早已從 TWSE/TPEX 官方清單一路帶進結果表格、CSV 與 Excel；本版補上圖表標題（`chart_engine.create_stock_chart` 新增選用參數 `stock_name`，亦會自動讀取資料框的 `StockName` 欄）與 K 線圖選股選單的 `format_func`。
  - 回歸測試：`test_title_includes_stock_name_from_column`、`test_title_includes_stock_name_from_argument`、`test_chart_works_without_stock_name`。
- Excel `All_Data` 工作表的 `股票名稱` 欄移到 `股票代號` 旁（原本因 join 落在最後一欄），與訊號／摘要工作表一致。

### 已知限制

- ETF 與 5–6 碼代號（如 0050、00878）目前仍無名稱（清單解析僅收 4 碼普通股），會退回顯示代號。擴充需加 CFI 白名單並將名稱對照表與自動篩選宇宙分離，留待後續。

## v3.0.0 — 2026-07-05

回測方向性重設計。v2 的回測判定只有「觸線＋收盤守住／壓回」，沒有方向前提，突破棒本身經常被算成回測。v3 把「做多＝由下往上突破、由上往下回測」「做空＝完全相反」寫進引擎。

### 正確性 / 領域規則（影響訊號結果）

- **回測加入方向前提。** 做多回測棒要求**前一根收盤 ≥ 基準線**（由上往下），做空要求**前一根收盤 ≤ 基準線**（由下往上）。方向前提是收盤對收盤的近似：只比對前一根收盤，不看回測棒自身的開盤與盤中路徑（跳空穿線後收回仍算）。
  - 檔案：`signal_engine.py`（`add_retest_hold_signals` 重寫、`add_new_line_window_signals` 加前提）。
  - 回歸測試：`test_p1_requires_previous_bar_closed_on_line_side`、`test_gap_through_bar_still_counts_as_retest`。
- **事件當根不再自算回測，且回測有窗格。** 突破／跌破棒本身（`bars_since == 0`）不計為回測；回測須在事件後 `retest_window` 根（新參數，預設 5，不含事件當根）內發生，過期失效。
  - 回歸測試：`test_p1_retest_expires_after_retest_window`；並更新 `test_breakout_and_retest_hold_are_final_signal`。
- **提前失效。** 突破後窗格內任一根收盤穿回基準線（做多 `Close < L`、做空 `Close > L`）即代表突破失敗，該回測窗自該根之後作廢，直到新的突破棒重啟窗格。守住（`Close >= L`）與失效互為補集，`Close == L` 恆歸守住側。
  - 回歸測試：`test_p1_window_invalidated_by_close_through_line`（spec §3.5e 線價中途移動案例）。
- **同根同向雙突破改依價位選基準線。** 一根同時向上突破紅、黑兩線時，回測基準取**較高**線（拉回先觸到的支撐）；同根雙跌破取**較低**線。不再沿用僅供顯示的顏色優先序（沿用會漏掉回測較高／較低那條線的正當訊號）。
  - 檔案：`signal_engine.py`（`add_breakout_signals` / `add_breakdown_signals`）。
  - 回歸測試：`test_dual_break_uses_higher_line_for_long_retest`。

### 行為變更（訊號數會顯著縮水，屬預期）

- P1／P3 不再發在突破／跌破當根；v2「跌破當根即壓回」的經典做空樣態一律消失，須事件後窗格內再度觸線且前一根收在正確一側才成立。多空兩側訊號數都大幅縮水（同資料實測約砍半至三分之二），**相對比例**則依行情而定：偏空行情下做多側常縮得更多，升級後做空「佔比」可能反而上升，屬預期、並非做空側放寬。（2026-07-06 補記：原句「做空側訊號數尤其明顯縮水」經同資料實測修正。）
  - 更新的既有測試：`test_long_retest_failure_is_not_a_long_signal`、`test_breakdown_sets_active_line_and_p3_reject`、`test_direction_signals_explode_into_multiple_rows`、`test_direction_filter_short_only_suppresses_long_side`。
- 黑線的 P2 於新線窗格第 1 根、紅線的 P4 於第 1 根，數學上不可能成立（出現當根收盤必在線的另一側）；異色首根樣態消失，最早於窗格第 2 根成立。
  - 回歸測試：`test_heterochromatic_first_window_bar_cannot_be_p2`。
- 週／月線上「突破與回測於同一根內完成」不再產生訊號（突破當根不計回測），此為高時框的常見型態。

### 參數 / UI / 匯出

- 新增參數 `retest_window`（預設 5）：側邊欄、`config.DEFAULT_PARAMETERS`、Excel `Parameter_Settings` 三處同步。
- 窗格單位統一為「K 棒（根）」：修正 `new_line_window` 舊標籤「（交易日）」為「（K 棒數）」，UI help、結果摘要字串與 spec §3.4c/§3.5c/§6 一併更正（引擎一直是數根，v2 標籤誤寫）。
- `All_Data` 新增欄位 `bars_since_breakout`、`bars_since_breakdown`、`breakout_window_valid`、`breakdown_window_valid`，並補上 `DISPLAY_COLUMN_LABELS` 中文標籤。
- spec §6 補列一直生效卻漏列的 `min_volume`。
- 回歸測試：`test_p2_and_p4_can_fire_on_same_bar_both_directions`（§3.8 同根多空並發）。

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
