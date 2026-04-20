# HANDOFF V2

最後更新：2026-04-20

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
  - 預設主題改為日間模式
  - 新增手機版排版：
    - 窄螢幕改成上下堆疊
    - header 換行
    - 統計列改為 mobile-friendly grid
    - 表格可水平捲動
  - 控制面板按鈕與輸入元件放大：
    - 週期按鈕
    - 策略按鈕
    - 數字輸入框
    - 滑桿與數值
    - 執行掃描按鈕

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

| 檔案 | 用途 |
|------|------|
| `update_db.py` | 更新 K 棒資料；`--purple` 產出紫圈報告 |
| `backend_api.py` | FastAPI 主程式；`purple` 只讀預掃資料；`/` 路由回傳 cards 前端 |
| `market_watcher.py` | 盤中常駐 daemon；30m 哨兵觸發增量更新；14:00 盤後整理 |
| `scanner_cards.html` | **主前端**（cards 版）；sticky header / sort / skeleton / clickable |
| `scanner_terminal.html` | 終端機風格前端；琥珀金主題；全寬頂列 |
| `scanner.html` | 原版側欄表格前端 |
| `scanner.py` | 無券商 API 的紫圈基準掃描器 |
| `DEPLOY_TRANSITION.md` | GitHub Pages + Render + ngrok 過渡部署說明 |

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

### 8. 盤中 watcher（常駐）

```powershell
python market_watcher.py
```

- 盤中每 60 秒輪詢；14:00 後自動做盤後整理（日K + 分K + 紫圈）

### 9. 盤中 watcher（只跑一輪測試）

```powershell
python market_watcher.py --once
```

### 10. 盤中 watcher（自訂補棒數）

```powershell
python market_watcher.py --bars 5
```

- 盤中每次增量只 upsert 最近 5 根 K 棒，速度更快

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

## 2026-04-20 新增前端樣式

### Claude Code 新增兩個前端變體

- `scanner_terminal.html`
  - 無側欄，所有控制集中在頂部工具列一行
  - 琥珀金 / 黑色終端機美學
  - TF / 策略 / 窗口 / 日量 / DMI Δ範圍 / 掃描 全部橫排
  - 統計列在第二行（命中 / 掃描 / ms + 搜尋 + meta）
  - 表格佔滿剩餘螢幕高度

- `scanner_cards.html`
  - 左側欄 + 右側卡片格 布局
  - 預設淺色主題，可切換夜間
  - 每個命中標的顯示為卡片（左側有策略色彩標線）
  - 卡片格自適應寬度（`auto-fill` 300px 列）
  - 指標以色票（chip）方式顯示，不再使用純文字 bar
  - 策略切換後所有顏色連動（按鈕 / 滑桿 / TF / 統計數字）

- 兩個新前端均包含：
  - 全部三種策略（DMI / MACD / 紫圈）
  - 所有六個週期（紫圈自動限制為 60m / 1d）
  - 股票名稱顯示
  - 紫圈顯示上次掃描時間
  - API URL 可切換（支援 `?api=` query string + localStorage）
  - 搜尋支援代碼與股名
  - 支援 Enter 快捷鍵觸發掃描

### 前端檔案一覽

| 檔案 | 風格 | 主題 |
|------|------|------|
| `scanner.html` | 側欄 + 表格（原版） | 深色固定 |
| `scanner_terminal.html` | 全寬頂列 + 表格 | 深色琥珀金 |
| `scanner_cards.html` | 側欄 + 卡片格 | 淺色 / 深色切換 |

---

## 2026-04-20 最新補充

- 前端已新增左右兩區字體大小控制
  - `左字` 控制左側面板字體
  - `右字` 控制右側統計、搜尋列、結果表格字體
  - 設定會存到瀏覽器 `localStorage`
- 字體大小控制按鈕已放進上方統計列，和 `命中 / 掃描 / ms` 同排
- 結果表格最後一欄已改成 `成交值`
  - 目前直接以前端用 `close * volume` 計算
  - 不再顯示 `DMI / MACD / 紫圈標籤` 那欄
- 這批改動不需要重掃資料庫
  - 目前 DB 已有 `Close` 與 `Volume`
  - 前端更新後即可直接顯示
- 協作習慣確認
  - 之後只要 Codex 有改 code，要同步更新 `HANDOFF_V2.md`
  - 回覆時要一起附上 `git add / commit / push`
  - 若影響 Render 顯示，要提醒使用者去 Render 重新 deploy
## 2026-04-20 盤中更新計畫

- 目標
  - 盤中由常駐程式自動偵測 `30m` K 棒是否更新
  - 只有在確認新 bar 出現後，才做一次增量 DB 更新
  - 收盤後再把當天資料重更新一次，作為最終乾淨版本

- 參考來源
  - `fishh6.py`
  - 目前確認其邏輯確實接近：
    - 常駐 daemon
    - 定時檢查市場 bar / signature 是否更新
    - 若沒更新就跳過
    - 若更新才做全市場掃描

- 盤中更新規則
  - 多設幾支哨兵股票，不只看單一股票
  - 以 `30m` 作為盤中主觸發週期
  - 隔一段時間去檢查最新 `30m` bar 是否更新
  - 若 `30m` bar 沒更新，就不更新 DB
  - 若 `30m` bar 更新，再做三層檢查：
    - 哨兵 quorum
    - bar 穩定性
    - 市場樣本覆蓋率
  - 通過後才觸發 DB 增量更新

- DB 更新範圍
  - 盤中不要補最近 3 天
  - 盤中只補最近幾根 K 棒
  - 目前討論較合理的是：
    - 最近 `3 ~ 5` 根
    - 比更新最近 3 天快很多
    - 也較不會碰到舊資料
  - 大更新 / 手動維護時，才補較長區間

- 收盤後流程
  - 盤後再重更新一次「今天」的資料
  - 用來清掉盤中可能尚未完全穩定的 bar
  - 盤後版本視為最終正本

- 對舊資料的影響
  - 原則上只碰：
    - 當天
    - 或最近少數幾根 bar
  - 不應回頭重洗很久以前的資料
  - 但盤中仍建議保留 `UPSERT` 能力
    - 避免同一 timestamp 的 bar 在盤中修正時無法覆蓋

- 準確性判斷
  - 這套方法可以大幅提高盤中資料可信度
  - 但不能保證 100% 都是完全不會再變動的收棒
  - 若資料源仍是 `yfinance`：
    - 盤中最後一根 bar 仍可能延遲或修正
    - 因此盤中資料應視為 provisional
    - 收盤後重更新仍然必要

- 目前結論
  - 盤中以 `30m` 作主觸發是目前最適合的方案
  - 盤中只更新最近幾根 bar
  - 盤後重洗當天資料
  - 這套邏輯目前尚未正式寫進主流程 code
  - 下一步可考慮做成：
    - 新的 watcher 腳本
    - 或 `update_db.py --watch` 模式

## 2026-04-20 盤中 watcher 已落地

- 新增檔案
  - `market_watcher.py`

- 功能
  - 以 `30m` 為主觸發週期
  - 使用多哨兵股票偵測最新 `30m` bar 是否更新
  - 若同一根 bar 的 signature 沒變，就略過
  - 若 bar 更新，會做：
    - sentinel quorum 檢查
    - 最新 bar 穩定性檢查
    - 市場樣本 ready ratio 檢查
  - 通過後才做盤中增量 DB 更新
  - 盤中只 upsert 最近幾根 K 棒
  - 14:00 後會再做一次：
    - 今天日K更新
    - 今天分K更新
    - 紫圈預掃描重建

- 使用方式
  - 只跑一輪測試：
    - `python market_watcher.py --once`
  - 常駐執行：
    - `python market_watcher.py`
  - 指定盤中只補最近 5 根：
    - `python market_watcher.py --bars 5`

- 預設行為
  - 盤中輪詢：60 秒
  - 非盤中輪詢：600 秒
  - 盤後整理開始時間：14:00
  - 盤後紫圈：`all`

- 注意事項
  - 這是新增的第三個常駐終端，不取代：
    - `uvicorn`
    - `ngrok`
  - 盤中資料仍視為 provisional
  - 收盤後 14:00 整理版本才是最終正本

## 2026-04-20 哨兵 stale 問題排查與修正

### 問題現象（來自 watch log.txt）

```
[WARNING] sentinel stale：目前已是 2026-04-20 10:53，但最新 30m target 仍停在 2026-04-17 13:00
[WARNING] sentinel stale，直接強制執行盤中增量刷新
✅ 哨兵優先批完成：15m=0 30m=0 60m=0，latest 15m=N/A 30m=N/A 60m=N/A
```

### 根本原因（雙重）

1. **`_detect_target_bar_end()` 用 `period="5d"` 批次下載**
   - 週一早上盤中，yfinance 對 `period="5d"` 仍可能回傳週五的最後一根 bar
   - 原因：yfinance 在開盤初期（有時長達 30–60 分鐘）尚未將當天的最新 bar 發布至 API
   - 結果：stale 偵測正確觸發，但此時確實沒有今天的資料

2. **`download_intraday_single(days=1)` 使用 `start=today, end=tomorrow` 寫法**
   - 當 `days=1` 時，`start="2026-04-20", end="2026-04-21"`
   - yfinance 對這個**特定日期的 start/end 組合**，在開盤初期會回傳空資料
   - 但同樣的資料用 `period="2d"` 方式呼叫卻可正常取得（已驗證：batch 用 period 有拿到資料）

### 修正方式

- **`update_db.py` — `download_intraday_single`**
  - `days ≤ 7` 改用 `period=f"{days+1}d"` 取代 `start/end` 寫法
  - `days > 7` 仍用 `start/end`（yfinance period 最大約 60 天）
  - 實際效果：`days=1` → `period="2d"`，會拿到昨天 + 今天的 bar，`tail(5)` 自動取最新幾根

### 修正後預期行為

- 週一早盤第一輪：`_detect_target_bar_end()` 若仍回傳週五，stale 觸發
- force refresh 執行：`download_intraday_single(days=1)` 改用 `period="2d"`，可拿到週五尾盤的最後幾根 bar 作為 fallback
- 等 yfinance 發布今天的 bar 後（通常 30–60 分鐘內），下一輪輪詢就能正確寫入今天的資料

### 注意事項

- 盤中開頭的 0-row 狀態是暫時現象，不是系統 bug
- market_watcher 每 10 分鐘會再做一次 stale force refresh，持續嘗試
- 若手動想確認資料有沒有進去，可直接查 DB 的最新 `30m` 時間戳

## 2026-04-20 前端字體控制調整

- 右上方字體控制已改成滑桿，不再用 A- / A+ 按鈕
- `左字` / `右字` 的控制列本身字體固定，不跟著放大
- 字體變大時，不再一起放大那一列的按鈕或控制寬度
- 目的：
  - 避免你剛剛遇到的「字變大後整排往右擠、沒辦法連點」
## 2026-04-20 Cards 前端切換與成交值濾網

- 主前端改為 `scanner_cards.html`
  - `backend_api.py` 根路由 `/` 直接回傳 cards 版頁面
  - Render 入口之後以 cards 版為主，不再優先使用舊表格式 `scanner.html`

- cards 版這次新增
  - 成交值濾網：`最低成交值（萬）`
  - sticky header / sticky tools bar
  - 排序：
    - 最新觸發
    - 成交值高到低
    - 成交量高到低
    - 收盤高到低
    - 代碼排序
  - 卡片可點擊，會開 Yahoo 股價頁
  - 控制面板大小滑桿
    - 只調整左側控制面板按鈕 / 輸入框 / 掃描按鈕
    - 不把上方那排說明字一起放大，避免按鈕往右擠

- cards 版目前保留
  - API 可手動切換
  - `?api=...` query string 覆蓋
  - `onrender.com` 預設走目前 ngrok API
  - localStorage 記住 API 與控制面板大小

- 這批不需要重掃 DB
  - 成交值直接吃 API 回傳的 `turnover`
  - DB schema 不變
  - 不需要重建 `stock_data.db`

- 這批上線注意
  - 只改前端時：
    - `uvicorn` 不用手動重開（若本機是 `--reload` 通常會自動處理）
    - `ngrok` 不用重開
  - Render 需要重新部署，讓新 cards 前端上線

### 後續微調

- 成交值顯示
  - cards 卡片與 chip 現在會自動進位：
    - `< 10000 萬` 顯示 `萬`
    - `>= 10000 萬` 顯示 `億`
  - 例：
    - `2974166 萬` 會顯示成 `297.4 億`

- 成交值濾網單位
  - 控制面板預設單位是 `萬`
  - 可切換成 `億`
  - 前端會自動換算回 API 目前使用的 `萬` 單位送出

- 盤中 watcher 備註
  - `market_watcher.py --once` 若在非交易時段執行，出現：
    - `無法偵測最新 30m bar，略過本輪`
    - 這屬正常現象，不代表 watcher 壞掉
  - 目前 watcher 已是多哨兵版
    - `SENTINEL_SYMBOLS` 內建多檔權值 / ETF 哨兵
    - 另有 quorum / stable bar / sample ready ratio 檢查
