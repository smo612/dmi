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
- 協作習慣確認（Codex 與 Claude Code 共同遵守）
  - 只要有改 code，要同步更新 `HANDOFF_V2.md`
  - 回覆時要一起附上 `git add / commit / push` 指令或直接執行
  - 說明哪些終端需要重開（uvicorn / ngrok / market_watcher）
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

## 2026-04-20 Yahoo Finance 延遲根因 + FinMind 日K 替代方案

### 根本原因確認

- Yahoo Finance 台股分鐘K **不在收盤後立即提供**
  - `timestamp` 有今日時間槽（`ts_today=True`），但 close 全為 null（`bar_today=False`）
  - 分鐘K 通常在收盤後 2~4 小時甚至更晚才補上實際數值
  - 這不是程式 bug，是 Yahoo Finance 的資料發布機制
- FinMind 日K（`TaiwanStockPrice`）收盤後 ~30 分鐘即有當日完整資料（**免費**）
- FinMind 分鐘K（`TaiwanStockKBar`）需要付費方案

### 新增檔案：`finmind_fetcher.py`

- 功能：使用 FinMind 免費方案更新 `daily_candles`
- 主要函式：
  - `fetch_daily_single(ticker, start_date)` → 單檔日K DataFrame
  - `bulk_update_daily(tickers, days, db_path)` → 批次寫入 daily_candles
  - `verify_token()` → 驗證 token 是否有效
- 分鐘K 不支援（免費方案限制），有佔位函式說明

### 使用方式

```powershell
# 驗證 token
python finmind_fetcher.py --verify

# 更新所有股票今日日K（收盤後跑）
python finmind_fetcher.py --days 1

# 回補最近 3 天日K
python finmind_fetcher.py --days 3
```

### 你需要做的動作（FinMind 設定）

1. 至 https://finmindtrade.com 免費註冊，取得 API token（不需信用卡）
2. 在專案目錄建立 `finmind_token.txt`，貼上 token（只寫一行）
3. 執行 `python finmind_fetcher.py --verify` 確認 token 有效
4. 之後盤後日K 改用：`python finmind_fetcher.py --days 1`

### 分鐘K 短期建議

- 保留 Yahoo Finance，但將 `market_watcher.py` 的 `--eod-start` 從 `14:00` 改為 `17:00`
- Yahoo Finance 台股分鐘K 通常在 17:00 前後補齊
- 長期若需穩定分鐘K，考慮 FinMind 付費（約 USD 5-10/月）

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

## 2026-04-20 watcher 防重複啟動補強

- `market_watcher.py` 新增單實例 lock 機制
  - 預設 lock 檔：`market_watcher.lock`
  - 若同一資料夾已有另一個 watcher 在跑，再次啟動會直接退出並寫 log
  - 若真的需要平行測試，可手動傳 `--lock-file ""` 停用

- stale 強制刷新冷卻時間改為「開始刷新前就先寫入 state」
  - 舊版是整輪 `run_intraday_incremental_update()` 跑完才寫 `last_stale_force_run_ts`
  - 若使用者在長任務中途又重開 watcher，冷卻資訊會遺失，造成重複全市場刷新
  - 現在會先寫 `last_stale_force_run_ts`，完成後再補 `last_stale_force_done_ts`
  - 同時也會補寫 `last_intraday_bar_key / last_intraday_signature / last_intraday_run_ts`
  - 避免 stale refresh 明明已完成，但 state 仍停在更早一次的盤中執行

- 操作注意
  - `market_watcher.py` 啟動後會把 `update_db.py` 內匯入的函式快照留在記憶體
  - 如果剛改過 `update_db.py` 或 `market_watcher.py`，必須把舊 watcher 全部停掉再重開
  - 否則 log 可能同時混到「舊版 start/end 邏輯」和「新版 period 邏輯」的結果

## 2026-04-20 watcher 盤中速度優化

- 舊版盤中增量刷新太慢的根因
  - 1954 檔全市場逐檔跑
  - 每檔都各自下載 `15m / 30m / 60m`
  - 等於一次盤中刷新要打接近 `1954 * 3` 次 yfinance 請求
  - 即使只 upsert 最近 `5` 根，下載時間仍然遠大於寫 DB 時間

- 已改為批次 15m 下載 + 本地 resample
  - `market_watcher.py` 盤中刷新現在分批下載 `15m`
  - 每批預設 `80` 檔
  - `30m / 60m / 180m / 240m` 全部在本地由 `15m` 合成
  - 不再為每檔額外直抓 `30m / 60m`
  - 若短窗 `15m` 仍只拿到前一交易日，會自動 fallback 成 fish6 風格的 `period="1mo"` 批次下載

- 影響
  - 盤中刷新網路請求量大幅下降
  - 理論上更接近「真的只補最新幾根 bar」
  - 若資料源已有當天 `15m`，全市場刷新速度會明顯快於舊版
  - 若 Yahoo 只對較長 period 提供當天 `15m`，watcher 現在也會嘗試走這條路

- 注意
  - 這只解決「太慢」問題
  - 若 Yahoo 當下連 `15m` 都還沒提供今天資料，DB 仍可能只會重寫舊 bar

## 2026-04-20 Claude 改動 Review 與後續修正方向

- 已確認 `yahoo.py` 直接打 `https://query1.finance.yahoo.com/v8/finance/chart/...`
  - `2026-04-20` 收盤後仍可拿到當天最新分鐘資料
  - 代表問題不在 Yahoo 完全沒有資料
  - 問題比較像是目前程式走 `yfinance` / ready gate 的方式沒有正確吃到最新來源

- 已確認 `fishh6.py` 也不是拿到今天新資料
  - `fishh6.py` 當時輸出仍是 `target=20260417_1300`
  - 只是用舊資料成功完成掃描
  - 不能證明 DB watcher 路線正常

- 已確認 `14:00` 盤後整理目前也會失敗
  - `market_watcher.log` 顯示：
    - `2026-04-20 14:05:10 [盤後] 偵測到 14:00 之後尚未做今日整理，準備執行`
    - 之後 `sample_ready_ratio=87.50% (need 90%)`
    - `2026-04-20 14:20:54 [Ready] timeout`
  - 結果：
    - `market_watch_state.json` 沒有 `last_eod_date`
    - DB 仍停在 `2026-04-17`

- 已 review Claude 提交 `8f2dda8`
  - commit message：`Add direct Yahoo Finance v8 API fetch to bypass yfinance curl_cffi issue`
  - 正向部分：
    - `update_db.py` 新增 `_direct_yahoo_fetch()`
    - `download_intraday_single()` 已改為優先 direct Yahoo API，失敗才 fallback `yfinance`
  - 關鍵缺口：
    - `download_intraday_batch()` 仍然只走 `yf.download(...)`
    - 但 `market_watcher.py` 盤中主路徑目前走的是 `download_intraday_batch()`
    - 所以 watcher 最核心的 stale 問題其實還沒被這筆提交修到
  - 第二個缺口：
    - `market_watcher.py` 的 `_download_latest_ts_batch()` / `_sample_ready_ratio()` / `wait_for_market_ready()`
      仍然依賴 `yfinance`
    - `14:00` 盤後整理前的 ready gate 仍會卡在舊 target
    - 所以盤後 timeout 問題也還沒解

- 目前最準確的結論
  - Claude 這次是「改了半套」
  - `update_db.py` 單檔下載方向是對的
  - 但 watcher 批次盤中更新與 `14:00` 盤後 gate 還沒有改完整

- 下一步建議修正方向
  - 盤中：
    - 把 `download_intraday_batch()` 接到 direct Yahoo chart API
    - 至少讓 watcher 主路徑不再依賴 `yfinance` 批次下載
  - 盤後：
    - 不要再讓 `14:00` 整理被舊 target 的 ready gate 卡住
    - 改成盤後直接執行整理，或至少對 EOD 放寬 / 跳過該 gate
  - 文件：
    - 若後續完成上述修補，需同步補寫到本段交接紀錄

## 2026-04-20 direct Yahoo batch + 盤後 gate 修補已完成

- `update_db.py` 已補完 direct Yahoo 批次主路徑
  - 新增 `_direct_yahoo_fetch_many()`
  - `download_intraday_batch()` 現在會先平行直打 Yahoo chart API
  - 只有直打失敗 / 回空的 ticker 才 fallback 回 `yfinance`
  - 若 fallback 也失敗，仍會保留已成功抓到的 direct Yahoo 結果，不會整批歸零

- `update_intraday()` 已改成只抓 `15m`
  - 不再逐檔直抓 `15m / 30m / 60m`
  - 改為批次抓 `15m` 後，本地合成 `30m / 60m / 180m / 240m`
  - 與 watcher 的盤中增量策略一致
  - 可避免同一批股票重複打多次分鐘線請求

- `market_watcher.py` 的哨兵 / ready 檢查已改接新下載路徑
  - `_download_latest_ts_batch()` 改用 `download_intraday_batch()`
  - `_download_latest_bar_close()` / `_get_market_signature()` 改用 `download_intraday_single()`
  - 代表：
    - 盤中 stale 判斷
    - stable bar 檢查
    - sample ready ratio
    - market signature
    都不再直接依賴原本那條 `yfinance` 批次抓法

- `14:00` 盤後整理 gate 已修正
  - 若仍在盤中，保留原本 ready gate
  - 若已經收盤，直接略過 ready gate 執行盤後整理
  - 避免 `sample_ready_ratio` 卡在舊 target 時，整輪 EOD 直接 timeout 放棄

- 驗證
  - `python -m py_compile update_db.py market_watcher.py backend_api.py scanner.py yahoo.py` 已通過
  - 目前還沒做完整 live watcher 實盤驗證
  - 必須先把舊 watcher 全停掉，再重開新版 `python market_watcher.py`

- 目前判讀
  - 這次修補的重點不是換資料庫結構
  - 而是把「怎麼向 Yahoo 拿分鐘資料」改成 direct chart API 為主
  - 若新版 watcher 重開後仍拿不到當天 bar，才需要再往更底層的請求節流 / source fallback 繼續查

## 2026-04-20 direct Yahoo 進一步驗證結果

- 已確認 `yahoo.py` 原本只看 `timestamp`，會出現假陽性
  - 例如 `2026-04-20 13:15` 會被判成「今天有更新」
  - 但這只代表 Yahoo payload 有今天的 timestamp
  - 不代表該 timestamp 對應的 `Open/High/Low/Close/Volume` 真的有值

- 已直接對 `2330.TW` raw chart payload 驗證
  - `range=2d/5d/1mo` 的 `timestamp` 都能看到 `2026-04-20`
  - 但 `1m / 5m / 15m / 30m` 在 `2026-04-20` 對應的 `OHLCV` 全是 `None`
  - 最後一根非空 `close` 仍停在 `2026-04-17`
  - 所以程式把 `NaN/None` bar 丟掉後，最新可寫入 bar 仍然只剩 `2026-04-17`

- 這表示
  - `direct Yahoo chart API` 雖然比 `yfinance` 更可控
  - 但在這次案例裡，Yahoo 自身回的分鐘 OHLC payload 仍然不完整
  - 問題不是單純「改成 direct API 就一定能補回 `2026-04-20` 的 intraday K」

- 已順手修正 `yahoo.py`
  - 改為同時顯示：
    - `latest_ts`
    - `last_bar`
    - `bar_today`
  - 之後檢查 Yahoo 時，應以 `last_bar` 是否到今天為準，不要只看 `timestamp`

## 2026-04-20 新增 DB / state 快速檢查腳本

- 新增 `check_update_status.py`
  - 用途：
    - 一次檢查 `market_watch_state.json`
    - `intraday_candles` 各週期最新時間
    - `daily_candles` 最新日期
    - `purple_signals` 最新掃描時間
    - `market_watcher.log` 最後一行
  - 預設會以「今天」為目標日期判斷是否已更新
  - 也可手動指定：
    - `python check_update_status.py --date 2026-04-20`

- 判讀重點
  - `intraday_core_ok=True`
    - 代表 `15m / 30m / 60m` 都至少更新到目標日期
  - `daily_ok=True`
    - 代表 `daily_candles` 已更新到目標日期
  - `state_bar_key_today=True`
    - 代表 watcher state 看到的最後 intraday bar key 已是今天
  - `state_eod_today=True`
    - 代表盤後整理已記錄為今天成功跑過
  - `overall`
    - `OK`：上述都到位
    - `PARTIAL`：只有部分成功
    - `STALE`：核心資料仍停在舊日期

## 2026-04-20 Yahoo Finance 分鐘K 發布時間確認 + EOD 時間修正

### 最終確認（yahoo.py 重新驗證後）

- 2026-04-20 收盤後執行 `yahoo.py`，結果：
  ```
  1m:  last_bar=2026-04-20 13:30:00 close=2025.0 bar_today=True
  5m:  last_bar=2026-04-20 13:30:00 close=2025.0 bar_today=True
  15m: last_bar=2026-04-20 13:30:00 close=2025.0 bar_today=True
  30m: last_bar=2026-04-20 13:30:00 close=2025.0 bar_today=True
  60m: last_bar=2026-04-20 13:30:00 close=2025.0 bar_today=True
  ```
- 全部 `bar_today=True`，代表 Yahoo Finance 分鐘K **收盤後確實會補齊**
- 先前 EOD 在 `14:00` 執行時 Yahoo 分鐘K 尚未發布，所以整理失敗、DB 停在上個交易日

### 修正：`DEFAULT_EOD_START` 從 `14:00` 改為 `15:30`

- 修改檔案：`market_watcher.py` 第 64 行
- 舊值：`"14:00"` → 新值：`"15:30"`
- 效果：收盤（13:30）後等待約 2 小時才觸發盤後整理，確保 Yahoo Finance 分鐘K 已發布
- 如需臨時用更晚的時間，CLI 可覆蓋：`python market_watcher.py --eod-start 17:00`

### 你需要做的動作

1. 停止目前三個終端的 watcher（Ctrl+C）
2. 重開 watcher（使用 `--state` 重置避免今日 EOD 已被記錄為完成）：
   ```powershell
   python market_watcher.py
   ```
3. 今天若想立即補資料（現在是收盤後）：
   ```powershell
   python update_db.py --tf all --daily-days 1 --intraday-days 1
   ```

## 2026-04-20 資料源問題全覽與待解清單

### 問題一：日K（daily_candles）不更新

- **症狀**：`check_update_status.py` 顯示 `daily_ok=False`，`daily_candles.latest=2026-04-17`
- **根因**：`download_daily_batch()` 純靠 `yfinance`，沒有 direct Yahoo API fallback
  - yfinance NumPy 2.2.6 相容性問題 → curl_cffi 失敗 → 回退 plain requests 無 User-Agent → Yahoo 可能 block 或回空
  - 分鐘K 已有 `_direct_yahoo_fetch()` 作 fallback，但日K 沒有
- **現狀**：每次需手動 `python update_db.py --tf 1d --daily-days 1` 或 `python finmind_fetcher.py --days 1`
- **待修**：
  - 選項 A：在 `download_daily_batch()` 加 direct Yahoo chart API fallback（`interval=1d`）
  - 選項 B：把日K 改接 FinMind（需先設定 token，收盤後 30 分鐘即有資料，比 Yahoo 穩定）

### 問題二：EOD 即使日K 寫入失敗，仍標記 last_eod_date = today

- **症狀**：state 顯示 `state_eod_today=True`，但 `daily_ok=False`
- **根因**：`run_eod_refresh()` 執行後不管日K 是否真的寫入，`last_eod_date` 都被寫進 state → watcher 認為今天 EOD 完成 → 不再重試
- **後果**：即使 15:30 以後 Yahoo 資料補上，watcher 也不會再跑日K 更新
- **待修**：
  - `run_eod_refresh()` 回傳成功/失敗狀態
  - 只有日K 確實寫入 > 0 筆才寫 `last_eod_date`
  - 或拆成 `last_eod_daily_date` / `last_eod_intraday_date` 分開追蹤

### 問題三：分鐘K 更新時間視窗

- **症狀**：Yahoo 台股分鐘K 在 13:30 收盤後仍為 null，需等約 1~2 小時才補齊
- **已修**：`DEFAULT_EOD_START = "15:30"`（原 14:00）
- **驗證方式**：`python yahoo.py`，確認 `bar_today=True` 後才代表資料可用
- **注意**：若某天 Yahoo 延遲超過 2 小時，15:30 也可能失敗 → 手動補：
  ```powershell
  python update_db.py --tf intraday --intraday-days 1
  ```

### 手動補資料指令速查

```powershell
# 補今日日K（yfinance）
python update_db.py --tf 1d --daily-days 1

# 補今日日K（FinMind，需先設定 token）
python finmind_fetcher.py --days 1

# 補今日分鐘K
python update_db.py --tf intraday --intraday-days 1

# 補全部
python update_db.py --tf all --daily-days 1 --intraday-days 1

# 確認狀態
python check_update_status.py --date 2026-04-20
```

### 優先修正建議

| 優先序 | 問題 | 修法 |
|--------|------|------|
| 高 | 日K 無 fallback | `download_daily_batch()` 加 direct Yahoo 1d，或改接 FinMind |
| 高 | EOD 假成功 | `last_eod_date` 只在日K 真正寫入後才記錄 |
| 低 | 15:30 仍可能太早 | 監控一週，必要時調整為 16:00 |
