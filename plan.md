# 穩定化除錯計畫

## 目標

讓運行在 Streamlit 上的台股篩選器在遇到外部資料異常時**不再整頁崩潰**，而是：

- 隔離單一股票 / 單一 API / 單日資料的失敗，繼續完成篩選
- 把失敗原因清楚顯示給使用者（不再靜默回傳空表）
- 自動股票清單載入、法人過濾、時間框架對應維持正確
- UI、內部邏輯、Excel 匯出三者一致
- 未來修改遵循固定紀律

---

## 錯誤根源與修正清單（依風險排序）

> 狀態標記：`[x]` 已完成、`[ ]` 待辦。行號以修正當下的程式碼為準，後續可能微幅位移。

### B1.〔高〕網路逾時太短、無重試 — `[x]`
- **症狀**：尖峰時段抓 TWSE/TPEX 常逾時，整批失敗。
- **位置**：`data_loader.py` `_get_with_ssl_fallback` / `_request_once`
- **修法**：逾時集中到 `config.py`（`REQUEST_TIMEOUT = 60`、`REQUEST_RETRIES = 2`）；對逾時 / 連線中斷做有上限的重試，SSL 憑證問題仍走 `verify=False` 後援。

### B2.〔高〕yfinance 下載靜默失敗 — `[x]`
- **症狀**：`except Exception: raw_data = pd.DataFrame()` 吞掉所有錯誤，無法分辨「查無資料」與「網路 / 限流失敗」。
- **位置**：`data_loader.py` `download_stock_data`（主下載與備援下載兩處）
- **修法**：`download_stock_data` 多回傳一個 `download_errors` 診斷清單；批次失敗時記錄原因（`logging` + 清單）。`app.py` 以 `st.warning` 顯示樣本原因，Excel `Failed_Downloads` 新增 `DiagnosticNote` 欄。

### B3.〔高〕TWSE/TPEX 法人資料分不清結構錯誤 vs 暫時失敗 — `[x]`
- **症狀**：API 掛掉、欄位變動、單日無資料都回傳同一個空表，使用者完全無感，法人條件靜默全失效。
- **位置**：`data_loader.py` `_fetch_twse_investor_flow`、`_fetch_tpex_investor_flow`、`download_investor_flow_data`
- **修法**：fetch 函式讓網路 / 解碼錯誤往上拋（結構性 / 無資料才回空表）；`download_investor_flow_data` 逐日逐來源 try/except，計數 `fetch_attempts` / `fetch_failures` 並寫入 `DataFrame.attrs`。`app.py` 讀 `attrs` 顯示「N/M 次抓取失敗」提示。

### B4.〔中〕yfinance MultiIndex 正規化無防呆 — `[x]`
- **症狀**：yfinance 版本變動造成欄位結構改變時 `KeyError` 冒泡，中斷整批。
- **位置**：`data_loader.py` `normalize_yfinance_data`
- **修法**：整段解析包 try/except，結構不符時回傳空表並記錄，視為「該檔無可用資料」。

### B5.〔中〕`merge_asof` 法人對齊無防呆 — `[x]`
- **症狀**：Streamlit Cloud 的 pandas 版本差異 / 日期型別不一致時 `merge_asof` 拋錯，整個篩選中斷。
- **位置**：`signal_engine.py` `attach_investor_flow_flags`
- **修法**：以 try/except 包覆該股票的 merge；失敗時旗標退化為 `False`（與 `flow_df.empty` 分支一致），不中斷。

### B6.〔中〕依賴版本範圍過寬 — `[x]`
- **症狀**：Cloud 端易裝到破壞性版本（`lxml`/`bs4` 無上限、`yfinance` 範圍過寬）。
- **位置**：`requirements.txt`
- **修法**：`yfinance>=0.2.54,<0.3`、`lxml>=5.2,<6`，收斂上界。

---

## 仍維持原樣（不動）

- `app.py` 篩選主流程的頂層 try/except 結構維持不變，只讓下游失敗變得「有訊息可顯示」。
- 快取層（`@st.cache_data`）維持現狀；B1~B5 修好後，被快取函式不再拋出未處理例外，快取失敗風險自然下降。

---

## 驗證矩陣

### 每次穩定化後必過
1. `python3 -m py_compile app.py config.py data_loader.py signal_engine.py chart_engine.py export_engine.py`
2. `pytest tests/test_stability.py`
3. 本機 `streamlit run app.py`，用 `2330`、`2454` 加一個不存在的 `9999` 跑一次，確認失敗代號進失敗清單與 `st.warning`，而非整頁報錯。

### 已建立的持久回歸檢查（`tests/test_stability.py`）
1. 週 / 月線重取樣使用實際最後交易日
2. 法人旗標不延伸到最後一個有資料的法人日期之後
3. 突破紅 / 黑線會設定當前回測線
4. 回測失敗不算最終訊號
5. 股票清單表格格式異常會明確報錯
6. yfinance MultiIndex 正規化仍能產出 OHLCV 欄位
7. **（新）** `normalize_yfinance_data` 對畸形輸入回空表、不拋例外（B4）
8. **（新）** `download_stock_data` 下載拋錯時回報 `download_errors`（B2）
9. **（新）** `download_investor_flow_data` 暫時失敗會計數 `fetch_failures`（B3）
10. **（新）** `merge_asof` 失敗時法人旗標退化為 `False`、不中斷（B5）

---

## 最可能變更的檔案

- `data_loader.py`（B1~B4 主要）
- `signal_engine.py`（B5）
- `app.py`、`export_engine.py`（呈現失敗原因）
- `config.py`（逾時 / 重試設定）
- `requirements.txt`（B6）

---

## 變更控制紀律

任何未來涉及策略或資料載入的功能 / 修補，都應同步更新：
1. 程式碼
2. UI 文字
3. Excel 參數表
4. `spec.md`
5. 本 `plan.md`（當優先順序或執行順序改變時）
