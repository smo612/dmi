"""
backend_api.py v3
台股全市場掃股系統 — FastAPI 後端

策略：
  dmi    : DMI 黃金交叉（支援近 N 根交叉與差值範圍）
  macd   : MACD 金叉（MACD 穿越 Signal，且金叉與當前皆在 0 軸上）
  purple : 讀取預計算紫圈報告（僅 60m / 1d）
"""

import sqlite3
import logging
from typing import Literal
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_ta as ta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# ─── 設定 ──────────────────────────────────────────────────────────────────────
DB_PATH = "stock_data.db"
FRONTEND_PATH = Path(__file__).with_name("scanner.html")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SUPPORTED_TIMEFRAMES = ("1d", "15m", "30m", "60m", "180m", "240m")
PURPLE_REPORT_TIMEFRAMES = ("1d", "60m")
LOCAL_TIMEZONE = "Asia/Taipei"


# ─── App State ────────────────────────────────────────────────────────────────
app_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("API 啟動：預載全市場 K 線資料...")
    app_state["data"] = load_all_data(DB_PATH)
    app_state["stock_names"] = load_stock_name_map(DB_PATH)
    app_state["daily_volume_map"] = build_daily_volume_map(app_state["data"].get("1d", {}))
    purple_reports, purple_scan_at = load_purple_reports(DB_PATH, app_state["stock_names"])
    app_state["purple_reports"] = purple_reports
    app_state["purple_scan_at"] = purple_scan_at
    total = sum(len(v) for v in app_state["data"].values())
    log.info(f"預載完成：{total} 檔×週期組合")
    yield
    app_state.clear()
    log.info("API 關閉")


app = FastAPI(
    title="台股全市場掃股系統 API",
    description="支援 15m / 30m / 60m / 180m / 240m / 1d 多週期掃描；紫圈讀取預計算報告",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── 指標計算工具 ──────────────────────────────────────────────────────────────

def calc_dmi_components(df: pd.DataFrame, length: int = 14):
    """回傳 (+DI array, -DI array)；失敗回傳 (None, None)"""
    result = ta.adx(
        high=df["High"], low=df["Low"], close=df["Close"],
        length=length, append=False,
    )
    if result is None or result.empty:
        return None, None
    plus_col  = next((c for c in result.columns if str(c).startswith("DMP_")), None)
    minus_col = next((c for c in result.columns if str(c).startswith("DMN_")), None)
    if plus_col is None or minus_col is None:
        return None, None
    return result[plus_col].to_numpy(), result[minus_col].to_numpy()


def calc_macd_components(df: pd.DataFrame, fast=12, slow=26, signal=9):
    """回傳 (MACD array, Signal array)；失敗回傳 (None, None)"""
    result = ta.macd(df["Close"], fast=fast, slow=slow, signal=signal, append=False)
    if result is None or result.empty:
        return None, None
    macd_col = next(
        (c for c in result.columns
         if str(c).startswith("MACD_")
         and not str(c).startswith("MACDs_")
         and not str(c).startswith("MACDh_")),
        None,
    )
    sig_col = next((c for c in result.columns if str(c).startswith("MACDs_")), None)
    if macd_col is None or sig_col is None:
        return None, None
    return result[macd_col].to_numpy(), result[sig_col].to_numpy()


def _volume_ok(volume_value: int | float | None, min_volume: int) -> bool:
    """True = 成交量達標（或不限）；目前統一以日K總量為準。"""
    if min_volume <= 0:
        return True
    if volume_value is None or pd.isna(volume_value):
        return False
    return int(volume_value) >= min_volume * 1000


def _strip_nan(a: np.ndarray, b: np.ndarray):
    """去除兩陣列的 NaN，回傳同步過濾後的 (a, b)"""
    valid = ~(np.isnan(a) | np.isnan(b))
    return a[valid], b[valid]


def _strip_nan_with_index(a: np.ndarray, b: np.ndarray):
    """
    去除兩陣列的 NaN，並保留對應回原始陣列的位置索引。
    這對需要回傳「實際觸發那根 K 棒」的日期很重要。
    """
    valid = ~(np.isnan(a) | np.isnan(b))
    idx = np.flatnonzero(valid)
    return a[valid], b[valid], idx


def _cross_in_window(series_a: np.ndarray, series_b: np.ndarray, window: int) -> bool:
    """
    取最後 window 根，檢查是否有 series_a 向上穿越 series_b 的事件。
    window=3 → 檢查最後 3 根中 2 對相鄰蠟燭。
    """
    wa = series_a[-window:]
    wb = series_b[-window:]
    return any(wa[j-1] <= wb[j-1] and wa[j] > wb[j] for j in range(1, len(wa)))


def _cross_up_indices_in_window(series_a: np.ndarray, series_b: np.ndarray, window: int) -> list[int]:
    """
    回傳最後 window 根內向上穿越發生的位置索引（以原陣列索引表示）。
    可用來額外判斷「穿越當下」是否同時滿足其他條件，例如站上 0 軸。
    """
    if len(series_a) != len(series_b) or len(series_a) < window:
        return []
    start = len(series_a) - window
    wa = series_a[start:]
    wb = series_b[start:]
    return [
        start + j
        for j in range(1, len(wa))
        if wa[j - 1] <= wb[j - 1] and wa[j] > wb[j]
    ]


def _format_trigger_time(ts: pd.Timestamp, timeframe: str) -> str:
    return ts.strftime("%Y-%m-%d %H:%M") if timeframe != "1d" else ts.strftime("%Y-%m-%d")


# ─── 1. 資料讀取層 ──────────────────────────────────────────────────────────────

def load_all_data(db_path: str) -> dict:
    result = {}
    try:
        conn = sqlite3.connect(db_path)
    except Exception as e:
        log.error(f"無法連線資料庫：{e}")
        return {}

    try:
        df_daily = pd.read_sql(
            "SELECT Ticker, Date as _dt, Open, High, Low, Close, Volume "
            "FROM daily_candles ORDER BY Ticker, Date ASC",
            conn,
        )
        df_daily["_dt"] = pd.to_datetime(df_daily["_dt"])
        result["1d"] = {tk: g.reset_index(drop=True) for tk, g in df_daily.groupby("Ticker")}
        log.info(f"日K 預載：{len(result['1d'])} 檔")
    except Exception as e:
        log.error(f"日K 讀取失敗：{e}")
        result["1d"] = {}

    try:
        df_intra = pd.read_sql(
            "SELECT Ticker, Timeframe, Datetime as _dt, Open, High, Low, Close, Volume "
            "FROM intraday_candles ORDER BY Ticker, Timeframe, Datetime ASC",
            conn,
        )
        # DB 內分鐘線時間戳目前以 UTC 字串儲存，這裡轉回台灣時間以便與 TV 對齊
        df_intra["_dt"] = pd.to_datetime(df_intra["_dt"], utc=True).dt.tz_convert(LOCAL_TIMEZONE)
        for tf, tf_group in df_intra.groupby("Timeframe"):
            result[tf] = {tk: g.reset_index(drop=True) for tk, g in tf_group.groupby("Ticker")}
            log.info(f"{tf} 預載：{len(result[tf])} 檔")
    except Exception as e:
        log.warning(f"分鐘K 讀取失敗（可能尚未更新）：{e}")
    finally:
        for tf in SUPPORTED_TIMEFRAMES:
            result.setdefault(tf, {})

    conn.close()
    return result


def load_stock_name_map(db_path: str) -> dict[str, str]:
    """讀取股票名稱對照表。"""
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql("SELECT Ticker, Name FROM stocks", conn)
        conn.close()
        if df.empty:
            return {}
        return dict(zip(df["Ticker"], df["Name"]))
    except Exception as e:
        log.warning(f"stocks 表讀取失敗：{e}")
        return {}


def load_purple_reports(db_path: str, stock_names: dict[str, str]) -> tuple[dict[str, list["StockHit"]], dict[str, str]]:
    """讀取盤後預計算紫圈報告。"""
    reports = {tf: [] for tf in PURPLE_REPORT_TIMEFRAMES}
    scan_at = {tf: "" for tf in PURPLE_REPORT_TIMEFRAMES}
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql(
            "SELECT Ticker, Timeframe, TriggerTime, Close, Volume, ScanAt "
            "FROM purple_signals ORDER BY Timeframe, TriggerTime DESC",
            conn,
        )
        conn.close()
    except Exception as e:
        log.warning(f"purple_signals 表讀取失敗：{e}")
        return reports, scan_at

    if df.empty:
        return reports, scan_at

    for tf, group in df.groupby("Timeframe"):
        if tf not in reports:
            continue
        latest_scan = group["ScanAt"].dropna()
        if not latest_scan.empty:
            scan_at[tf] = str(latest_scan.max())

        report_rows: list[StockHit] = []
        for _, row in group.iterrows():
            ticker = row["Ticker"]
            volume = int(row["Volume"]) if not pd.isna(row["Volume"]) else 0
            report_rows.append(StockHit(
                ticker=ticker,
                name=stock_names.get(ticker, ticker),
                trigger_time=str(row["TriggerTime"]),
                close=float(row["Close"]) if not pd.isna(row["Close"]) else 0.0,
                volume=volume,
                volume_lots=volume // 1000,
                signal_label="紫圈",
            ))
        reports[tf] = report_rows

    return reports, scan_at


def build_daily_volume_map(daily_data: dict[str, pd.DataFrame]) -> dict[str, int]:
    """建立 ticker -> 最新日K成交量 對照表。"""
    result: dict[str, int] = {}
    for ticker, df in daily_data.items():
        if df.empty:
            continue
        last_vol = df["Volume"].iloc[-1]
        if pd.isna(last_vol):
            continue
        result[ticker] = int(last_vol)
    return result


def count_bars_since_trigger(
    tf_data: dict[str, pd.DataFrame],
    ticker: str,
    trigger_time: str,
    timeframe: str,
) -> int | None:
    """
    計算 trigger_time 距離該 ticker 最新一根 K 棒相隔幾根。
    例如最新一根觸發 -> 0；前一根觸發 -> 1。
    """
    df = tf_data.get(ticker)
    if df is None or df.empty:
        return None

    formatted = df["_dt"].apply(lambda ts: _format_trigger_time(ts, timeframe))
    matched = formatted[formatted == trigger_time]
    if matched.empty:
        return None

    trigger_pos = int(matched.index[-1])
    return len(df) - 1 - trigger_pos


# ─── 2. 策略模組 ──────────────────────────────────────────────────────────────

def strategy_dmi(
    df: pd.DataFrame,
    window: int,
    min_volume: int,
    daily_volume: int | None,
    diff_min: float = 0,
    diff_max: float = 0,
):
    """
    DMI 黃金交叉策略。
    A. window 根內 +DI 穿越 -DI
    B. 最後一根 +DI > -DI（多頭維持）
    C. 成交量 >= min_volume 張
    D. 最後一根的 DMI 差值（+DI - -DI）在指定範圍內
    """
    if len(df) < 14 + window + 5:
        return None
    if not _volume_ok(daily_volume, min_volume):
        return None

    dp_raw, dm_raw = calc_dmi_components(df)
    if dp_raw is None:
        return None

    dp, dm, orig_idx = _strip_nan_with_index(dp_raw, dm_raw)
    if len(dp) < window + 1:
        return None

    if dp[-1] <= dm[-1]:          # B
        return None

    diff = float(dp[-1] - dm[-1])
    if diff_min > 0 and diff < diff_min:
        return None
    if diff_max > 0 and diff > diff_max:
        return None

    cross_indices = _cross_up_indices_in_window(dp, dm, window)
    if not cross_indices:
        return None

    trigger_idx = int(orig_idx[cross_indices[-1]])
    return {
        "trigger_idx": trigger_idx,
        "di_plus": round(float(dp[-1]), 2),
        "di_minus": round(float(dm[-1]), 2),
        "dmi_diff": round(diff, 2),
    }


def strategy_macd(df: pd.DataFrame, window: int, min_volume: int, daily_volume: int | None):
    """
    MACD 金叉策略。
    A. window 根內 MACD 穿越 Signal
    B. 最後一根 MACD > Signal（金叉狀態維持）
    C. 金叉發生當下與最後一根都在 0 軸之上
    D. 成交量 >= min_volume 張
    """
    if len(df) < 26 + 9 + window + 5:
        return None
    if not _volume_ok(daily_volume, min_volume):
        return None

    ma_raw, si_raw = calc_macd_components(df)
    if ma_raw is None:
        return None

    ma, si, orig_idx = _strip_nan_with_index(ma_raw, si_raw)
    if len(ma) < window + 1:
        return None

    if ma[-1] <= si[-1]:   # B
        return None
    if ma[-1] <= 0 or si[-1] <= 0:  # C（最後一根在 0 軸上）
        return None
    cross_indices = _cross_up_indices_in_window(ma, si, window)
    if not cross_indices:
        return None

    # 只接受「穿越發生當下」就已經站上 0 軸的金叉
    valid_crosses = [idx for idx in cross_indices if ma[idx] > 0 and si[idx] > 0]
    if not valid_crosses:
        return None

    return {
        "trigger_idx": int(orig_idx[valid_crosses[-1]]),
        "macd_val": round(float(ma[-1]), 4),
        "macd_sig": round(float(si[-1]), 4),
    }


# ─── 3. Schema ──────────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    strategy: Literal["dmi", "macd", "purple"] = Field(
        default="dmi", description="策略：dmi / macd / purple"
    )
    timeframe: Literal["1d", "15m", "30m", "60m", "180m", "240m"] = Field(
        default="1d", description="K 線週期"
    )
    dmi_window: int = Field(
        default=3, ge=2, le=20,
        description="幾根K棒內發生訊號（2~20）"
    )
    min_volume: int = Field(
        default=0, ge=0,
        description="最低成交量（張），0 = 不限"
    )
    dmi_diff_min: float = Field(
        default=0, ge=0, le=100,
        description="DMI：最後一根 +DI 與 -DI 的最小差值，0 = 不限"
    )
    dmi_diff_max: float = Field(
        default=0, ge=0, le=100,
        description="DMI：最後一根 +DI 與 -DI 的最大差值，0 = 不限"
    )


class StockHit(BaseModel):
    ticker:       str
    name:         str
    trigger_time: str
    close:        float
    volume:       int
    volume_lots:  int
    di_plus:      float = 0.0
    di_minus:     float = 0.0
    dmi_diff:     float = 0.0
    macd_val:     float = 0.0
    macd_sig:     float = 0.0
    signal_label: str = ""


class ScanResponse(BaseModel):
    strategy:   str
    timeframe:  str
    dmi_window: int
    min_volume: int
    dmi_diff_min: float
    dmi_diff_max: float
    total_scan: int
    total_hits: int
    scan_at:    str = ""
    results:    list[StockHit]


# ─── 4. API 端點 ────────────────────────────────────────────────────────────────

@app.get("/", summary="前端入口")
async def frontend():
    if FRONTEND_PATH.exists():
        return FileResponse(FRONTEND_PATH)
    raise HTTPException(status_code=404, detail="scanner.html not found")

@app.post("/scan", response_model=ScanResponse, summary="全市場策略掃描")
async def scan(req: ScanRequest):
    data    = app_state.get("data", {})
    tf_data = data.get(req.timeframe, {})
    stock_names = app_state.get("stock_names", {})
    daily_volume_map = app_state.get("daily_volume_map", {})

    if not data:
        raise HTTPException(status_code=503, detail="資料尚未載入，請確認資料庫")
    if req.strategy == "purple":
        if req.timeframe not in PURPLE_REPORT_TIMEFRAMES:
            raise HTTPException(status_code=400, detail="紫圈目前只支援 60m 與 1d 預計算報告")
        purple_reports = app_state.get("purple_reports", {})
        purple_scan_at = app_state.get("purple_scan_at", {})
        report_rows = purple_reports.get(req.timeframe, [])
        tf_source = data.get(req.timeframe, {})
        filtered = []
        for row in report_rows:
            if not _volume_ok(daily_volume_map.get(row.ticker), req.min_volume):
                continue
            bars_since = count_bars_since_trigger(tf_source, row.ticker, row.trigger_time, req.timeframe)
            if bars_since is None or bars_since >= req.dmi_window:
                continue
            filtered.append(row)
        normalized_rows = []
        for row in filtered:
            daily_volume = daily_volume_map.get(row.ticker, row.volume)
            normalized_rows.append(StockHit(
                ticker=row.ticker,
                name=row.name,
                trigger_time=row.trigger_time,
                close=row.close,
                volume=int(daily_volume),
                volume_lots=int(daily_volume) // 1000,
                signal_label=row.signal_label,
            ))
        return ScanResponse(
            strategy=req.strategy,
            timeframe=req.timeframe,
            dmi_window=req.dmi_window,
            min_volume=req.min_volume,
            dmi_diff_min=req.dmi_diff_min,
            dmi_diff_max=req.dmi_diff_max,
            total_scan=len(stock_names),
            total_hits=len(normalized_rows),
            scan_at=purple_scan_at.get(req.timeframe, ""),
            results=normalized_rows,
        )

    if not tf_data:
        raise HTTPException(
            status_code=404,
            detail=f"週期 [{req.timeframe}] 無資料。若為分鐘線，請先執行 python update_db.py --tf intraday",
        )

    log.info(
        "掃描：%s %s window=%s vol>=%s dmi_diff=%s~%s",
        req.strategy, req.timeframe, req.dmi_window, req.min_volume, req.dmi_diff_min, req.dmi_diff_max,
    )
    hits: list[StockHit] = []

    for ticker, df in tf_data.items():
        try:
            daily_volume = daily_volume_map.get(ticker)
            if req.strategy == "dmi":
                signal = strategy_dmi(df, req.dmi_window, req.min_volume, daily_volume, req.dmi_diff_min, req.dmi_diff_max)
            else:
                signal = strategy_macd(df, req.dmi_window, req.min_volume, daily_volume)

            if not signal:
                continue

            last         = df.iloc[-1]
            trigger_row  = df.iloc[signal["trigger_idx"]]
            trigger_dt   = df["_dt"].iloc[signal["trigger_idx"]]
            trigger_time = _format_trigger_time(trigger_dt, req.timeframe)
            volume = int(daily_volume if daily_volume is not None else last["Volume"])

            hits.append(StockHit(
                ticker=ticker,
                name=stock_names.get(ticker, ticker),
                trigger_time=trigger_time,
                close=round(float(trigger_row["Close"]), 2),
                volume=volume,
                volume_lots=volume // 1000,
                di_plus=signal.get("di_plus", 0.0),
                di_minus=signal.get("di_minus", 0.0),
                dmi_diff=signal.get("dmi_diff", 0.0),
                macd_val=signal.get("macd_val", 0.0),
                macd_sig=signal.get("macd_sig", 0.0),
                signal_label=signal.get("signal_label", ""),
            ))

        except Exception as e:
            log.warning(f"計算失敗 [{ticker}]：{e}")

    hits.sort(key=lambda x: x.trigger_time, reverse=True)
    log.info(f"掃描完成：{len(hits)}/{len(tf_data)} 命中")

    return ScanResponse(
        strategy=req.strategy,
        timeframe=req.timeframe,
        dmi_window=req.dmi_window,
        min_volume=req.min_volume,
        dmi_diff_min=req.dmi_diff_min,
        dmi_diff_max=req.dmi_diff_max,
        total_scan=len(tf_data),
        total_hits=len(hits),
        scan_at="",
        results=hits,
    )


@app.get("/reload", summary="重新載入資料庫到記憶體")
async def reload():
    app_state["data"] = load_all_data(DB_PATH)
    app_state["stock_names"] = load_stock_name_map(DB_PATH)
    app_state["daily_volume_map"] = build_daily_volume_map(app_state["data"].get("1d", {}))
    purple_reports, purple_scan_at = load_purple_reports(DB_PATH, app_state["stock_names"])
    app_state["purple_reports"] = purple_reports
    app_state["purple_scan_at"] = purple_scan_at
    total = sum(len(v) for v in app_state["data"].values())
    return {"status": "ok", "message": f"已重新載入，共 {total} 檔×週期"}


@app.get("/status", summary="系統狀態")
async def status():
    data    = app_state.get("data", {})
    purple_reports = app_state.get("purple_reports", {})
    purple_scan_at = app_state.get("purple_scan_at", {})
    summary = {}
    for tf, tf_data in data.items():
        if tf_data:
            dates = [g["_dt"].max() for g in tf_data.values() if not g.empty]
            summary[tf] = {
                "stocks": len(tf_data),
                "latest": max(dates).strftime("%Y-%m-%d %H:%M") if dates else "N/A",
            }
    purple_summary = {
        tf: {"hits": len(purple_reports.get(tf, [])), "scan_at": purple_scan_at.get(tf, "")}
        for tf in PURPLE_REPORT_TIMEFRAMES
    }
    return {"status": "ok", "timeframes": summary, "purple_reports": purple_summary}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend_api:app", host="0.0.0.0", port=8000, reload=True)
