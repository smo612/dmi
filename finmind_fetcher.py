"""
finmind_fetcher.py
台股全市場掃股系統 — FinMind 資料源（日K）

免費方案可用：
  - TaiwanStockPrice：日K OHLCV，收盤後即有當日資料 ✅
  - TaiwanStockKBar：分鐘K OHLCV，需付費方案 ❌

用途：
  - 取代 update_db.py 的 Yahoo Finance 日K 下載
  - Yahoo Finance 日K 更新時間不穩定（有時收盤後數小時才更新）
  - FinMind 日K 在台股收盤後約 30 分鐘內即可取得

使用前：
  1. 至 https://finmindtrade.com 免費註冊，取得 API token
  2. 將 token 存入 finmind_token.txt（只寫一行）
     或設定環境變數：set FINMIND_TOKEN=你的token

免費額度：每小時 600 requests（1954 檔日K 約需 ~100 requests，綽綽有餘）
"""

import os
import time
import logging
import sqlite3
from datetime import datetime, timedelta

import requests
import pandas as pd

from update_db import DB_PATH, init_db, upsert_daily

log = logging.getLogger(__name__)

FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"
TOKEN_FILE = "finmind_token.txt"
REQUEST_SLEEP = 0.1          # 每次請求間隔（100 ms），< 600 req/hr 極限
BATCH_LOG_EVERY = 200        # 每幾檔印一次進度


# ─── Token ──────────────────────────────────────────────────────────────────

def get_token() -> str:
    token = os.environ.get("FINMIND_TOKEN", "")
    if not token and os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            token = f.read().strip()
    if not token:
        log.warning("找不到 FINMIND_TOKEN，請設定環境變數或建立 finmind_token.txt")
    return token


# ─── 核心下載 ────────────────────────────────────────────────────────────────

def _ticker_to_code(ticker: str) -> str:
    """'2330.TW' / '3008.TWO' → '2330' / '3008'"""
    return ticker.split(".")[0]


def fetch_daily_single(ticker: str, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    """
    從 FinMind 取得單一股票日K。
    回傳欄位：Ticker, Date, Open, High, Low, Close, Volume
    """
    code = _ticker_to_code(ticker)
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": code,
        "start_date": start_date,
        "end_date": end_date or (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d"),
        "token": get_token(),
    }
    try:
        r = requests.get(FINMIND_API_URL, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        if body.get("status") != 200:
            log.debug(f"FinMind [{code}] status={body.get('status')} msg={body.get('msg')}")
            return pd.DataFrame()
        rows = body.get("data")
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df = df.rename(columns={
            "date":  "Date",
            "open":  "Open",
            "max":   "High",
            "min":   "Low",
            "close": "Close",
            "Trading_Volume": "Volume",
        })
        df["Ticker"] = ticker
        df = df[["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        df["Volume"] = df["Volume"].fillna(0).astype(int)
        return df
    except Exception as e:
        log.debug(f"FinMind fetch_daily_single [{code}]: {e}")
        return pd.DataFrame()


# ─── 批次更新 ────────────────────────────────────────────────────────────────

def bulk_update_daily(
    tickers: list[str],
    days: int = 3,
    db_path: str = DB_PATH,
) -> int:
    """
    批次更新 daily_candles。

    tickers: ['2330.TW', '3008.TWO', ...]
    days:    回補天數（1 = 只抓今天；3 = 最近 3 天）
    回傳：寫入總筆數
    """
    start_date = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = init_db(db_path)
    total = 0
    errors = 0

    log.info(f"▶ FinMind 日K 批次更新開始：{len(tickers)} 檔，start={start_date}")

    try:
        for i, ticker in enumerate(tickers, start=1):
            df = fetch_daily_single(ticker, start_date=start_date)
            if not df.empty:
                total += upsert_daily(conn, df)
            else:
                errors += 1

            if i % BATCH_LOG_EVERY == 0:
                log.info(f"  FinMind 日K 進度 {i}/{len(tickers)}，已寫入 {total:,} 筆，失敗 {errors} 檔")

            time.sleep(REQUEST_SLEEP)

    finally:
        conn.close()

    log.info(f"✅ FinMind 日K 完成：寫入 {total:,} 筆，失敗 {errors} 檔")
    return total


# ─── 分鐘K（付費功能說明）────────────────────────────────────────────────────

def intraday_not_available(*args, **kwargs) -> pd.DataFrame:
    """
    FinMind 分鐘K（TaiwanStockKBar）需要付費方案。
    免費方案只提供日K。

    需要分鐘K 的選項：
      1. FinMind 付費方案：https://finmindtrade.com/analysis/#/Sponsor/sponsor
      2. 繼續用 Yahoo Finance（update_db.py 的 download_intraday_single）
         → Yahoo Finance 台股分鐘K 通常在收盤後 2~4 小時更新
         → 建議將 market_watcher.py 的 eod_start 改為 17:00 或 18:00
    """
    log.warning("FinMind 分鐘K 需要付費方案，此函式為佔位符")
    return pd.DataFrame()


# ─── 單次驗證 ────────────────────────────────────────────────────────────────

def verify_token() -> bool:
    """確認 token 有效、API 可連線。"""
    df = fetch_daily_single("2330.TW", start_date=datetime.today().strftime("%Y-%m-%d"))
    if not df.empty:
        log.info(f"Token 驗證成功，2330.TW 今日收盤：{df['Close'].iloc[-1]}")
        return True
    log.warning("Token 驗證失敗或今日無資料")
    return False


# ─── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from update_db import configure_yfinance_cache, get_all_stocks, upsert_stocks

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )

    parser = argparse.ArgumentParser(description="FinMind 日K 更新工具")
    parser.add_argument("--verify", action="store_true", help="只驗證 token")
    parser.add_argument("--days", type=int, default=3, help="回補天數（預設 3）")
    parser.add_argument("--db", type=str, default=DB_PATH, help="DB 路徑")
    args = parser.parse_args()

    configure_yfinance_cache()

    if args.verify:
        verify_token()
    else:
        stocks = get_all_stocks()
        conn = init_db(args.db)
        upsert_stocks(conn, stocks)
        conn.close()
        bulk_update_daily(stocks["ticker"].tolist(), days=args.days, db_path=args.db)
