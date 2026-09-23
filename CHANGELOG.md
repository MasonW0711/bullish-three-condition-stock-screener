# 變更紀錄（Changelog）

本檔記錄各版本的重要變更。日期為當地時間。

## v4.0.0 — 2026-09-24

實跑全市場（2026-08-26～09-23、1,977 檔、預設參數）後，Mason 名單裡有不少一眼就覺得不該入選的股票。規則的最終依據是 Mason 本人的判斷；追出四個共同的誤選來源，本版逐一修正，不動其餘既有行為。

### 行為變更：新線只朝一個方向用，紅黑兩色各自獨立觀察窗（spec §3.5c）

- **以前**：新紅線與新黑線共用「目前的新線」這一個追蹤對象——任一色都能同時產生 P2（做多）與 P4（做空），且任何顏色的新線出現都會結束前一條新線的觀察窗，即使新出現的是相反顏色。
- **現在**：新紅線只產生 P2（做多守住），新黑線只產生 P4（做空壓回）；紅、黑兩色各自維護獨立的線價與觀察窗（出現當根為第 0 根，觀察窗為第 1～`new_line_window` 根），只有同色出現更新的新線才會重設，另一色的新線完全不影響。
- **為什麼**：Mason 選股時新線路徑的核心依據就是「線的顏色＝方向」，黑線也能做多、紅線也能做空違反這個直覺；異色新線把觀察窗吃掉，也會讓真正的守住／壓回憑空消失。
- 觀察窗內的提前失效（v3.3.0 起）保留，仍分兩色各自累計、互不影響。v3 允許的「異色路徑」（黑線產生 P2、紅線產生 P4）自本版起刪除；「先收回黑線之上、再守住」這類跨色型態，改由突破回測路徑（P1）辨識，不再靠新線路徑硬接。

### 行為變更：開盤穿線不算測線（spec §3.5f）

- **以前**：只要當根收盤守住／壓回，不論開盤在哪裡都算一次有效回測——即使開盤已經明顯跳到線的另一側、盤中才穿回來（v3 稱為 gap-through，明文視為有效回測）。
- **現在**：回測棒的開盤若超出線價「開盤穿線容許度（%）」（新參數 `open_cross_tolerance_pct`，預設 1.0，可調，最小 0）就視為穿線而非測試，不算守住／壓回；P1～P4 四條路徑皆適用。穿線的那根本身不會讓觀察窗失效——只有收盤破線才會；之後在同一觀察窗內再回測一次，仍可能成立。
- **為什麼**：在一次 16 檔的人工標記中，判錯的 3 檔全是「開盤跳到線的另一側 1.8%～5.3%」這種型態（例如 6173 信昌電），在 Mason 眼中那是穿線、不是測線；判對的樣本裡開盤跳過去的幅度都在 1.0% 以內。1% 是能讓這 16 檔標記全部吻合的最緊門檻。
- v3 規格 §3.5d「開盤跳過線、收回線上仍算回測（gap-through）」的條文自本版起作廢，改由本規則決定。

### 行為變更：訊號後破線即移出名單（spec §3.5g）

- **以前**：訊號一旦出現就留在名單上，即使之後股價早已收盤跌破（做多）或漲破（做空）訊號用的線，名單上仍然看得到——同資料實測，做多名單裡有 41%、做空名單裡有 62% 屬於這種情況。
- **現在**：每一筆訊號（P1～P4），只要從訊號那根之後、到該股票最新一根為止，任何一根收盤破線（做多看收盤是否低於訊號用的線、做空看是否高於），這筆訊號就失效，不進做多／做空訊號表，也就不進最新摘要。這個判斷不受觀察窗或回看根數限制，在成交量、回看根數門檻之外另外生效，四條路徑一體適用。

### 行為變更：路徑欄與「訊號類型」更名

- **以前**：Mason 選股主要看新線路徑（P2／P4），突破回測路徑（P1／P3）只當次要參考，但兩者混在同一張表裡分不出來，而且突破回測路徑占了名單的一大半。
- **現在**：做多、做空訊號表新增「路徑」欄：P2、P4 顯示「新線路徑」，P1、P3 顯示「突破回測路徑」；欄位順序為 `direction`（方向）、`path`（路徑）、`signal_type`（訊號類型）。原「訊號路徑」欄保留，**顯示標籤改為「訊號類型」**，避免和新增的「路徑」欄混淆。

### 最新摘要與路徑篩選器

- 最新摘要改為每個方向內、每個（股票、路徑）組合各取最新一筆，新增「Path」欄（位於「Direction」與「SignalType」之間）；**移除「同一天 P1／P3 優先於 P2／P4」的隱性排序**——同一路徑固定對應同一種訊號類型，這條排序規則已無意義，且可能讓「篩到新線路徑時，剛好某檔最新一筆訊號是 P1」而整檔消失。
- 結果頁新增路徑篩選器（全部／新線路徑／突破回測路徑，預設全部），同時作用於訊號表與最新摘要，因此也連動 K 線圖可選股票清單。**CSV／Excel 匯出不套用此篩選**，一律輸出全部路徑，並都帶路徑欄，供使用者離線再篩選。Excel 的 `Parameter_Settings` 工作表新增一列「開盤穿線容許度（%）」；工作表名稱與順序不變。

### All_Data 欄位變更

- **移除**：`bars_since_new_line`、`active_new_line_type`、`active_new_line_price`、`new_line_window_valid`、`new_line_window_valid_long`、`new_line_window_valid_short`——紅黑線自本版起各自獨立追蹤，不分顏色的單一組欄位不再有意義；新紅線的線價就是 `red_line` 欄本身，新黑線就是 `black_line` 欄本身，不再需要另一組 `active_new_line_price`。
- **新增**：`bars_since_new_red_line`（新紅線後第幾根）、`new_red_line_window_valid`（新紅線觀察窗有效（做多），已含提前失效）、`bars_since_new_black_line`（新黑線後第幾根）、`new_black_line_window_valid`（新黑線觀察窗有效（做空），已含提前失效）、`p1_broken_after` ～ `p4_broken_after`（P1～P4訊號後破線；只在該路徑的訊號棒上可能為 `True`）。
- **保留**：`new_line_appeared` / `new_line_type` / `new_line_price`（新線出現當根本身的標記，不分方向，語意不變）。

### 圖表

- 新增新線路徑標記：「新紅線守住（新線路徑）」（星形、紅色，K 棒下方）與「新黑線壓回（新線路徑）」（星形、黑色，K 棒上方），滑鼠提示顯示該訊號實際採用的線價。
- 同一根同時突破（或跌破）兩條線時，**兩個標記都會畫出**（上下錯開）；spec.md 一併更正舊文字裡容易誤讀成「只顯示一色」的「顯示優先序」說法——實際行為自 v3.1.0（同根雙突破標記錯開）起就是兩個都畫，本次是文件更正而非行為變更。

### 新參數

- `open_cross_tolerance_pct`（開盤穿線容許度（%）），預設 1.0，最小 0。已加入預設參數、側邊欄數字輸入、參數組裝，以及 Excel 參數設定工作表。

### 預期訊號量變化（模擬結果，僅供大致參考）

以 2026-08-26～2026-09-23 全市場日 K（1,977 檔、預設參數）模擬：

- **v3（現行規則）**：做多 184 檔、做空 214 檔；其中訊號出現後早已收盤破線、卻仍掛在名單上的，做多占 41%、做空占 62%。
- **v4**：新線路徑約做多 79 檔、做空 59 檔；突破回測路徑約做多 70 檔、做空 68 檔（兩路徑分開計算；同一檔可能兩條路徑都有訊號，以路徑欄區分）。
- 這些數字僅供參考。還原權值重新下載後可能小幅變動，不是精確的測試期望值。

### 已知範圍（本次未變更）

- 新線的產生規則、線價（昨收）、「守住要有測」的核心語意、成交量門檻、回看根數、法人條件皆未變動。
- P1／P3 的突破／跌破偵測、同根雙突破選線規則、窗格、提前失效、收回線上重新開窗（reclaim）等既有行為不變，僅在回測判定加上開盤條件（§3.5f）。
- 週 K、月 K 沿用同一套規則，未另外調整。
- 不改變線價定義；不新增新線的幅度／K 棒顏色／成交量門檻；不移除 P1／P3 或改變其突破偵測；不動法人買賣超邏輯本身；圖表既有的紅線、黑線歷史路徑與凍結基準線畫法維持現狀。

## v3.3.0 — 2026-08-21

修正「新線窗格內實體 K 棒已跌破基準線，之後仍出做多（P2）訊號」的回報。

### 行為變更：P2／P4 補上各自的提前失效

- 原規格 §3.5c 明文「P2 / P4 **不設**提前失效」，實作亦忠實照做（`add_new_line_window_signals` 的 `window_valid` 不含 breach 項）。後果是新線窗格內某根 K 棒整根收在紅線／黑線之下，只要窗內稍後有一根回測站回線上，**照樣輸出做多**。這是規格設計與使用者預期的落差，不是實作錯誤。
- 現改為：窗格內任一根 `Close < L` 使**做多側（P2）**自該根之後作廢；任一根 `Close > L` 使**做空側（P4）**自該根之後作廢。
- **兩側必須各自獨立累計。** P2 與 P4 共用同一條線與同一個窗格，若沿用單一失效旗標，任何收盤不等於 `L` 的 K 棒都會同時殺掉兩個方向，P2／P4 將幾乎無法成立。
- **失效掃描自窗格第 1 根起算，不含新線出現當根。** 出現當根的收盤依構造必落在 `L` 的固定一側（紅線在上、黑線在下），若計入，黑線的 P2 與紅線的 P4 將永遠無法成立，與 §3.5d 明文的異色路徑相牴觸（已由 `test_new_line_breach_scan_excludes_the_appearance_bar` 鎖定）。
- 新線窗格**不因失效而重啟**：新線沒有「突破棒」可重設，只有下一條新線出現才會開新窗格。

### All_Data 新增欄位

- `new_line_window_valid_long`／`new_line_window_valid_short`（新線窗格有效（做多）／（做空））：分別表示「此棒仍可能成立 P2／P4」。
- `new_line_window_valid` 語意**維持不變**（僅表示窗格根數與線價在場，不含失效）——單一欄位無法同時表達兩個方向。
- **Excel 表頭更名**：`new_line_window_valid` 的顯示名稱由「新線窗格有效」改為「**新線窗格（根數內）**」。該欄刻意不含提前失效，原名稱與行為不符（spec §5：UI 標籤必須描述相同的行為），且本版 All_Data 表頭本來就因新增兩欄而改變，一次調整成本最低。
- **失效是「事前」語意**：破線當根本身仍回報 `True`，只有其後的 K 棒轉為 `False`。此欄是 P2／P4 在該棒消費的資格項，該棒自己的收盤才用來決定「守住或破線」；把結果折回自己的資格會使匯出欄位與判定公式脫鉤。與 `breakout_window_valid` 一致。

### 圖表：畫出訊號實際採用的凍結基準線

- 過去圖上只有 `red_line`／`black_line`，那是**已 forward-fill 的當前線價、會隨新的攻擊成功而移動**；但 P1／P3 判定用的是**突破／跌破當根凍結的 `L`**（`active_breakout_line_price`／`active_breakdown_line_price`）。兩者在「線價於窗格內被移走」時（§3.5e 案例二）會分岔。
- 後果：一個完全合法的 P1 訊號棒，可能**整根 K 棒都在畫出來的紅線之下**——看起來就是「跌破卻仍做多」，實際上是兩條不同的線。**回報的異常有一部分屬於此視覺落差，而非判定錯誤。**
- 現在圖表在窗格存活期間額外繪製凍結 `L`（做多紫色 `#7c3aed`、做空青色 `#0891b2`，實線、`hv` 階梯），線段自事件當根起繪。缺少對應欄位時（原始 OHLCV 預覽）靜默略過。

### 已知範圍（本次未變更）

- **P1 的 reclaim 重啟仍在。** 依 §3.5d，在場線價不變時，收破線後任何嚴格收回線上的棒**本身即構成新突破棒**並重啟窗格，因此「實體跌破後隔幾根出 P1 做多」仍是規格允許的輸出。本次僅依需求修正 P2／P4。

## v3.2.3 — 2026-07-25

修正桌面打包「build 卡 24 小時」的真正原因，並更正 v3.2.2 對此的錯誤說明。

### 移除 `macos-13`（Intel）建置目標

- **它從未成功過一次。** v1.0.3 以來全部 6 次 desktop build，`macos-13` job **6/6 皆為 `cancelled`**，每次都是在佇列中等到 GitHub 的 **24 小時上限**（v3.2.0 該 job 耗時恰好 `24h 0m 0s`）才被自動取消——**runner 從未被指派、job 從未開始執行**。
- 因此 **`macos-intel.zip` 從來沒有出現在任何一個 Release 中**；使用者一直只拿得到 Windows 與 Apple Silicon 版。這個 job 唯一的作用，是讓每次 tag 觸發的 build 整體狀態卡在 `queued` 一整天。
- 移除後，每次 build 約 **3 分鐘**乾淨完成（Windows 3m02s、Apple Silicon 1m36s，實測值）。

### 更正 v3.2.2 對 `timeout-minutes` 的錯誤描述

- v3.2.2 宣稱 `timeout-minutes: 30`「直接上限化那個已知的 24 小時卡死」——**這是錯的**。`timeout-minutes` 只在 **runner 接手 job 之後**才開始計時，**完全不涵蓋排隊等待**，因此對上述佇列卡死毫無作用。該設定仍保留，但其作用僅限於「防止已開始執行的 build 步驟卡住」。
- 已在 workflow 註解與 v3.2.2 條目就地標註更正。

### 文件

- README 原本指示使用者下載 `macos-intel.zip`——**該檔案從未存在**。已改為明確標示不提供 Intel 封裝版，並指引改用本機執行或雲端版。

## v3.2.2 — 2026-07-24

深度審查（七維度、對抗式驗證）後的必修項目。

### 🔴 修正：併發寫入可產生「混用兩次下載」的快取檔（會造成假訊號）

- `price_cache.save_snapshot` 的暫存檔名原為 `<key>.parquet.tmp`，**只由 key 決定**，兩個行程存同一組 (代號, 起訖) 會開同一個檔案交錯寫入。因 parquet 是**欄式**格式，倖存者通常是一個**完全合法**的檔案，但欄位來自兩次不同下載——`read_parquet` 成功、不拋錯、fail-open 永不觸發，混用的價格會產生假突破訊號。桌面版無單一實例鎖，多開即可觸發。
- 改為每個寫入者使用唯一暫存檔名（`pid.uuid4`），並在 `finally` 清除，parquet 與 json sidecar 皆同。
  - 回歸測試：`test_concurrent_saves_never_mix_columns_from_two_downloads`（40,000 列 × 2 執行緒；已驗證還原舊碼會**穩定失敗** 3/3）。

### 🟠 修正：法人資料「全部抓取失敗」時靜默且不清快取

- `if investor_flow_df.empty:` / `elif fetch_failures > 0:` 的鏈接，使「每一次 TWSE＋TPEX 抓取都失敗」這個**最嚴重**的情況跳過診斷訊息與快取清除——空結果被整整 1 小時的 TTL 重複回傳，使用者完全不知情。改為獨立判斷。
  - 回歸測試：`test_total_investor_fetch_failure_still_evicts_and_reports`、`test_partial_investor_fetch_failure_still_evicts`、`test_clean_investor_fetch_does_not_evict`。

### 🟠 更正：v3.2.0 部署事故的根因記載錯誤

v3.2.1 記載「現代 Streamlit 已不依賴 pyarrow」——**這是錯的**，已於本版更正：

- 實測 `streamlit 1.36.0 → pyarrow>=7.0`、`1.60.0 → pyarrow<25,>=7.0`，**整個 pin 範圍都硬性依賴 pyarrow**，雲端一直都裝得到，快取在雲端**是啟用的**。
- 真正肇因是我加的**上界** `pyarrow>=15,<22`：它把版本釘死在 21.0.0（**0 個 cp314 wheel**），才導致原始碼建置與 cmake 失敗。移除該行之所以有效，是讓 pip 改用 streamlit 自己的 `>=7.0` 解到 25.0.0（**14 個 cp314 wheel**）。
- **教訓：不要為原生相依加上界，把它壓在最新有 wheel 的版本之下。** 已更正 `price_cache.py` 註解、`requirements-build.txt` 說明與測試命名前提。

### 🟠 修正：快取無上限成長（雲端亦受影響，因上述前提錯誤）

- 快取鍵含日期區間，滾動預設區間使其**每日產生新鍵**，而原本沒有任何刪除路徑。新增 `_prune`：每次寫入後清掉超過最長 TTL、已永遠不可能被服務的快照與殘留暫存檔（best-effort，不影響呼叫端）。
  - 回歸測試：`test_prune_removes_snapshots_that_can_never_be_served_again`。

### 🟠 補上會漏掉真實回歸的測試

- **`merge_asof(direction="backward")` 無測試釘住**：改成 `"nearest"`／`"forward"`（**未來偷看**）原本 93 個測試全過。新增 `test_investor_flags_never_use_flow_published_after_the_bar`。
- **每股 future mask 無多股測試**：改成全域 max 原本全過。新增 `test_investor_flags_stop_at_each_stock_own_last_flow_date`。
- **`_snapshot_key` 完全無防護**：把 codes 從鍵中拿掉原本全過（會跨清單誤供資料）。新增 `test_snapshots_are_isolated_per_code_list_and_window`。
- 補實 `test_cache_is_inactive_without_a_parquet_engine` 原本空洞的斷言（現同時驗證有引擎時確實會寫入與回讀）。

### 🟠 其餘 Major：Excel 記憶體與週／月線法人視窗

- **Excel `All_Data` 只輸出回看窗格內的 K 棒。** 原本輸出全部下載歷史，全市場約 23 萬列 × 50 欄，openpyxl 逐格建物件，實測峰值 4.2 GB／75 秒。訊號判定本來就只用窗格內資料，窗格外不構成任何已報訊號的證據。實測改善：**75 秒 → 5.8 秒、xlsx 52 MB → 5.3 MB**；略過的列數會寫入診斷訊息。
- **法人抓取視窗依週期換算。** `needed_trading_days` 原本把 `lookback_bars` 直接當交易日，但它是**所選週期的 K 棒數**（spec §6）。週線／月線因此只抓約 34 個日曆日的法人資料，卻要涵蓋回看數月的窗格，較舊的合格訊號被靜默丟棄。改為 `D=1 / W=5 / M=21` 換算。（此為 v3.2.0 之前即存在的問題）

### 🟡 快取強化（Minor）

- **歷史區間 TTL 由 7 天縮短為 1 天**：yfinance 連**成交量**都會回溯調整，7 天可能沿用除權前的量能基準而翻轉 `min_volume` 絕對門檻。1 天仍完整保有桌面冷啟動的效益。
- **時間一律以 UTC 比較**：原用 naive 本地時間，DST 回撥或機器時區變動會讓年齡少算最多一小時、變相延長 TTL。
- **`end_was_current` 缺失時保守採用短 TTL**（原本回退成載入時重算，正好把該欄位要防的升級問題放回來）。
- **快取鍵**：分隔符改用不可能出現在股票代號中的控制字元（原本 `|` 可被代號內容偽造），雜湊截斷由 16 → 32 字元。
- **檔案權限改為 0600**（sidecar 內含使用者自選股清單）；`default_cache_dir` 補上 macOS 分支，且環境變數僅在**絕對路徑**時採用（空值或相對值原本會把快取寫進當前工作目錄）。

### 🟡 其他修正

- **`attach_investor_flow_flags` 補回遺漏的 `.strip()`**：v3.2.0 改寫成 `merge_asof` 時掉了股票側 BaseCode 的 `.strip()`，`StockCode` 帶空白時會比對不到、法人旗標全歸零——「byte-identical」對這類輸入原本並不成立。已驗證修正後與 v3.0.0 一致。
- **移除兩處多餘的整表排序**：實測整表排序由 3 次降為 1 次（`add_prev_close` 之後無任何階段重排），CHANGELOG v3.2.0「9 次 → 單次」的說法過於樂觀，此處一併更正。以 120 組差分測試（含**刻意打亂順序**的輸入）驗證輸出仍與 v3.0.0 逐格一致。
- **Excel 建立失敗可重試**（原本失敗後按鈕消失，必須重跑整次篩選）。
- **注入下載器時，快取清除預設改為 no-op**，避免測試或替身呼叫端意外清掉正式快取。
- 補上 K 線圖成交量顏色與雙破標記錯開的測試（原本改回舊行為不會被發現）。
- 移除死碼 `_SNAPSHOT_RESULT`；`price_cache` 公開 API 補型別註記並改用 `X | None` 慣例。
- **spec.md 新增 §5.2 資料新鮮度與快取契約**，把「不得增量拼接」「新鮮度上限」「fail-open」「磁碟須有界」寫成正式規格；§5.1 補上「暫時性失敗不得被快取沿用」。

## v3.2.1 — 2026-07-24

修復 v3.2.0 造成的 Streamlit Cloud 部署失敗。

- **移除 `requirements.txt` 的 `pyarrow`（部署中斷主因）。** Streamlit Cloud 為 Python 3.14.6，pyarrow 21.0.0 無 cp314 wheel，pip 改走原始碼建置卻找不到 `cmake`，相依安裝失敗使**整個 App 無法啟動**。現代 Streamlit 已不依賴 pyarrow，該行為 v3.2.0 快照快取所加，是唯一肇因。
- **parquet 引擎改列為選用，只在 `requirements-build.txt`（桌面打包）宣告。** 快取的價值本就集中在桌面版（閒置 15 秒即關、每次開啟都是冷啟動）；雲端容器為臨時性，本來就無法受益。桌面 build 與 CI 使用 Python 3.13，pyarrow 有對應 wheel。
- **`price_cache` 於載入時偵測 parquet 引擎（`_PARQUET_AVAILABLE`）。** 無引擎時快取靜默停用（不寫檔、恆為 miss、不拋錯也不刷警告），篩選改走即時下載，功能不受影響。
  - 回歸測試：`test_cache_is_inactive_without_a_parquet_engine`、`test_download_stock_data_falls_back_to_live_without_parquet_engine`。

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

- `build-desktop-executables.yml` 加 `timeout-minutes: 30` 與 `cache: pip`。
  - **（v3.2.3 更正）** 原文宣稱這「直接上限化過去卡到 24 小時的 build」，**這是錯的**：`timeout-minutes` 只在 runner 接手後才開始計時，不涵蓋排隊等待。真正的 24 小時來自 Intel runner 排不到而卡在佇列，該問題由 v3.2.3 移除 `macos-13` 才真正解決。
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
