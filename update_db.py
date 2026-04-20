"""
update_db.py（升級版）
台股全市場掃股系統 — 多週期資料倉儲更新程式

支援週期：
  - 1d   → daily_candles 表（預設回補最近 3 天；首次可拉長）
  - 15m  → intraday_candles 表（預設回補最近 3 天）
  - 30m  → intraday_candles 表（預設回補最近 3 天）
  - 60m  → intraday_candles 表（預設回補最近 3 天，同時作為 180m / 240m 來源）
  - 180m / 240m → 由 60m resample 合成，存入 intraday_candles

執行方式：
  python update_db.py            # 更新所有週期（每日盤後跑）
  python update_db.py --tf 1d    # 只更新日K
  python update_db.py --tf intraday  # 只更新分鐘線
  python update_db.py --purple   # 更新完後執行紫圈預掃描
  python update_db.py --daily-days 365 --intraday-days 58  # 手動全量補建
"""

import sqlite3
import time
import logging
import argparse
import os
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, timezone

RESAMPLE_CONFIG = {
    "30m": {"rule": "30min", "offset": "1h"},
    "60m": {"rule": "60min", "offset": "1h"},
    # DB 內分鐘線時間目前以 UTC 字串保存；台股 09:00 開盤等於 UTC 01:00
    "180m": {"rule": "180min", "offset": "1h"},
    "240m": {"rule": "240min", "offset": "1h"},
}

# ─── 設定 ──────────────────────────────────────────────────────────────────────
DB_PATH     = "stock_data.db"
YF_CACHE_DIR = ".yfinance_cache"
DEFAULT_DAILY_DAYS = 3
DEFAULT_INTRADAY_DAYS = 3
SLEEP_SEC   = 1.5   # 每批下載後等待秒數（避免被 ban）
BATCH_SIZE  = 20    # 日K 批次大小
INTRA_SLEEP = 2.0   # 分鐘線每檔等待（較保守）
PURPLE_SLEEP = 0.8  # 紫圈預掃描每檔等待

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("update_db.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("curl_cffi").setLevel(logging.CRITICAL)


def configure_yfinance_cache():
    """
    將 yfinance cache 固定到專案內可寫目錄。
    某些環境若使用預設位置，Ticker.history() 可能因快取 SQLite 無法開啟而整批失敗。
    """
    os.makedirs(YF_CACHE_DIR, exist_ok=True)
    try:
        yf.set_tz_cache_location(os.path.abspath(YF_CACHE_DIR))
        log.info(f"yfinance cache 位置：{os.path.abspath(YF_CACHE_DIR)}")
    except Exception as e:
        log.warning(f"設定 yfinance cache 位置失敗：{e}")


def get_intraday_sleep(days: int) -> float:
    """
    增量更新時縮短等待，全量回補時保持保守。
    """
    if days <= 3:
        return 0.15
    if days <= 7:
        return 0.3
    return INTRA_SLEEP


# ─── 1. 初始化資料庫 ────────────────────────────────────────────────────────────
def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    # 日K 表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_candles (
            Ticker  TEXT NOT NULL,
            Date    TEXT NOT NULL,
            Open    REAL, High REAL, Low REAL, Close REAL, Volume INTEGER,
            PRIMARY KEY (Ticker, Date)
        );
    """)

    # 分鐘K 表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS intraday_candles (
            Ticker    TEXT NOT NULL,
            Timeframe TEXT NOT NULL,
            Datetime  TEXT NOT NULL,
            Open      REAL, High REAL, Low REAL, Close REAL, Volume INTEGER,
            PRIMARY KEY (Ticker, Timeframe, Datetime)
        );
    """)

    # 股票名稱表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            Ticker  TEXT NOT NULL PRIMARY KEY,
            Name    TEXT,
            Market  TEXT
        );
    """)

    # 紫圈預計算結果表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS purple_signals (
            Ticker      TEXT NOT NULL,
            Timeframe   TEXT NOT NULL,
            TriggerTime TEXT NOT NULL,
            Close       REAL,
            Volume      INTEGER,
            ScanAt      TEXT,
            PRIMARY KEY (Ticker, Timeframe, TriggerTime)
        );
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily     ON daily_candles    (Ticker, Date DESC);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_intraday  ON intraday_candles (Ticker, Timeframe, Datetime DESC);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_purple    ON purple_signals   (Timeframe, TriggerTime DESC);")
    conn.commit()
    log.info(f"資料庫初始化完成：{db_path}")
    return conn


# ─── 2. 股票名單 ────────────────────────────────────────────────────────────────
def fetch_twse_stocks() -> pd.DataFrame:
    """上市普通股（台灣證交所 OpenAPI）"""
    try:
        resp = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=15)
        resp.raise_for_status()
        df = pd.DataFrame(resp.json())[["公司代號", "公司簡稱"]]
        df.columns = ["code", "name"]
        df["market"] = "TW"
        df = df[df["code"].str.match(r"^\d{4}$")]
        log.info(f"上市普通股：{len(df)} 檔")
        return df
    except Exception as e:
        log.error(f"上市名單失敗：{e}")
        return pd.DataFrame(columns=["code", "name", "market"])


def fetch_tpex_stocks() -> pd.DataFrame:
    """上櫃普通股（櫃買中心 OpenAPI）"""
    try:
        resp = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=15)
        resp.raise_for_status()
        df = pd.DataFrame(resp.json())[["SecuritiesCompanyCode", "CompanyName"]]
        df.columns = ["code", "name"]
        df["market"] = "TWO"
        df = df[df["code"].str.match(r"^\d{4}$")]
        log.info(f"上櫃普通股：{len(df)} 檔")
        return df
    except Exception as e:
        log.error(f"上櫃名單失敗：{e}")
        return pd.DataFrame(columns=["code", "name", "market"])


def get_all_stocks() -> pd.DataFrame:
    stocks = pd.concat([fetch_twse_stocks(), fetch_tpex_stocks()], ignore_index=True)
    stocks["ticker"] = stocks["code"] + "." + stocks["market"]
    log.info(f"全市場合計：{len(stocks)} 檔")
    return stocks[["ticker", "code", "name", "market"]]


# ─── 3. UPSERT 工具 ─────────────────────────────────────────────────────────────
def upsert_stocks(conn: sqlite3.Connection, stocks: pd.DataFrame):
    """寫入股票名稱對照表。"""
    rows = [(row["ticker"], row["name"], row["market"]) for _, row in stocks.iterrows()]
    conn.executemany("""
        INSERT INTO stocks (Ticker, Name, Market)
        VALUES (?, ?, ?)
        ON CONFLICT(Ticker) DO UPDATE SET Name=excluded.Name, Market=excluded.Market
    """, rows)
    conn.commit()
    log.info(f"stocks 表更新：{len(rows)} 檔")


def upsert_daily(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    conn.executemany("""
        INSERT INTO daily_candles (Ticker, Date, Open, High, Low, Close, Volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(Ticker, Date) DO UPDATE SET
            Open=excluded.Open, High=excluded.High, Low=excluded.Low,
            Close=excluded.Close, Volume=excluded.Volume
    """, df[["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"]].values.tolist())
    conn.commit()
    return len(df)


def upsert_intraday(conn: sqlite3.Connection, df: pd.DataFrame, timeframe: str) -> int:
    if df.empty:
        return 0
    df = df.copy()
    df["Timeframe"] = timeframe
    conn.executemany("""
        INSERT INTO intraday_candles (Ticker, Timeframe, Datetime, Open, High, Low, Close, Volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(Ticker, Timeframe, Datetime) DO UPDATE SET
            Open=excluded.Open, High=excluded.High, Low=excluded.Low,
            Close=excluded.Close, Volume=excluded.Volume
    """, df[["Ticker", "Timeframe", "Datetime", "Open", "High", "Low", "Close", "Volume"]].values.tolist())
    conn.commit()
    return len(df)


# ─── 4. 下載函式 ────────────────────────────────────────────────────────────────
def _flatten_yf(raw: pd.DataFrame, single_ticker: str = None) -> pd.DataFrame:
    """統一處理 yfinance MultiIndex 欄位，回傳扁平 DataFrame。"""
    if raw.empty:
        return pd.DataFrame()
    raw = raw.reset_index()
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.stack(level=1, future_stack=True).reset_index()
        raw.columns.name = None
        if "level_1" in raw.columns:
            raw = raw.rename(columns={"level_1": "Ticker"})
        elif "Ticker" not in raw.columns and single_ticker:
            raw["Ticker"] = single_ticker
    else:
        if single_ticker:
            raw["Ticker"] = single_ticker
    for col in ["Datetime", "Date", "index"]:
        if col in raw.columns:
            raw = raw.rename(columns={col: "_dt"})
            break
    return raw


_YF_API_HEADERS = {"User-Agent": "Mozilla/5.0"}
_YF_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_DAYS_TO_RANGE = {1: "2d", 2: "5d", 3: "5d", 5: "5d", 7: "7d"}


def _direct_yahoo_fetch(ticker: str, interval: str, days: int = 3) -> pd.DataFrame:
    """直接打 Yahoo Finance v8 API。
    繞過 yfinance 的 curl_cffi 問題，確保盤中能拿到最新 bar。
    """
    range_str = _DAYS_TO_RANGE.get(days, f"{min(days, 30)}d")
    url = _YF_CHART_URL.format(symbol=ticker)
    params = {"range": range_str, "interval": interval, "includePrePost": "false"}
    try:
        r = requests.get(url, params=params, headers=_YF_API_HEADERS, timeout=20)
        r.raise_for_status()
        result = r.json()["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        if not timestamps:
            return pd.DataFrame()
        quote = result["indicators"]["quote"][0]
        df = pd.DataFrame({
            "Ticker": ticker,
            "Datetime": [
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                for ts in timestamps
            ],
            "Open":   quote.get("open",   [None] * len(timestamps)),
            "High":   quote.get("high",   [None] * len(timestamps)),
            "Low":    quote.get("low",    [None] * len(timestamps)),
            "Close":  quote.get("close",  [None] * len(timestamps)),
            "Volume": [v or 0 for v in quote.get("volume", [0] * len(timestamps))],
        })
        df = df.dropna(subset=["Close"])
        df["Volume"] = df["Volume"].astype(int)
        return df
    except Exception as e:
        log.debug(f"直接 API 失敗 [{ticker} {interval}]：{e}")
        return pd.DataFrame()


def _build_intraday_download_kwargs(interval: str, days: int, period_override: str | None = None) -> dict:
    kwargs = {
        "interval": interval,
        "auto_adjust": True,
        "progress": False,
    }
    if period_override:
        kwargs["period"] = period_override
        return kwargs
    # 短天數（≤7 天）優先使用 period，避免盤中 start/end 組合回傳空資料。
    if days <= 7:
        kwargs["period"] = f"{days + 1}d"
    else:
        end_dt = datetime.today() + timedelta(days=1)
        start_dt = end_dt - timedelta(days=days)
        kwargs["start"] = start_dt.strftime("%Y-%m-%d")
        kwargs["end"] = end_dt.strftime("%Y-%m-%d")
    return kwargs


def _extract_intraday_frame(raw: pd.DataFrame, ticker: str | None = None) -> pd.DataFrame:
    """從單檔或多檔 yfinance download 結果抽出標準 OHLCV DataFrame。"""
    if raw is None or raw.empty:
        return pd.DataFrame()

    frame = raw.copy()
    if ticker is not None and isinstance(raw.columns, pd.MultiIndex):
        frame = pd.DataFrame()
        for level in range(raw.columns.nlevels):
            try:
                candidate = raw.xs(ticker, axis=1, level=level, drop_level=True)
                if candidate is not None and not candidate.empty:
                    frame = candidate.copy()
                    break
            except Exception:
                continue
        if frame.empty:
            return pd.DataFrame()

    frame = frame.reset_index()
    if isinstance(frame.columns, pd.MultiIndex):
        flat_cols = []
        for col in frame.columns:
            if not isinstance(col, tuple):
                flat_cols.append(col)
                continue
            left = col[0]
            right = col[1] if len(col) > 1 else ""
            if left in {"Datetime", "Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"}:
                flat_cols.append(left)
            elif right in {"Datetime", "Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"}:
                flat_cols.append(right)
            else:
                flat_cols.append(left or right)
        frame.columns = flat_cols

    dt_col = next((c for c in frame.columns if c in ("Datetime", "Date", "index")), None)
    if dt_col is None:
        dt_col = frame.columns[0]

    frame = frame.rename(columns={dt_col: "Datetime"})
    needed = ["Open", "High", "Low", "Close", "Volume"]
    if any(col not in frame.columns for col in needed):
        return pd.DataFrame()

    if ticker is not None:
        frame["Ticker"] = ticker
    if "Ticker" not in frame.columns:
        return pd.DataFrame()

    frame["Datetime"] = pd.to_datetime(frame["Datetime"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    frame = frame[["Ticker", "Datetime", "Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    frame["Volume"] = frame["Volume"].fillna(0).astype(int)
    return frame


def download_daily_batch(tickers: list, start: str, end: str) -> pd.DataFrame:
    """批次下載日K，回傳標準 DataFrame。"""
    try:
        raw = yf.download(
            tickers=" ".join(tickers),
            start=start, end=end,
            interval="1d",
            auto_adjust=True,
            progress=False, threads=True,
            group_by="ticker",
        )
        if raw.empty:
            return pd.DataFrame()

        raw = raw.reset_index()
        if isinstance(raw.columns, pd.MultiIndex):
            frames = []
            for tk in tickers:
                try:
                    sub = raw.xs(tk, axis=1, level=1).copy()
                    sub["Ticker"] = tk
                    sub["Date"] = pd.to_datetime(raw[("Date", "")]).dt.strftime("%Y-%m-%d") if ("Date", "") in raw.columns else pd.to_datetime(raw.iloc[:, 0]).dt.strftime("%Y-%m-%d")
                    frames.append(sub)
                except Exception:
                    pass
            if not frames:
                return pd.DataFrame()
            df = pd.concat(frames, ignore_index=True)
        else:
            df = raw.copy()
            df["Ticker"] = tickers[0]
            date_col = [c for c in df.columns if "date" in c.lower() or "datetime" in c.lower()]
            df["Date"] = pd.to_datetime(df[date_col[0]]).dt.strftime("%Y-%m-%d") if date_col else pd.to_datetime(df.iloc[:, 0]).dt.strftime("%Y-%m-%d")

        df = df[["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        df["Volume"] = df["Volume"].fillna(0).astype(int)
        return df

    except Exception as e:
        log.warning(f"日K批次下載失敗 {tickers[:3]}：{e}")
        return pd.DataFrame()


def download_intraday_single(
    ticker: str,
    interval: str,
    days: int | None = None,
    period_override: str | None = None,
) -> pd.DataFrame:
    """
    下載單一股票分鐘K。
    優先用直接 v8 API（確保盤中能拿到最新 bar），失敗才 fallback yfinance。
    """
    if days is None:
        days = DEFAULT_INTRADAY_DAYS

    # 優先：直接打 Yahoo Finance v8 API，不受 yfinance curl_cffi 問題影響
    if not period_override:
        df = _direct_yahoo_fetch(ticker, interval, days=days)
        if not df.empty:
            return df

    # Fallback：yfinance download
    try:
        raw = yf.download(ticker, **_build_intraday_download_kwargs(interval, days, period_override=period_override))
        if raw.empty:
            return pd.DataFrame()
        return _extract_intraday_frame(raw, ticker=ticker)
    except Exception as e:
        log.warning(f"分鐘K下載失敗 [{ticker} {interval}]：{e}")
        return pd.DataFrame()


def download_intraday_batch(
    tickers: list[str],
    interval: str,
    days: int | None = None,
    period_override: str | None = None,
) -> pd.DataFrame:
    """
    批次下載多檔分鐘K。
    主要給盤中 watcher 用，減少一檔一請求造成的延遲。
    """
    if not tickers:
        return pd.DataFrame()
    if len(tickers) == 1:
        return download_intraday_single(tickers[0], interval, days=days, period_override=period_override)

    if days is None:
        days = DEFAULT_INTRADAY_DAYS

    try:
        raw = yf.download(
            tickers=" ".join(tickers),
            threads=True,
            group_by="ticker",
            **_build_intraday_download_kwargs(interval, days, period_override=period_override),
        )
        if raw.empty:
            return pd.DataFrame()

        frames = []
        for ticker in tickers:
            df = _extract_intraday_frame(raw, ticker=ticker)
            if not df.empty:
                frames.append(df)

        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    except Exception as e:
        log.warning(f"分鐘K批次下載失敗 [{interval} {tickers[:3]}...]：{e}")
        return pd.DataFrame()


def resample_from_60m(df_60m: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """
    將 60m K 線 resample 合成較大週期 K 線。
    支援：180m（前 3h 一根）、240m（半日壓縮）
    """
    if df_60m.empty:
        return pd.DataFrame()
    if timeframe not in RESAMPLE_CONFIG:
        raise ValueError(f"不支援的 resample 週期：{timeframe}")

    cfg = RESAMPLE_CONFIG[timeframe]
    frames = []
    for ticker, g in df_60m.groupby("Ticker"):
        g = g.copy()
        g["dt"] = pd.to_datetime(g["Datetime"])
        g = g.set_index("dt").sort_index()

        resampled = g[["Open", "High", "Low", "Close", "Volume"]].resample(
            cfg["rule"],
            origin="start_day",
            offset=cfg["offset"],
        ).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        resampled = resampled.dropna(subset=["Close"]).reset_index()
        resampled["Ticker"]   = ticker
        resampled["Datetime"] = resampled["dt"].dt.strftime("%Y-%m-%d %H:%M:%S")
        frames.append(resampled[["Ticker", "Datetime", "Open", "High", "Low", "Close", "Volume"]])

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def resample_from_15m(df_15m: pd.DataFrame, timeframe: str = "30m") -> pd.DataFrame:
    """
    將 15m K 線 resample 合成較大週期。
    盤中 watcher 會直接以 15m 作為唯一下載來源，再本地合成 30m / 60m / 180m / 240m。
    """
    if df_15m.empty:
        return pd.DataFrame()
    if timeframe not in ("30m", "60m", "180m", "240m"):
        raise ValueError(f"不支援的 15m resample 週期：{timeframe}")

    cfg = RESAMPLE_CONFIG[timeframe]
    frames = []
    for ticker, g in df_15m.groupby("Ticker"):
        g = g.copy()
        g["dt"] = pd.to_datetime(g["Datetime"])
        g = g.set_index("dt").sort_index()

        resampled = g[["Open", "High", "Low", "Close", "Volume"]].resample(
            cfg["rule"],
            origin="start_day",
            offset=cfg["offset"],
        ).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        resampled = resampled.dropna(subset=["Close"]).reset_index()
        resampled["Ticker"] = ticker
        resampled["Datetime"] = resampled["dt"].dt.strftime("%Y-%m-%d %H:%M:%S")
        frames.append(resampled[["Ticker", "Datetime", "Open", "High", "Low", "Close", "Volume"]])

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ─── 5. 紫圈預掃描 ─────────────────────────────────────────────────────────────
def _scan_purple_from_df(df: pd.DataFrame, lookback_days: int = 7, is_daily: bool = False) -> list[dict]:
    """
    紫圈訊號偵測（移植自 scanner.py check_purple_signal）。
    df 需有 DatetimeIndex 及 Close、Volume 欄位。
    回傳 [{"trigger_time": str, "close": float, "volume": int}, ...]
    """
    if len(df) < 216:
        return []

    df = df.copy()
    df["_sS"]  = df["Close"].rolling(36).mean()
    df["_sL"]  = df["Close"].rolling(216).mean()
    df["_vma"] = df["Volume"].rolling(20).mean()

    is_spike = df["Volume"] > (df["_vma"] * 2.5)
    near_s   = (df["Close"] - df["_sS"]).abs() / df["_sS"] < 0.002
    near_l   = (df["Close"] - df["_sL"]).abs() / df["_sL"] < 0.002

    df["_sp"] = np.nan
    df.loc[is_spike & (near_s | near_l), "_sp"] = df["Close"]
    df["_sp"] = df["_sp"].ffill()

    gold = (df["_sS"] > df["_sL"]) & (df["_sS"].shift(1) <= df["_sL"].shift(1))
    up   = df["_sp"].notna() & (df["Close"] > df["_sp"]) & (df["_sS"] > df["_sL"])
    df["_p"] = (gold & up).fillna(False)

    hits = df[df["_p"]].copy()
    if hits.empty:
        return []

    hit_idx = hits.index
    if getattr(hit_idx, "tz", None) is not None:
        hit_idx_naive = hit_idx.tz_localize(None)
    else:
        hit_idx_naive = hit_idx

    cutoff = datetime.now() - timedelta(days=lookback_days)
    recent_mask = hit_idx_naive > cutoff
    hits = hits[recent_mask]
    hit_idx_naive = hit_idx_naive[recent_mask]

    # Filter tail-session false signal (last candle at 13:xx Taiwan time)
    last_naive = df.index[-1]
    if getattr(last_naive, "tzinfo", None) is not None:
        last_naive = last_naive.tz_localize(None)

    time_fmt = "%Y-%m-%d" if is_daily else "%Y-%m-%d %H:%M"
    results = []
    for t, (_, row) in zip(hit_idx_naive, hits.iterrows()):
        if not is_daily and t == last_naive and t.hour == 13:
            continue
        results.append({
            "trigger_time": t.strftime(time_fmt),
            "close":  float(row["Close"]),
            "volume": int(row["Volume"]),
        })
    return results


def update_purple_signals(
    conn: sqlite3.Connection,
    stocks: pd.DataFrame,
    lookback_days: int = 7,
    purple_tf: str = "all",
):
    """
    全市場紫圈預掃描。
    60m：每檔呼叫 yfinance history(period="1y")
    1d ：直接讀 DB daily_candles
    結果寫入 purple_signals 表。
    """
    log.info(f"▶ 開始紫圈預掃描（週期：{purple_tf}，回溯 {lookback_days} 天）")
    scan_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 清除舊訊號（因回溯窗口可能已過期）
    if purple_tf == "all":
        conn.execute("DELETE FROM purple_signals WHERE Timeframe IN ('60m', '1d')")
    else:
        conn.execute("DELETE FROM purple_signals WHERE Timeframe = ?", (purple_tf,))
    conn.commit()

    tickers_list = stocks["ticker"].tolist()
    total = len(tickers_list)
    all_signals: list[tuple] = []
    fetch_errors = 0

    for i, ticker in enumerate(tickers_list):
        if (i + 1) % 200 == 0:
            log.info(f"  紫圈掃描進度 {i+1}/{total}（累計 {len(all_signals)} 筆，錯誤 {fetch_errors} 檔）")

        if purple_tf in ("all", "60m"):
            # ── 60m：直接用 yfinance 抓 1 年歷史（與 scanner.py 相同邏輯）
            try:
                df60 = yf.Ticker(ticker).history(period="1y", interval="1h", auto_adjust=True)
                if not df60.empty:
                    hits = _scan_purple_from_df(df60, lookback_days=lookback_days, is_daily=False)
                    for h in hits:
                        all_signals.append((ticker, "60m", h["trigger_time"], h["close"], h["volume"], scan_at))
            except Exception as e:
                fetch_errors += 1
                log.warning(f"  60m purple 失敗 [{ticker}]：{e}")

            time.sleep(PURPLE_SLEEP)

        if purple_tf in ("all", "1d"):
            # ── 1d：從 DB 讀取
            try:
                df1d = pd.read_sql(
                    "SELECT Date, Open, High, Low, Close, Volume FROM daily_candles "
                    "WHERE Ticker=? ORDER BY Date ASC",
                    conn, params=[ticker],
                )
                if not df1d.empty:
                    df1d.index = pd.to_datetime(df1d["Date"])
                    df1d = df1d.drop(columns=["Date"])
                    hits = _scan_purple_from_df(df1d, lookback_days=lookback_days, is_daily=True)
                    for h in hits:
                        all_signals.append((ticker, "1d", h["trigger_time"], h["close"], h["volume"], scan_at))
            except Exception as e:
                fetch_errors += 1
                log.warning(f"  1d purple 失敗 [{ticker}]：{e}")

    if all_signals:
        conn.executemany("""
            INSERT INTO purple_signals (Ticker, Timeframe, TriggerTime, Close, Volume, ScanAt)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(Ticker, Timeframe, TriggerTime) DO UPDATE SET
                Close=excluded.Close, Volume=excluded.Volume, ScanAt=excluded.ScanAt
        """, all_signals)
        conn.commit()

    if not all_signals:
        log.warning(f"⚠ 紫圈預掃描完成但無任何訊號（週期：{purple_tf}，錯誤檔數：{fetch_errors}）")
    else:
        log.info(f"✅ 紫圈預掃描完成：{len(all_signals)} 筆訊號（週期：{purple_tf}，錯誤檔數：{fetch_errors}）")


# ─── 6. 更新日K ─────────────────────────────────────────────────────────────────
def update_daily(conn: sqlite3.Connection, tickers: list, days: int = DEFAULT_DAILY_DAYS):
    log.info(f"▶ 開始更新日K（daily_candles，回補最近 {days} 天）")
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    total_written = 0

    for i in range(0, len(tickers), BATCH_SIZE):
        batch   = tickers[i: i + BATCH_SIZE]
        batch_n = i // BATCH_SIZE + 1
        total_n = (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE
        log.info(f"日K 批次 {batch_n}/{total_n}（{batch[0]}~{batch[-1]}）")
        df = download_daily_batch(batch, start, end)
        w  = upsert_daily(conn, df)
        total_written += w
        log.info(f"  → 寫入 {w} 筆")
        time.sleep(SLEEP_SEC)

    log.info(f"✅ 日K 完成，共 {total_written:,} 筆")


# ─── 7. 更新分鐘K ───────────────────────────────────────────────────────────────
def update_intraday(conn: sqlite3.Connection, tickers: list, days: int = DEFAULT_INTRADAY_DAYS):
    sleep_sec = get_intraday_sleep(days)
    log.info(f"▶ 開始更新分鐘K（intraday_candles，回補最近 {days} 天，sleep={sleep_sec}s）")
    writes = {"15m": 0, "30m": 0, "60m": 0, "180m": 0, "240m": 0}
    total = len(tickers)

    for i, ticker in enumerate(tickers):
        if (i + 1) % 100 == 0:
            log.info(
                "  分鐘K 進度 %s/%s，15m=%s 30m=%s 60m=%s 180m=%s 240m=%s",
                i + 1, total,
                writes["15m"], writes["30m"], writes["60m"], writes["180m"], writes["240m"],
            )

        # 15m
        df15 = download_intraday_single(ticker, "15m", days=days)
        if not df15.empty:
            writes["15m"] += upsert_intraday(conn, df15, "15m")

        # 30m
        df30 = download_intraday_single(ticker, "30m", days=days)
        if not df30.empty:
            writes["30m"] += upsert_intraday(conn, df30, "30m")

        # 60m（同時作為較大週期原料）
        df60 = download_intraday_single(ticker, "60m", days=days)
        if not df60.empty:
            writes["60m"] += upsert_intraday(conn, df60, "60m")
            for timeframe in ("180m", "240m"):
                df_rs = resample_from_60m(df60, timeframe)
                if not df_rs.empty:
                    writes[timeframe] += upsert_intraday(conn, df_rs, timeframe)

        time.sleep(sleep_sec)

    log.info(
        "✅ 分鐘K 完成：15m=%s / 30m=%s / 60m=%s / 180m=%s / 240m=%s 筆",
        f"{writes['15m']:,}", f"{writes['30m']:,}", f"{writes['60m']:,}",
        f"{writes['180m']:,}", f"{writes['240m']:,}",
    )


# ─── 8. 主流程 ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="台股多週期資料更新")
    parser.add_argument(
        "--tf",
        choices=["1d", "intraday", "all"],
        default="all",
        help="1d=只更新日K，intraday=只更新分鐘線，all=全部（預設）",
    )
    parser.add_argument(
        "--purple",
        action="store_true",
        help="更新完畢後執行紫圈預掃描（寫入 purple_signals 表）",
    )
    parser.add_argument(
        "--purple-lookback",
        type=int,
        default=7,
        dest="purple_lookback",
        help="紫圈回溯天數，預設 7",
    )
    parser.add_argument(
        "--purple-tf",
        choices=["60m", "1d", "all"],
        default="all",
        dest="purple_tf",
        help="紫圈預掃描週期：60m / 1d / all（預設）",
    )
    parser.add_argument(
        "--daily-days",
        type=int,
        default=DEFAULT_DAILY_DAYS,
        help=f"日K回補天數，預設 {DEFAULT_DAILY_DAYS}；首次全量可改 365",
    )
    parser.add_argument(
        "--intraday-days",
        type=int,
        default=DEFAULT_INTRADAY_DAYS,
        help=f"分鐘K回補天數，預設 {DEFAULT_INTRADAY_DAYS}；首次全量可改 58",
    )
    args = parser.parse_args()

    log.info("█" * 50)
    log.info(f"  台股資料倉儲更新（模式：{args.tf}{'  +紫圈' if args.purple else ''}）")
    log.info("█" * 50)

    configure_yfinance_cache()
    conn   = init_db(DB_PATH)
    stocks = get_all_stocks()
    tickers = stocks["ticker"].tolist()

    # 更新股票名稱對照表
    upsert_stocks(conn, stocks)

    if args.tf in ("1d", "all"):
        update_daily(conn, tickers, days=args.daily_days)

    if args.tf in ("intraday", "all"):
        update_intraday(conn, tickers, days=args.intraday_days)

    if args.purple:
        update_purple_signals(
            conn,
            stocks,
            lookback_days=args.purple_lookback,
            purple_tf=args.purple_tf,
        )

    conn.close()
    log.info("█" * 50)
    log.info("  ✅ 全部更新完成！")
    log.info("█" * 50)


if __name__ == "__main__":
    main()
