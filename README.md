# 紅黑線多空雙向突破回測選股系統

> **聲明：本工具僅供研究與篩選參考，不構成任何投資建議。**

## 系統目標

依方向（做多／做空）分流，找出符合下列任一**訊號路徑**的股票：

| 路徑 | 方向 | 觸發 | 回測判定 |
|------|------|------|----------|
| P1 突破回測 | 做多 | 向上突破紅線或黑線（收盤確認，由下往上） | 突破後窗格內、由上往下測線並守在線上 |
| P2 新線回測 | 做多 | 新紅線或黑線出現（不需突破） | 新線窗格內、由上往下測線並守在線上 |
| P3 跌破回測 | 做空 | 向下跌破紅線或黑線（收盤確認，由上往下） | 跌破後窗格內、由下往上測線並壓在線下 |
| P4 新線回測 | 做空 | 新紅線或黑線出現（不需突破） | 新線窗格內、由下往上測線並壓在線下 |

方向不由紅線／黑線決定，而由突破／跌破方向與回測方向、收盤位置共同決定。一根 K 棒可同時符合多條路徑，會依方向分流為多列輸出。

**v3 的回測方向性**：做多是「由下往上突破、收盤高於線」，之後「由上往下回測、收盤守在線上」；做空為完全相反。程式上以三個條件落實：回測棒的**前一根收盤**在線的正確一側（做多在線上、做空在線下）、回測棒與突破棒**必須不同根**（突破當根不算回測）、回測須在突破後 `retest_window` 根 K 棒內發生，且窗格內一旦收盤穿回基準線即**提前失效**。

流程：

1. 下載日 OHLCV 資料。
2. 依使用者選擇轉成 Daily K / Weekly K / Monthly K。
3. 用前一根收盤價計算大紅攻與大黑攻。
4. 大紅攻成功產生紅線，大黑攻成功產生黑線，分別逐股往後延伸，並標記「新線出現」。
5. 偵測收盤價嚴格向上突破與向下跌破最新紅線或黑線（同根同時突破雙線時，做多取較高線、做空取較低線為回測基準）。
6. 以突破／跌破事件凍結基準線，在 `retest_window` 根窗格內偵測做多守住（P1）與做空壓回（P3），含方向前提與提前失效；另以最新出現的新線在 `new_line_window` 窗格內偵測 P2／P4。
7. 套用最近 N 根 K 棒、成交量與可選法人連續買賣超／賣超條件（連買用於做多、連賣用於做空），再依方向分流。

## 策略定義

攻擊方向只由 `Open` 與 `prev_close` 決定；`Close` 只決定攻擊成功或失敗。

| 訊號 | 條件 | 說明 |
|------|------|------|
| 大紅攻成功 | `Open > prev_close` 且 `Close > prev_close` | 產生紅線，紅線價格為 `prev_close` |
| 大紅攻失敗 | `Open > prev_close` 且 `Close < prev_close` | 不產生黑線 |
| 大黑攻成功 | `Open < prev_close` 且 `Close < prev_close` | 產生黑線，黑線價格為 `prev_close` |
| 大黑攻失敗 | `Open < prev_close` 且 `Close > prev_close` | 不產生紅線 |

向上突破條件（做多 P1 觸發）：

```text
previous Close <= previous line
current Close > current line
```

向下跌破條件（做空 P3 觸發）：

```text
previous Close >= previous line
current Close < current line
```

`previous Close` 等於線價時歸類為突破、不歸跌破。

做多回測（由上往下守住，P1／P2）：

```text
前一根 Close >= 回測線價     # 由上往下：進入回測棒前收盤在線上
Low <= 回測線價             # 當根測到線
Close >= 回測線價           # 當根收盤守住
```

做空回測（由下往上壓回，P3／P4）：

```text
前一根 Close <= 回測線價     # 由下往上：進入回測棒前收盤在線下
High >= 回測線價            # 當根測到線
Close <= 回測線價           # 當根收盤壓回
```

P1／P3 的基準線為「最新被突破／跌破的線」，回測有效範圍為事件後 `retest_window` 根（不含事件當根）；窗格內任一根收盤穿回基準線（做多 `Close < 線`、做空 `Close > 線`）即提前失效。P2／P4 的基準線為「最新出現的新線」，僅在出現後 `new_line_window` 根 K 棒內（不含出現當根）有效。所有窗格一律以所選週期的 K 棒（根）計數，不以日曆交易日計。

## 功能

- 自動抓取 TWSE 上市與上櫃普通股清單，也支援手動輸入或上傳股票清單。
- 支援 Daily K / Weekly K / Monthly K。
- 多空雙向四條訊號路徑（P1～P4），並可用方向過濾只看做多或只看做空。
- 可設定最近回看 K 棒數、最小成交量、突破回測窗格與新線回測窗格。
- 法人條件：外資 / 投信最近 N 日連續買超（用於做多）或連續賣超（用於做空）。
- Plotly K 線圖顯示紅線、黑線、突破／跌破標記與回測守住／壓回標記。
- Streamlit 以「做多」「做空」分頁分開呈現結果與各自 K 線圖。
- Excel 匯出 `All_Data`、`Long_Signals`、`Short_Signals`、`Latest_Summary_Long`、`Latest_Summary_Short`、`Failed_Downloads`、`Parameter_Settings`。

## 安裝與執行（Python 開發模式）

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 桌面執行檔（免 Python）

### 直接使用已封裝版本

- **Windows x64**：下載 `BullishThreeConditionStockScreener-windows-x64.zip`，解壓縮後執行資料夾中的 `BullishThreeConditionStockScreener.exe`。
- **macOS Intel**：下載 `BullishThreeConditionStockScreener-macos-intel.zip`，解壓縮後執行 `BullishThreeConditionStockScreener.app`。
- **macOS Apple Silicon**：下載 `BullishThreeConditionStockScreener-macos-apple-silicon.zip`，解壓縮後執行 `BullishThreeConditionStockScreener.app`。
- 執行檔會自動在本機啟動 Streamlit，並開啟預設瀏覽器。
- **Windows** 若要結束程式，請直接關閉啟動時一起打開的命令視窗；**macOS** 可直接結束 `BullishThreeConditionStockScreener.app`。

> macOS 與 Windows 的封裝檔都不需要另外安裝 Python，但第一次執行未簽章程式時，系統可能會要求手動允許開啟。

### 本機打包

```bash
python -m pip install -r requirements.txt -r requirements-build.txt
python scripts/build_desktop.py
```

打包完成後的產物位置：

- **Windows**：`dist/BullishThreeConditionStockScreener/BullishThreeConditionStockScreener.exe`
- **macOS**：`dist/BullishThreeConditionStockScreener.app`

### GitHub Actions 自動打包

- Workflow 檔案：`.github/workflows/build-desktop-executables.yml`
- 在 GitHub Actions 手動執行 **Build desktop executables**，即可產生 Windows / macOS 下載檔。
- 推送 `v*` 標籤時，workflow 會同時上傳 artifacts，並把 zip 檔附加到 GitHub Release。

## 側邊欄參數

| 參數 | 預設值 | 說明 |
|------|--------|------|
| 自動抓取全市場 | 開啟 | 開啟：自動抓取上市與上櫃普通股；關閉：手動輸入或上傳清單 |
| 開始日期 | 今天-30天 | 資料下載起始日（預設只抓最近一個月，避免全市場 × 長區間在雲端逾時） |
| 結束日期 | 今天 | 資料下載截止日 |
| 分析週期 | Daily K | Daily K / Weekly K / Monthly K |
| 最小成交量（張） | 2000 | 內部換算為股數後套用；門檻套用於所選週期的單根 K 棒量 |
| 回看 K 棒數 | 10 | 只保留最近 N 根 K 棒內的有效訊號 |
| 新線回測窗格（K 棒數） | 5 | 新線出現後幾根 K 棒內仍可作為 P2／P4 回測基準（不含出現當根） |
| 突破回測窗格（K 棒數） | 5 | 突破／跌破後幾根 K 棒內仍可作為 P1／P3 回測基準（不含事件當根，收盤穿回即提前失效） |
| 方向過濾 | 全部 | 全部 / 做多 / 做空 |
| 法人連續買賣超天數 | 3 | 勾選法人條件時使用（連買用於做多、連賣用於做空） |

## 輸入格式

手動模式下，每行一個股票代號，或上傳包含 `StockCode` 欄位的 CSV / Excel：

```text
2330.TW
2317.TW
6182.TWO
```

## 週 K / 月 K

週 K / 月 K 由日線資料重新取樣產生：

| 欄位 | 計算方式 |
|------|----------|
| Open | 期間第一個交易日開盤 |
| High | 期間最高價 |
| Low | 期間最低價 |
| Close | 期間最後一個交易日收盤 |
| Volume | 期間成交量加總 |
| Date | 期間最後一個實際交易日 |
