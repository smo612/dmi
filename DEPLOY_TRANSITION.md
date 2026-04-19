# 過渡部署：GitHub Pages + Render Free

這份是「先試上網站」版本，不是最終正式架構。

## 架構

- 前端：`scanner.html` 放 GitHub Pages
- 後端：`backend_api.py` 放 Render Free
- 保活：可用 UptimeRobot 打 `/status`

## 重要限制

- Render Free 會在 15 分鐘閒置後休眠
- Render Free 本地檔案系統不是永久保存
- `stock_data.db` 若只放在 Render Free 本地磁碟，重啟 / redeploy / spin down 後可能遺失

結論：
- 可拿來 demo / 小範圍測試
- 不建議當正式長期版本

## 後端部署到 Render

1. 把專案推到 GitHub
2. 到 Render 建立新的 Web Service
3. 指向這個 repo
4. Render 會讀：
   - `requirements.txt`
   - `render.yaml`
5. 啟動後確認：
   - `/status` 可打開
   - 網址類似 `https://your-service.onrender.com`

## 前端部署到 GitHub Pages

1. 把 `scanner.html` 放到 repo
2. 開 GitHub Pages
3. 打開頁面後，點右上角 `API`
4. 輸入 Render 後端網址，例如：

```text
https://your-service.onrender.com
```

5. 前端會把 API 位址存到瀏覽器 `localStorage`

也可以用 query string 臨時指定：

```text
https://yourname.github.io/your-repo/?api=https://your-service.onrender.com
```

## UptimeRobot 建議

- Monitor 類型：HTTP(s)
- 監控網址：

```text
https://your-service.onrender.com/status
```

- 不要打 `/robots.txt`
  - Render Free 在休眠時不會讓 `/robots.txt` 喚醒服務

## 建議流程

1. 先在本機把 `stock_data.db` 更新好
2. 再嘗試 Render 部署 API
3. 只拿來測：
   - 頁面能不能連到 API
   - 功能流程有沒有通
4. 正式版等 Student Pack / 便宜 VPS 再做
