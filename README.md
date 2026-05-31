# 紅黑線突破回測守住選股系統

> **聲明：本工具僅供研究與篩選參考，不構成任何投資建議。**

## 系統目標

找出最近突破最新紅線或黑線，之後回測該線且收盤守住的股票。

流程：

1. 下載日 OHLCV 資料。
2. 依使用者選擇轉成 Daily K / Weekly K / Monthly K。
3. 用前一根收盤價計算大紅攻與大黑攻。
4. 大紅攻成功產生紅線，大黑攻成功產生黑線，分別逐股往後延伸。
5. 偵測收盤價嚴格突破最新紅線或黑線。
6. 記住最新被突破的線，篩出 Low 觸及該線且 Close 沒有跌破該線的回測守住 K 棒。
7. 套用最近 N 根 K 棒、成交量與可選法人連續買賣超條件。

## 策略定義

攻擊方向只由 `Open` 與 `prev_close` 決定；`Close` 只決定攻擊成功或失敗。

| 訊號 | 條件 | 說明 |
|------|------|------|
| 大紅攻成功 | `Open > prev_close` 且 `Close > prev_close` | 產生紅線，紅線價格為 `prev_close` |
| 大紅攻失敗 | `Open > prev_close` 且 `Close < prev_close` | 不產生黑線 |
| 大黑攻成功 | `Open < prev_close` 且 `Close < prev_close` | 產生黑線，黑線價格為 `prev_close` |
| 大黑攻失敗 | `Open < prev_close` 且 `Close > prev_close` | 不產生紅線 |

突破條件：

```text
previous Close <= previous line
current Close > current line
```

回測守住條件：

```text
Low <= active_breakout_line
Close >= active_breakout_line
```

## 功能

- 自動抓取 TWSE 上市與上櫃普通股清單，也支援手動輸入或上傳股票清單。
- 支援 Daily K / Weekly K / Monthly K。
- 可設定最近回看 K 棒數與最小成交量。
- 保留法人條件：外資 / 投信最近 N 日連續買超或連續賣超。
- Plotly K 線圖顯示紅線、黑線、突破標記與回測守住標記。
- Excel 匯出 `All_Data`、`Matching_Retest_Hold`、`Latest_Summary`、`Failed_Downloads`、`Parameter_Settings`。

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
| 開始日期 | 今天-2年 | 資料下載起始日 |
| 結束日期 | 今天 | 資料下載截止日 |
| 分析週期 | Daily K | Daily K / Weekly K / Monthly K |
| 最小成交量（張） | 2000 | 內部換算為股數後套用 |
| 回看 K 棒數 | 10 | 只保留最近 N 根 K 棒內的有效回測守住訊號 |
| 法人連續買賣超天數 | 3 | 勾選法人條件時使用 |

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
