# 大紅攻 / 大黑攻 訊號選股系統

> **聲明：本工具僅供研究與篩選參考，不構成任何投資建議。**

---

## 系統目標

自動下載台灣上市與上櫃股票的 OHLCV 資料，依據「**大紅攻 / 大黑攻**」策略偵測攻擊訊號，篩選出符合條件的股票供研究參考。

---

## 策略定義

攻擊方向由**開盤價與前一根收盤價**的關係決定；收盤價只決定攻擊是否成功。

**前提：**
```
prev_close = 前一根 K 棒的收盤價
```

| 訊號 | 條件 | 說明 |
|------|------|------|
| 大紅攻成功 | `Open > prev_close` 且 `Close > prev_close` | 多頭開高並收高 |
| 大紅攻失敗 | `Open > prev_close` 且 `Close < prev_close` | 多頭開高但收低（≠ 大黑攻） |
| 大黑攻成功 | `Open < prev_close` 且 `Close < prev_close` | 空頭開低並收低 |
| 大黑攻失敗 | `Open < prev_close` 且 `Close > prev_close` | 空頭開低但收高（≠ 大紅攻） |

> **重要：失敗的大紅攻不等於大黑攻；失敗的大黑攻不等於大紅攻。**

---

## 功能

- 自動抓取 TWSE 上市與上櫃普通股清單（約 1900+ 檔）
- 支援日 K / 週 K / 月 K 分析（週/月 K 由日線 Resample 產生）
- 成交量前置過濾（最小成交量，單位：張）
- 回看 N 根 K 棒視窗篩選
- 互動式 K 線圖（含四種攻擊訊號標記）
- Excel 匯出（All_Data / Matching_Signals / Latest_Summary / Failed_Downloads / Parameter_Settings）

---

## 安裝與執行

### 本機執行

```bash
pip install -r requirements.txt
streamlit run app.py
```

### Streamlit Cloud 部署

1. Fork / Push 此 repo 至 GitHub
2. 至 [share.streamlit.io](https://share.streamlit.io) 建立新應用程式
3. 指向 `app.py`，Python 版本 3.11+
4. 點擊 Deploy

---

## 側邊欄參數說明

| 參數 | 預設值 | 說明 |
|------|--------|------|
| 自動抓取全市場 | 開啟 | 開啟：自動抓取 TWSE 全市場；關閉：手動輸入股票代號 |
| 開始日期 | 今天-2年 | 資料下載起始日 |
| 結束日期 | 今天 | 資料下載截止日 |
| 分析週期 | 日 K | 日 K / 週 K / 月 K |
| 最小成交量（張） | 2000 | Volume ≥ 此值（單位：張，內部換算×1000股）；設 0 = 不篩選 |
| 回看 K 棒數 | 10 | 只顯示最近 N 根 K 棒內出現攻擊訊號的資料 |

---

## 輸入格式

手動模式下，每行一個股票代號：

```
2330.TW
2317.TW
6182.TWO
```

- 上市股票使用 `.TW` 後綴
- 上櫃股票使用 `.TWO` 後綴

---

## 週 K / 月 K 說明

週 K / 月 K 由日線資料 Resample 產生，**不直接使用 yfinance 週線/月線**（避免資料格式不一致）。

| 欄位 | 計算方式 |
|------|----------|
| Open | 期間第一個交易日開盤 |
| High | 期間最高價 |
| Low | 期間最低價 |
| Close | 期間最後一個交易日收盤 |
| Volume | 期間成交量加總 |
| Date | 期間最後一個交易日 |

---

## 結果說明

### 訊號匹配結果

回看視窗內，成交量達標且有任一攻擊訊號的所有 K 棒。

### 最新訊號摘要

每股一列，顯示回看視窗內最新一筆攻擊訊號。

---

## 技術依賴

```
streamlit
pandas
numpy
yfinance
plotly
openpyxl
requests
lxml
```

---

*本工具僅供投資研究篩選參考，並非投資建議。投資決策請自行判斷。*
