# HANDOFF V2

最後更新：2026-04-19

這份檔案是目前正式交接版本。
舊的 `HANDOFF.md` 保留歷史紀錄，但內容仍有 `360m`、`min_di` 等已過期資訊，後續請以這份為主。

## 專案現況

- 專案定位：台股全市場掃股系統。
- 目前優先完成的是「收盤後版本」，不是盤中即時版。
- `DMI / MACD` 走本地 SQLite 資料庫即時計算。
- `紫圈` 改為盤後預掃描，前端只讀報告，不在 `/scan` 即時重算。

## 目前架構

### 1. 資料庫

- 主檔案：`stock_data.db`
- 主要資料表：
  - `daily_candles`
  - `intraday_candles`
  - `stocks`
  - `purple_signals`

### 2. 週期支援

- 動態掃描：`1d / 15m / 30m / 60m / 180m / 240m`
- 紫圈報告：`1d / 60m`
- 已移除：`360m`

### 3. 策略路徑

- `DMI`：
  - 從 DB 載入 K 棒後即時計算
  - 支援「幾根內金叉」與 `dmi_diff_min ~ dmi_diff_max`
- `MACD`：
  - 從 DB 載入 K 棒後即時計算
  - 只接受金叉發生當下與最新一根都在 0 軸上方
- `紫圈`：
  - 由 `update_db.py --purple` 預先掃描
  - `60m` 直接用 `yfinance Ticker.history(period="1y", interval="1h")`
  - `1d` 直接讀 DB 的 `daily_candles`
  - API 只讀 `purple_signals` 表

## Claude Code 已完成

- 規劃新架構方向：
  - `紫圈` 改成預掃報告
  - 新增 `stocks` 與 `purple_signals` 表
  - 前端紫圈只顯示 `60m / 1d`
  - 改用 `30m` 取代 `360m`
- `update_db.py` 已先改到一半：
  - 加入 `30m`
  - 移除 `360m`
  - 加入 `stocks` / `purple_signals` 表
  - 加上 `--purple` / `--purple-lookback`
  - 紫圈預掃流程雛形已放進去

## Codex 本次完成

- 補完 `backend_api.py`
  - `purple` 不再即時掃描
  - API 啟動時讀取 `stocks` 與 `purple_signals`
  - `/scan` 的 `purple` 改成直接回報告
  - `/status` 增加 `purple_reports`
  - 更新支援週期為 `1d / 15m / 30m / 60m / 180m / 240m`

- 補完 `scanner.html`
  - 加入 `30m`
  - 紫圈模式下只顯示 `60m / 1d`
  - 結果表格顯示股名
  - 搜尋可同時搜代碼與股名
  - 紫圈結果列出「上次掃描時間」
  - 數字欄位預設改成 placeholder，不再出現輸入 `03` 的問題
  - 成交量欄位與篩選文案改為「日K成交量」
  - 紫圈補回「幾根 K 棒內觸發」slider，範圍 `2 ~ 20`

- 修正 `update_db.py`
  - 明確保留 `60m` DB 更新為近 58 天
  - 紫圈 `60m` 的深歷史需求由 `--purple` 路徑單獨處理
  - 新增 `configure_yfinance_cache()`
  - 將 `yfinance` cache 固定到專案內 `.yfinance_cache`
  - 避免 `Ticker.history()` 在某些環境因預設 cache SQLite 路徑失敗，導致紫圈預掃整批變成 0 筆
  - 紫圈預掃 log 改成更明確：
    - 失敗會顯示 `60m purple 失敗 [...]`
    - 若最後 0 筆，會顯示錯誤檔數，不再只是安靜掃完
  - 新增 `--purple-tf`
    - 可單獨重跑 `60m`、`1d` 或 `all`
    - 方便先驗某一個紫圈週期，不必每次兩邊一起跑
  - 更新策略改為「增量回補」：
    - `--daily-days` 預設 `3`
    - `--intraday-days` 預設 `3`
    - 已建好 DB 後，日常盤後更新不再預設重抓 `365 / 58` 天
  - 分鐘線增量更新時會自動縮短 sleep：
    - `3` 天內約 `0.15s`
    - `7` 天內約 `0.3s`
    - 更長回補才用原本保守值

- 修正 `scanner.py`
  - 同步設定 `yfinance` cache 到 `.yfinance_cache`
  - 避免基準掃描器也踩到同一個 cache 路徑問題

- 驗證
  - `python -m py_compile backend_api.py update_db.py scanner.py` 已通過

## 重要檔案

- `update_db.py`
  - 更新 K 棒資料
  - 可選擇額外產出紫圈報告
- `backend_api.py`
  - API 主程式
  - `purple` 只讀預掃資料
- `scanner.html`
  - 本地前端頁面
- `scanner.py`
  - 無券商 API 的紫圈基準掃描器
- `new.txt`
  - 本次 Claude Code 討論原文

## 執行方式

### 只更新 DB

```powershell
python update_db.py --tf all
```

### 更新 DB 並重建紫圈報告

```powershell
python update_db.py --tf all --purple
```

### 只重建紫圈報告

```powershell
python update_db.py --tf 1d --purple
```

說明：
- 目前 `--purple` 仍需要有最新股票名單與日 K 資料。
- 若只想重跑紫圈，至少要保證 DB 內已有可用的 `daily_candles`。

### 啟動 API

```powershell
uvicorn backend_api:app --host 0.0.0.0 --port 8000 --reload
```

### API 文件

- `http://127.0.0.1:8000/docs`

### 前端

- 直接開 `scanner.html`

## 建議驗收順序

1. 先跑：
   `python update_db.py --tf all --purple`
2. 啟動：
   `uvicorn backend_api:app --host 0.0.0.0 --port 8000 --reload`
3. 打開 `/status`
   - 確認有 `1d / 15m / 30m / 60m / 180m / 240m`
   - 確認 `purple_reports` 有 `1d / 60m`
4. 測 `/scan`
   - `dmi`：任一週期
   - `macd`：任一週期
   - `purple`：只測 `60m` 和 `1d`
5. 打開 `scanner.html`
   - 切到紫圈時只剩 `60m / 日K`
   - 切回 `DMI / MACD` 時可看到全部週期

## 已知限制

- 紫圈目前是盤後報告，不是盤中即時掃描。
- `60m` 紫圈為了對齊 `scanner.py`，故意不依賴 DB 內的 58 天 `60m`，而是直接抓 `yfinance 1y / 1h`。
- 與 TradingView 是否逐點完全一致，仍可能受資料源、還原權息、歷史深度影響。
- 目前沒有做 Windows 排程，也沒有做服務常駐。
- 目前 Python 環境仍有既有相容性問題：
  - `numpy 2.2.6`
  - `pyarrow / numexpr / bottleneck` 看起來是用 NumPy 1.x 編譯的
  - 直接 `import backend_api` 的 runtime 驗證會被這個問題卡住
  - 這不是本次邏輯修改造成的，但若 API 啟動失敗，要先處理環境套件版本
- `intraday_candles` 目前 DB 內仍可看到舊的 `360m` 資料列。
  - 新程式已不再使用 `360m`
  - 但資料表裡舊資料尚未主動清掉
  - 這不影響目前 API，因為 `backend_api.py` 已不再暴露 `360m`

## 本輪測試發現

### 1. 紫圈為空的根因

- 測試時發現 `purple_signals` 表為空，前端紫圈 `1d / 60m` 都沒有結果。
- 同時專案內舊的 `purple_signals.json` 仍有 4 筆訊號，表示不是市場完全沒訊號。
- 判斷結果：
  - 問題不是前端
  - 也不像是策略本身完全失效
  - 更像是 `update_db.py --purple` 在 `yfinance.Ticker(...).history()` 這步出錯後，被舊版程式靜默吃掉
- 已修正方式：
  - `update_db.py` / `scanner.py` 都固定 `yfinance` cache 到 `.yfinance_cache`
  - 並補強失敗 log
  - 另外修正 `update_db.py` 紫圈索引 bug：
    - `recent_mask` 在這裡是 `numpy.ndarray`
    - 舊碼誤用 `recent_mask.values`
    - 會導致 `60m purple 失敗 ... 'numpy.ndarray' object has no attribute 'values'`
    - 現已改成直接用 `recent_mask`

### 2. MACD「聖暉* 為何沒掃到」

- 測試標的：`5536.TWO`（聖暉*）
- 使用情境：`30m / MACD / 窗口 11 根 / 最低成交量 1000 張`
- DB 檢查結果：
  - 最新一根 `30m` 成交量：約 `242 張`
  - 真正金叉那根 `2026-04-17 02:00` 成交量：約 `917 張`
- 結論：
  - 它不是 MACD 邏輯漏掃
  - 當時是被成交量門檻擋掉
  - 原本後端成交量條件看的是「該週期最後一根 K 的量」

### 3. 成交量規則已改

- 盤後版本目前統一規則：
  - 不管掃 `15m / 30m / 60m / 180m / 240m / 1d`
  - 成交量門檻都改看「最新日K總量」
- 這個變更已套用到：
  - `DMI`
  - `MACD`
  - `Purple` 報告篩選
- 前端文案也已同步改成：
  - `最低成交量（日K）`
  - 表格欄位顯示 `日量(張)`

### 4. 紫圈二次篩選已補上

- 紫圈仍然是預掃報告，不做即時全市場重算。
- 目前在讀取 `purple_signals` 時，後端會再做兩層二次篩選：
  - `幾根 K 棒內觸發`
  - `日K成交量門檻`
- `幾根 K 棒內` 的判定方式：
  - 依該週期的最新 K 棒回推
  - 最新一根觸發 = `0`
  - 前一根觸發 = `1`
  - 若設定 `4` 根內，則接受 `0 / 1 / 2 / 3`
- 前端紫圈 slider 上限目前固定 `20`
  - 先不超過目前系統既有掃描窗口範圍
  - 避免 UI 給出過大的回溯範圍造成判讀混亂

### 5. 建議的快速重測方式

- 不需要重跑兩小時全量更新
- 若只是補驗證紫圈，先跑：

```powershell
python update_db.py --tf 1d --purple
```

- 若只想先驗 `60m` 紫圈：

```powershell
python update_db.py --tf 1d --purple --purple-tf 60m
```

- 再重開 API 測 `Purple 60m / Purple 1d`

## 下一步建議

- 先把這個「盤後版」完整驗收。
- 若穩定，再考慮：
  - 盤中版
  - 自動排程
  - 紫圈單股 debug 工具
  - TradingView 對照驗證流程

## 未來部署規劃

這一段是「之後正式要給朋友用」時的方向，不是現在立刻要做的事。

### 目前階段

- 現在仍以本機開發為主。
- `update_db.py`、`uvicorn backend_api:app`、`scanner.html` 都先在自己電腦上驗證。
- 目標是先把功能、資料流、策略結果做穩。

### 過渡方案

- 若之後想先讓少數朋友測試：
  - 前端可先放 `GitHub Pages`
  - 後端仍跑在自己電腦
  - 透過 `ngrok` 或類似工具把本機 API 暴露成 `https://...`
- 這是短期 demo / 測試方案，不建議當正式長期架構。

原因：
- 電腦必須一直開著
- 隧道網址可能會變
- 穩定性有限

### 正式方向

- 正式版建議：
  - 前端：`GitHub Pages`
  - 後端：雲端主機上的 `FastAPI`
  - 資料庫：先保留 `SQLite` 也可以，但必須放在持久化磁碟；之後再視情況升級到 PostgreSQL
  - 排程：主機上定時跑 `update_db.py --purple`

### 為什麼不建議一開始完全依賴免費方案

- 免費平台常有休眠機制
- 首次喚醒速度慢
- 本地檔案不一定持久
- 對 SQLite 支援通常不理想
- 目前這個專案不是單純靜態網站，而是有：
  - FastAPI
  - SQLite
  - 盤後更新流程
  - 紫圈預掃報告

所以免費方案可拿來 demo，但不建議當最終正式方案。

### 成本粗估

- Demo / 測試：
  - `0 ~ 1 USD / 月`
  - 例如 GitHub Pages + 免費平台 / ngrok 類方案

- 小規模正式使用：
  - 約 `5 USD / 月` 上下
  - 一台便宜小主機或小型平台方案
  - 這是目前最看好的甜蜜點

- 再往上：
  - 約 `10 ~ 20 USD / 月`
  - 如果之後加上雲端 PostgreSQL、自訂網域、備份、更多用戶，再往上加

### 目前建議結論

- 現在：先本機做穩，不急著部署
- 短期分享：`GitHub Pages + 本機 FastAPI + ngrok`
- 正式上線：優先考慮「小額付費但穩定」的方案，而不是死撐免費

### 未來技術任務

- 把前端 API URL 改成可切換：
  - localhost
  - ngrok
  - 正式雲端 API
- 整理部署所需檔案：
  - `requirements.txt`
  - 啟動指令
  - 排程策略
- 若未來用戶增多，再評估是否從 SQLite 升級到 PostgreSQL

## 日常更新建議

- 目前建議的盤後日常流程：

```powershell
python update_db.py --tf all --purple
```

- 這時會採用預設增量模式：
  - 日K 回補最近 `3` 天
  - 分鐘K 回補最近 `3` 天
  - 紫圈照常重建報告

- 如果之後懷疑漏資料，可手動放大回補範圍，例如：

```powershell
python update_db.py --tf all --daily-days 7 --intraday-days 7 --purple
```

- 首次重建或重大補資料時，才建議使用：

```powershell
python update_db.py --tf all --daily-days 365 --intraday-days 58 --purple
```

## 常用指令表

### 1. 只更新資料庫

```powershell
python update_db.py --tf all
```

- 會更新：
  - `daily_candles`
  - `intraday_candles`
  - `stocks`
- 不會重建 `purple_signals`

### 2. 更新資料庫 + 重建紫圈報告

```powershell
python update_db.py --tf all --purple
```

- 會先更新：
  - `daily_candles`
  - `intraday_candles`
  - `stocks`
- 再重建：
  - `purple_signals`

### 3. 只重建紫圈報告

```powershell
python update_db.py --tf 1d --purple
```

- 適合：
  - K 棒資料已經是最新
  - 只想重新產出紫圈結果

### 4. 只重建 60m 紫圈

```powershell
python update_db.py --tf 1d --purple --purple-tf 60m
```

### 5. 只重建 1d 紫圈

```powershell
python update_db.py --tf 1d --purple --purple-tf 1d
```

### 6. 放大回補範圍

```powershell
python update_db.py --tf all --daily-days 7 --intraday-days 7 --purple
```

### 7. 首次全量重建

```powershell
python update_db.py --tf all --daily-days 365 --intraday-days 58 --purple
```

## 下一階段目標

- 如果目前這版：
  - `DMI`
  - `MACD`
  - `Purple`
  - 前端顯示
  - 盤後更新流程
  都已經穩定
- 那下一個大目標，的確就是「部署到網站可分享」。

不過建議順序仍然是：

1. 先把本機版持續驗收穩定幾天
2. 再把前端 API URL 做成可切換
3. 接著做過渡版：
   - `GitHub Pages + 本機 FastAPI + ngrok`
4. 最後再做正式版部署：
   - 前端靜態頁
   - 雲端 FastAPI
   - 持久化資料

也就是說：
- 若這版功能真的穩了，下一個方向就是上網站
- 但建議先走「過渡分享版」，再走「正式部署版」

## 過渡部署檔案

- `requirements.txt`
  - Render / 雲端 Python 安裝依賴
  - 已固定較穩的版本組合：
    - `numpy==2.2.6`
    - `pandas==3.0.2`
    - `pandas-ta==0.4.71b0`
- `render.yaml`
  - Render Free 測試用設定
  - 已加入 `PYTHON_VERSION=3.12.8`
- `.python-version`
  - 固定 Python `3.12`
- `DEPLOY_TRANSITION.md`
  - GitHub Pages + Render Free + UptimeRobot 的過渡部署說明
- `scanner.html`
  - 已加入 API Base URL 可切換功能
  - 右上角 `API` 按鈕可直接修改
  - 也支援 query string：
    - `?api=https://your-service.onrender.com`
  - 已對 ngrok API 請求加上 `ngrok-skip-browser-warning` header
  - 可減少前端在走 ngrok 過渡方案時被警告頁攔截
  - 若頁面是從 `onrender.com` 開啟，會預設使用目前的 ngrok API
- `backend_api.py`
  - 已新增 `/` 路由，Render 根網址可直接回傳 `scanner.html`
- `.gitignore`
  - 已排除本機 DB、log、快取、測試產物、個人筆記
  - 推 GitHub 時以程式與部署檔為主，不上傳 `stock_data.db`
