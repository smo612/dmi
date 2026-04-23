"""
backend_api.py v3
?啗?典??湔??∠頂蝯???FastAPI 敺垢

蝑嚗?  dmi    : DMI 暺?鈭文?嚗?渲? N ?嫣漱??撌桀潛???
  macd   : MACD ??嚗ACD 蝛輯? Signal嚗????????0 頠訾?嚗?  purple : 霈??閮?蝝怠??勗?嚗? 60m / 1d嚗?"""

import logging
import sqlite3
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

# ??? 閮剖? ??????????????????????????????????????????????????????????????????????
DB_PATH = "stock_data.db"
FRONTEND_PATH = Path(__file__).with_name("scanner_cards.html")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

SUPPORTED_TIMEFRAMES = ("1d", "15m", "30m", "60m", "180m", "240m")
PURPLE_REPORT_TIMEFRAMES = ("1d", "60m")
LOCAL_TIMEZONE = "Asia/Taipei"
DMI_CROSS_CONFIRM_MIN = 1.0
DMI_INTRADAY_CONFIRM_MIN = 2.0
DMI_READY_GAP_DEFAULT = 2.0


# ??? App State ????????????????????????????????????????????????????????????????
app_state: dict = {}


def _format_local_timestamp(value) -> str:
    if value is None:
        return ""
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(LOCAL_TIMEZONE)
    else:
        ts = ts.tz_convert(LOCAL_TIMEZONE)
    return ts.strftime("%Y-%m-%d %H:%M")


def _normalize_intraday_datetimes(values: pd.Series) -> pd.Series:
    """
    DB rows may contain old Yahoo-style UTC-naive timestamps (01:00~05:30)
    and Fubon rows that were briefly written as Taipei-naive timestamps
    (09:00~13:30). Normalize both to Asia/Taipei for scanning.
    """
    raw = pd.to_datetime(values, errors="coerce")
    local_mask = raw.dt.hour >= 7

    frames = []
    if local_mask.any():
        frames.append(raw.loc[local_mask].dt.tz_localize(LOCAL_TIMEZONE))
    if (~local_mask).any():
        frames.append(raw.loc[~local_mask].dt.tz_localize("UTC").dt.tz_convert(LOCAL_TIMEZONE))
    if not frames:
        return pd.Series(pd.NaT, index=values.index, dtype=f"datetime64[ns, {LOCAL_TIMEZONE}]")
    return pd.concat(frames).sort_index()


def _filter_valid_intraday_bar_times(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    valid = df["_dt"].dt.second.eq(0)
    minutes = df["_dt"].dt.minute
    tf = df["Timeframe"]
    valid &= ~((tf == "15m") & (minutes % 15 != 0))
    valid &= ~((tf == "30m") & (minutes % 30 != 0))
    valid &= ~((tf.isin(["60m", "180m", "240m"])) & (minutes != 0))
    return df.loc[valid].copy()


def _is_live_intraday_session(now: pd.Timestamp | None = None) -> bool:
    now = now or pd.Timestamp.now(tz=LOCAL_TIMEZONE)
    if now.weekday() >= 5:
        return False
    hhmm = (now.hour, now.minute)
    return (9, 0) <= hhmm < (13, 35)


def _get_db_updated_at(db_path: str) -> str:
    try:
        return _format_local_timestamp(Path(db_path).stat().st_mtime)
    except Exception:
        return ""


def refresh_app_state() -> int:
    app_state["data"] = load_all_data(DB_PATH)
    app_state["stock_names"] = load_stock_name_map(DB_PATH)
    app_state["daily_volume_map"] = build_daily_volume_map(app_state["data"].get("1d", {}))
    app_state["daily_turnover_map"] = build_daily_turnover_map(app_state["data"].get("1d", {}))
    purple_reports, purple_scan_at = load_purple_reports(DB_PATH, app_state["stock_names"])
    app_state["purple_reports"] = purple_reports
    app_state["purple_scan_at"] = purple_scan_at
    app_state["db_updated_at"] = _get_db_updated_at(DB_PATH)
    app_state["api_loaded_at"] = _format_local_timestamp(pd.Timestamp.now(tz=LOCAL_TIMEZONE))
    return sum(len(v) for v in app_state["data"].values())


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("API startup: preloading market data...")
    total = refresh_app_state()
    log.info(f"API preload done: {total} symbols loaded")
    yield
    app_state.clear()
    log.info("API shutdown")


app = FastAPI(
    title="?啗?典??湔??∠頂蝯?API",
    description="?舀 15m / 30m / 60m / 180m / 240m / 1d 憭望???嚗換????閮??勗?",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ??? ??閮?撌亙 ??????????????????????????????????????????????????????????????

def calc_dmi_full_components(df: pd.DataFrame, length: int = 14):
    """Return +DI / -DI / ADX / ADXR arrays, or None tuple on failure."""
    result = ta.adx(
        high=df["High"], low=df["Low"], close=df["Close"],
        length=length, append=False,
    )
    if result is None or result.empty:
        return None, None, None, None
    adx_col = next((c for c in result.columns if str(c).startswith("ADX_")), None)
    plus_col = next((c for c in result.columns if str(c).startswith("DMP_")), None)
    minus_col = next((c for c in result.columns if str(c).startswith("DMN_")), None)
    if adx_col is None or plus_col is None or minus_col is None:
        return None, None, None, None

    adx = result[adx_col]
    adxr = (adx + adx.shift(length)) / 2.0
    return (
        result[plus_col].to_numpy(),
        result[minus_col].to_numpy(),
        adx.to_numpy(),
        adxr.to_numpy(),
    )


def calc_dmi_components(df: pd.DataFrame, length: int = 14):
    """? (+DI array, -DI array)嚗仃????(None, None)"""
    dp, dm, _, _ = calc_dmi_full_components(df, length=length)
    return dp, dm


def calc_macd_components(df: pd.DataFrame, fast=12, slow=26, signal=9):
    """? (MACD array, Signal array)嚗仃????(None, None)"""
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
    """True = ?漱??璅?????嚗?絞銝隞交K蝮賡??箸???"""
    if min_volume <= 0:
        return True
    if volume_value is None or pd.isna(volume_value):
        return False
    return int(volume_value) >= min_volume * 1000


def _turnover_ok(turnover_value: float | None, min_turnover: float) -> bool:
    """True = ?漱?潮?璅??桐?嚗嚗??桀?蝯曹?隞交??唳K?漱?潛皞?"""
    if min_turnover <= 0:
        return True
    if turnover_value is None or pd.isna(turnover_value):
        return False
    return float(turnover_value) >= min_turnover * 10000


def _strip_nan(a: np.ndarray, b: np.ndarray):
    """?駁?拚?? NaN嚗??喳?甇仿?瞈曉???(a, b)"""
    valid = ~(np.isnan(a) | np.isnan(b))
    return a[valid], b[valid]


def _strip_nan_with_index(a: np.ndarray, b: np.ndarray):
    """
    ?駁?拚?? NaN嚗蒂靽?撠???憪??雿蔭蝝Ｗ???    ???閬??喋祕?孛?潮??K 璉??交?敺?閬?    """
    valid = ~(np.isnan(a) | np.isnan(b))
    idx = np.flatnonzero(valid)
    return a[valid], b[valid], idx


def _trim_intraday_placeholder_tail(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop trailing intraday placeholder bars such as Yahoo's final flat bar
    where OHLC are identical and volume is zero. These rows can flip the
    latest DMI/MACD reading without representing a tradable completed bar.
    """
    if df is None or df.empty or "_dt" not in df.columns:
        return df

    trimmed = df
    while len(trimmed) > 1:
        last = trimmed.iloc[-1]
        volume = last.get("Volume")
        open_ = last.get("Open")
        high = last.get("High")
        low = last.get("Low")
        close = last.get("Close")

        if any(pd.isna(v) for v in (volume, open_, high, low, close)):
            break

        is_zero_volume = int(volume) == 0
        is_flat_bar = float(open_) == float(high) == float(low) == float(close)
        if not (is_zero_volume and is_flat_bar):
            break

        trimmed = trimmed.iloc[:-1]

    return trimmed


def _trim_close_auction_tail(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """
    Fubon's final close-auction bar for 15m/30m often arrives as a single-price
    print (OHLC identical). It is useful for quotes but tends to distort DMI.
    Exclude it from strategy evaluation while keeping it in the DB.
    """
    if df is None or df.empty or "_dt" not in df.columns or timeframe not in {"30m", "60m", "180m", "240m"}:
        return df
    if len(df) <= 1:
        return df

    last = df.iloc[-1]
    ts = pd.Timestamp(last["_dt"])
    if ts.tzinfo is None:
        ts = ts.tz_localize(LOCAL_TIMEZONE)
    else:
        ts = ts.tz_convert(LOCAL_TIMEZONE)

    open_ = last.get("Open")
    high = last.get("High")
    low = last.get("Low")
    close = last.get("Close")
    if any(pd.isna(v) for v in (open_, high, low, close)):
        return df

    is_flat_bar = float(open_) == float(high) == float(low) == float(close)
    is_close_auction_slot = (ts.hour, ts.minute) == (13, 30)
    if is_flat_bar and is_close_auction_slot:
        return df.iloc[:-1].copy()
    return df


def _scan_ready_intraday_frame(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """
    Keep provisional intraday bars in DB for live views, but exclude the
    newest bar from strategy evaluation only during live trading. After
    close, the final 13:30 bar should participate in scans.
    """
    trimmed = _trim_intraday_placeholder_tail(df)
    trimmed = _trim_close_auction_tail(trimmed, timeframe)
    if timeframe == "1d" or trimmed is None or trimmed.empty:
        return trimmed
    latest_dt = pd.Timestamp(trimmed["_dt"].iloc[-1])
    if latest_dt.tzinfo is None:
        latest_dt = latest_dt.tz_localize(LOCAL_TIMEZONE)
    else:
        latest_dt = latest_dt.tz_convert(LOCAL_TIMEZONE)
    now_tw = pd.Timestamp.now(tz=LOCAL_TIMEZONE)
    if latest_dt.date() != now_tw.date() or not _is_live_intraday_session(now_tw):
        return trimmed
    if len(trimmed) <= 1:
        return trimmed.iloc[0:0].copy()
    return trimmed.iloc[:-1].copy()


def _cross_in_window(series_a: np.ndarray, series_b: np.ndarray, window: int) -> bool:
    """
    ??敺?window ?對?瑼Ｘ?臬??series_a ??蝛輯? series_b ??隞嗚?    window=3 ??瑼Ｘ?敺?3 ?嫣葉 2 撠?啗??准?    """
    wa = series_a[-window:]
    wb = series_b[-window:]
    return any(wa[j-1] <= wb[j-1] and wa[j] > wb[j] for j in range(1, len(wa)))


def _cross_up_indices_in_window(series_a: np.ndarray, series_b: np.ndarray, window: int) -> list[int]:
    """
    ??敺?window ?孵??蝛輯??潛???蝵桃揣撘?隞亙????蝝Ｗ?銵函內嚗?    ?舐靘?憭?瑯忽頞銝?血??遛頞喳隞?隞塚?靘?蝡? 0 頠詻?    """
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


def _latest_cross_up(series_a: np.ndarray, series_b: np.ndarray) -> bool:
    if len(series_a) < 2 or len(series_b) < 2:
        return False
    return series_a[-2] <= series_b[-2] and series_a[-1] > series_b[-1]


def _format_trigger_time(ts: pd.Timestamp, timeframe: str) -> str:
    return ts.strftime("%Y-%m-%d %H:%M") if timeframe != "1d" else ts.strftime("%Y-%m-%d")


# ??? 1. 鞈?霈?惜 ??????????????????????????????????????????????????????????????

def load_all_data(db_path: str) -> dict:
    result = {}
    try:
        conn = sqlite3.connect(db_path)
    except Exception as e:
        log.error(f"?⊥????鞈?摨恬?{e}")
        return {}

    try:
        df_daily = pd.read_sql(
            "SELECT Ticker, Date as _dt, Open, High, Low, Close, Volume "
            "FROM daily_candles ORDER BY Ticker, Date ASC",
            conn,
        )
        df_daily["_dt"] = pd.to_datetime(df_daily["_dt"])
        result["1d"] = {tk: g.reset_index(drop=True) for tk, g in df_daily.groupby("Ticker")}
        log.info(f"daily preload: {len(result['1d'])} tickers")
    except Exception as e:
        log.error(f"?仕 霈?仃??{e}")
        result["1d"] = {}

    try:
        df_intra = pd.read_sql(
            "SELECT Ticker, Timeframe, Datetime as _dt, Open, High, Low, Close, Volume "
            "FROM intraday_candles ORDER BY Ticker, Timeframe, Datetime ASC",
            conn,
        )
        # DB ?批??????喟?誑 UTC 摮葡?脣?嚗ㄐ頧??啁??隞乩噶??TV 撠?
        df_intra["_dt"] = _normalize_intraday_datetimes(df_intra["_dt"])
        df_intra = df_intra.dropna(subset=["_dt"])
        df_intra = (
            df_intra.sort_values(["Ticker", "Timeframe", "_dt"])
            .drop_duplicates(subset=["Ticker", "Timeframe", "_dt"], keep="last")
        )
        for tf, tf_group in df_intra.groupby("Timeframe"):
            result[tf] = {tk: g.reset_index(drop=True) for tk, g in tf_group.groupby("Ticker")}
            log.info(f"{tf} preload: {len(result[tf])} tickers")
    except Exception as e:
        log.warning(f"??K 霈?仃???航撠?湔嚗?{e}")
    finally:
        for tf in SUPPORTED_TIMEFRAMES:
            result.setdefault(tf, {})

    conn.close()
    return result


def load_stock_name_map(db_path: str) -> dict[str, str]:
    """霈?蟡典?蝔勗??扯”??"""
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql("SELECT Ticker, Name FROM stocks", conn)
        conn.close()
        if df.empty:
            return {}
        return dict(zip(df["Ticker"], df["Name"]))
    except Exception as e:
        log.warning(f"stocks 銵刻??仃??{e}")
        return {}


def load_purple_reports(db_path: str, stock_names: dict[str, str]) -> tuple[dict[str, list["StockHit"]], dict[str, str]]:
    """霈?敺?閮?蝝怠??勗???"""
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
        log.warning(f"purple_signals 銵刻??仃??{e}")
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
                signal_label="蝝怠?",
            ))
        reports[tf] = report_rows

    return reports, scan_at


def build_daily_volume_map(daily_data: dict[str, pd.DataFrame]) -> dict[str, int]:
    """撱箇? ticker -> ??唳K?漱??撠銵具?"""
    result: dict[str, int] = {}
    for ticker, df in daily_data.items():
        if df.empty:
            continue
        last_vol = df["Volume"].iloc[-1]
        if pd.isna(last_vol):
            continue
        result[ticker] = int(last_vol)
    return result


def build_daily_turnover_map(daily_data: dict[str, pd.DataFrame]) -> dict[str, float]:
    """撱箇? ticker -> ??唳K?漱?潘?close * volume嚗??扯”??"""
    result: dict[str, float] = {}
    for ticker, df in daily_data.items():
        if df.empty:
            continue
        last = df.iloc[-1]
        if pd.isna(last.get("Volume")) or pd.isna(last.get("Close")):
            continue
        result[ticker] = float(last["Close"]) * float(last["Volume"])
    return result


def count_bars_since_trigger(
    tf_data: dict[str, pd.DataFrame],
    ticker: str,
    trigger_time: str,
    timeframe: str,
) -> int | None:
    """
    閮? trigger_time 頝閰?ticker ??唬???K 璉?嗾?嫘?    靘???唬??寡孛??-> 0嚗?銝?寡孛??-> 1??    """
    df = tf_data.get(ticker)
    if df is None or df.empty:
        return None

    formatted = df["_dt"].apply(lambda ts: _format_trigger_time(ts, timeframe))
    matched = formatted[formatted == trigger_time]
    if matched.empty:
        return None

    trigger_pos = int(matched.index[-1])
    return len(df) - 1 - trigger_pos


def count_days_since_trigger(
    tf_data: dict[str, pd.DataFrame],
    ticker: str,
    trigger_time: str,
    timeframe: str,
) -> int | None:
    """
    Return the number of calendar days between the latest bar and trigger bar.
    Purple reports use this so they can filter by recent days instead of
    reusing the generic DMI/MACD bar-window parameter.
    """
    df = tf_data.get(ticker)
    if df is None or df.empty:
        return None

    formatted = df["_dt"].apply(lambda ts: _format_trigger_time(ts, timeframe))
    matched = formatted[formatted == trigger_time]
    if matched.empty:
        return None

    trigger_dt = pd.Timestamp(df["_dt"].iloc[int(matched.index[-1])])
    latest_dt = pd.Timestamp(df["_dt"].iloc[-1])
    return max((latest_dt.normalize() - trigger_dt.normalize()).days, 0)


# ??? 2. 蝑璅∠? ??????????????????????????????????????????????????????????????

def strategy_dmi(
    df: pd.DataFrame,
    timeframe: str,
    window: int,
    min_volume: int,
    daily_volume: int | None,
    diff_min: float = 0,
    diff_max: float = 0,
):
    """
    DMI 暺?鈭文?蝑??    A. window ?孵 +DI 蝛輯? -DI
    B. ?敺???+DI > -DI嚗??剔雁??
    C. ?漱??>= min_volume 撘?    D. ?敺??寧? DMI 撌桀潘?+DI - -DI嚗??蝭???    """
    df = _scan_ready_intraday_frame(df, timeframe)
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
    prev_diff = float(dp[-2] - dm[-2])
    dp_delta = float(dp[-1] - dp[-2])
    dm_delta = float(dm[-1] - dm[-2])
    intraday_floor = DMI_INTRADAY_CONFIRM_MIN if timeframe != "1d" else DMI_CROSS_CONFIRM_MIN
    confirm_min = max(float(diff_min), DMI_CROSS_CONFIRM_MIN, intraday_floor)
    if diff < confirm_min:
        return None
    if diff_max > 0 and diff > diff_max:
        return None

    cross_indices = _cross_up_indices_in_window(dp, dm, window)
    if not cross_indices:
        return None
    if not (_latest_cross_up(dp, dm) or (dp_delta >= 0 and dm_delta <= 0 and diff >= prev_diff)):
        return None

    trigger_idx = int(orig_idx[cross_indices[-1]])
    return {
        "trigger_idx": trigger_idx,
        "di_plus": round(float(dp[-1]), 2),
        "di_minus": round(float(dm[-1]), 2),
        "dmi_diff": round(diff, 2),
        "dmi_mode": "cross",
        "signal_label": "確認金叉",
    }


def strategy_dmi_ready(
    df: pd.DataFrame,
    timeframe: str,
    window: int,
    min_volume: int,
    daily_volume: int | None,
    diff_max: float = 0,
):
    """
    Ready-to-cross mode:
    - +DI is still below -DI
    - the gap is small and shrinking
    - no completed bullish cross has happened inside the current window yet
    """
    df = _scan_ready_intraday_frame(df, timeframe)
    if len(df) < 14 + window + 5:
        return None
    if not _volume_ok(daily_volume, min_volume):
        return None

    dp_raw, dm_raw = calc_dmi_components(df)
    if dp_raw is None:
        return None

    dp, dm, orig_idx = _strip_nan_with_index(dp_raw, dm_raw)
    if len(dp) < max(window + 1, 3):
        return None

    if dp[-1] >= dm[-1]:
        return None
    if _cross_up_indices_in_window(dp, dm, window):
        return None

    gap_now = float(dm[-1] - dp[-1])
    gap_prev = float(dm[-2] - dp[-2])
    dp_delta = float(dp[-1] - dp[-2])
    dm_delta = float(dm[-1] - dm[-2])
    gap_limit = float(diff_max) if diff_max > 0 else DMI_READY_GAP_DEFAULT
    recent_gaps = (dm[-window:] - dp[-window:]).astype(float)

    if gap_now <= 0 or gap_now > gap_limit:
        return None
    if dp_delta <= 0:
        return None
    if dm_delta >= 0:
        return None
    if gap_now >= gap_prev:
        return None
    if recent_gaps[-1] != np.min(recent_gaps):
        return None

    return {
        "trigger_idx": int(orig_idx[-1]),
        "di_plus": round(float(dp[-1]), 2),
        "di_minus": round(float(dm[-1]), 2),
        "dmi_diff": round(float(dp[-1] - dm[-1]), 2),
        "dmi_mode": "ready",
        "signal_label": "準備金叉",
    }


def strategy_dmi_tangle(
    df: pd.DataFrame,
    timeframe: str,
    min_volume: int,
    daily_volume: int | None,
    spread_max: float = 1.5,
    mean_min: float = 10.0,
    mean_max: float = 25.0,
):
    """DMI ?函鳥蝯???隞僑隞乩???????鳥蝯???"""
    df = _scan_ready_intraday_frame(df, timeframe)
    if len(df) < 40:
        return None
    if not _volume_ok(daily_volume, min_volume):
        return None

    dp_raw, dm_raw, adx_raw, adxr_raw = calc_dmi_full_components(df)
    if dp_raw is None:
        return None

    dmi_df = pd.DataFrame({
        "dp": dp_raw,
        "dm": dm_raw,
        "adx": adx_raw,
        "adxr": adxr_raw,
    })
    valid = dmi_df.notna().all(axis=1)
    if not valid.any():
        return None

    clean = dmi_df.loc[valid].reset_index(drop=True)
    orig_idx = np.flatnonzero(valid.to_numpy())
    dt_series = df["_dt"].iloc[orig_idx].reset_index(drop=True)
    start_of_year = pd.Timestamp(year=pd.Timestamp.now(tz=LOCAL_TIMEZONE).year, month=1, day=1)

    spread = clean.max(axis=1) - clean.min(axis=1)
    mean_val = clean.mean(axis=1)
    match = (
        (dt_series >= start_of_year)
        & (spread <= float(spread_max))
        & (mean_val >= float(mean_min))
        & (mean_val <= float(mean_max))
    )
    if not match.any():
        return None

    pos = int(np.flatnonzero(match.to_numpy())[-1])
    trigger_idx = int(orig_idx[pos])
    return {
        "trigger_idx": trigger_idx,
        "di_plus": round(float(clean["dp"].iloc[pos]), 2),
        "di_minus": round(float(clean["dm"].iloc[pos]), 2),
        "adx": round(float(clean["adx"].iloc[pos]), 2),
        "adxr": round(float(clean["adxr"].iloc[pos]), 2),
        "dmi_diff": round(float(clean["dp"].iloc[pos] - clean["dm"].iloc[pos]), 2),
        "dmi_spread": round(float(spread.iloc[pos]), 2),
        "dmi_mean": round(float(mean_val.iloc[pos]), 2),
        "dmi_mode": "tangle",
    }


def strategy_macd(
    df: pd.DataFrame,
    timeframe: str,
    window: int,
    min_volume: int,
    daily_volume: int | None,
):
    """
    MACD ??蝑??    A. window ?孵 MACD 蝛輯? Signal
    B. ?敺???MACD > Signal嚗????雁??
    C. ???潛??嗡???敺??寥??0 頠訾?銝?    D. ?漱??>= min_volume 撘?    """
    df = _scan_ready_intraday_frame(df, timeframe)
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
    if ma[-1] <= 0 or si[-1] <= 0:  # C
        return None
    cross_indices = _cross_up_indices_in_window(ma, si, window)
    if not cross_indices:
        return None

    # ?芣?忽頞?銝停撌脩?蝡? 0 頠貊???
    valid_crosses = [idx for idx in cross_indices if ma[idx] > 0 and si[idx] > 0]
    if not valid_crosses:
        return None

    return {
        "trigger_idx": int(orig_idx[valid_crosses[-1]]),
        "macd_val": round(float(ma[-1]), 4),
        "macd_sig": round(float(si[-1]), 4),
    }


# ??? 3. Schema ??????????????????????????????????????????????????????????????????



# ??? 4. API 蝡舫? ????????????????????????????????????????????????????????????????

class ScanRequest(BaseModel):
    strategy: Literal["dmi", "macd", "purple"] = Field(default="dmi")
    timeframe: Literal["1d", "15m", "30m", "60m", "180m", "240m"] = Field(default="1d")
    dmi_window: int = Field(default=3, ge=2, le=20)
    purple_days: int = Field(default=7, ge=1, le=365)
    purple_start_date: str = Field(default="")
    dmi_mode: Literal["cross", "ready", "tangle"] = Field(default="cross")
    min_volume: int = Field(default=0, ge=0)
    min_turnover: float = Field(default=0, ge=0)
    dmi_diff_min: float = Field(default=0, ge=0, le=100)
    dmi_diff_max: float = Field(default=0, ge=0, le=100)
    dmi_tangle_spread: float = Field(default=1.5, ge=0.1, le=20)
    dmi_tangle_mean_min: float = Field(default=10, ge=0, le=100)
    dmi_tangle_mean_max: float = Field(default=25, ge=0, le=100)


class StockHit(BaseModel):
    ticker: str
    name: str
    trigger_time: str
    close: float
    volume: int
    volume_lots: int
    turnover: float = 0.0
    di_plus: float = 0.0
    di_minus: float = 0.0
    adx: float = 0.0
    adxr: float = 0.0
    dmi_diff: float = 0.0
    dmi_spread: float = 0.0
    dmi_mean: float = 0.0
    dmi_mode: str = "cross"
    macd_val: float = 0.0
    macd_sig: float = 0.0
    signal_label: str = ""
    snapshot_basis: str = "bar"


class ScanResponse(BaseModel):
    strategy: str
    timeframe: str
    dmi_window: int
    purple_days: int = 7
    purple_start_date: str = ""
    dmi_mode: str = "cross"
    min_volume: int
    min_turnover: float
    dmi_diff_min: float
    dmi_diff_max: float
    dmi_tangle_spread: float = 1.5
    dmi_tangle_mean_min: float = 10.0
    dmi_tangle_mean_max: float = 25.0
    total_scan: int
    total_hits: int
    scan_at: str = ""
    results: list[StockHit]


def _resolve_scan_bar_metrics(
    timeframe: str,
    trigger_row: pd.Series,
    daily_volume: int | None,
    daily_turnover: float | None,
) -> tuple[int, int, float]:
    if timeframe == "1d":
        volume = int(daily_volume if daily_volume is not None else trigger_row.get("Volume", 0) or 0)
        volume_lots = volume // 1000
        if daily_turnover is not None:
            turnover = float(daily_turnover)
        else:
            close = float(trigger_row.get("Close", 0) or 0)
            turnover = close * volume
        return volume, volume_lots, turnover

    bar_volume = trigger_row.get("Volume", 0)
    close = trigger_row.get("Close", 0)
    volume_lots = int(bar_volume) if pd.notna(bar_volume) else 0
    volume = volume_lots * 1000
    turnover = float(close) * volume if pd.notna(close) else 0.0
    return volume, volume_lots, turnover


def _resolve_eod_intraday_snapshot(
    intraday_15m_df: pd.DataFrame | None,
    trigger_dt: pd.Timestamp,
) -> tuple[float, int, int, float, str] | None:
    if intraday_15m_df is None or intraday_15m_df.empty or _is_live_intraday_session():
        return None

    trigger_local = pd.Timestamp(trigger_dt)
    if trigger_local.tzinfo is None:
        trigger_local = trigger_local.tz_localize(LOCAL_TIMEZONE)
    else:
        trigger_local = trigger_local.tz_convert(LOCAL_TIMEZONE)

    same_day = intraday_15m_df.loc[intraday_15m_df["_dt"].dt.date == trigger_local.date()].copy()
    if same_day.empty:
        return None

    latest_bar = same_day.iloc[-1]
    latest_dt = pd.Timestamp(latest_bar["_dt"])
    if latest_dt.tzinfo is None:
        latest_dt = latest_dt.tz_localize(LOCAL_TIMEZONE)
    else:
        latest_dt = latest_dt.tz_convert(LOCAL_TIMEZONE)

    if (latest_dt.hour, latest_dt.minute) != (13, 30):
        return None

    close = round(float(latest_bar.get("Close", 0) or 0), 2)
    volume_lots = int(same_day["Volume"].fillna(0).sum())
    volume = volume_lots * 1000
    turnover = close * volume
    return close, volume, volume_lots, turnover, "daily"


def _resolve_display_snapshot(
    timeframe: str,
    trigger_dt: pd.Timestamp,
    trigger_row: pd.Series,
    daily_df: pd.DataFrame | None,
    intraday_15m_df: pd.DataFrame | None,
    daily_volume: int | None,
    daily_turnover: float | None,
) -> tuple[float, int, int, float, str]:
    if timeframe != "1d":
        intraday_snapshot = _resolve_eod_intraday_snapshot(intraday_15m_df, trigger_dt)
        if intraday_snapshot is not None:
            return intraday_snapshot

    if timeframe != "1d" and daily_df is not None and not daily_df.empty and not _is_live_intraday_session():
        daily_last = daily_df.iloc[-1]
        close = round(float(daily_last.get("Close", trigger_row.get("Close", 0)) or 0), 2)
        volume = int(daily_volume if daily_volume is not None else daily_last.get("Volume", 0) or 0)
        volume_lots = volume // 1000
        turnover = float(daily_turnover if daily_turnover is not None else close * volume)
        return close, volume, volume_lots, turnover, "daily"

    volume, volume_lots, turnover = _resolve_scan_bar_metrics(
        timeframe,
        trigger_row,
        daily_volume,
        daily_turnover,
    )
    close = round(float(trigger_row["Close"]), 2)
    return close, volume, volume_lots, turnover, "bar"


@app.get("/", summary="frontend")
async def frontend():
    if FRONTEND_PATH.exists():
        return FileResponse(FRONTEND_PATH)
    raise HTTPException(status_code=404, detail=f"{FRONTEND_PATH.name} not found")

@app.post("/scan", response_model=ScanResponse, summary="scan")
async def scan(req: ScanRequest):
    data    = app_state.get("data", {})
    tf_data = data.get(req.timeframe, {})
    daily_tf_data = data.get("1d", {})
    stock_names = app_state.get("stock_names", {})
    daily_volume_map = app_state.get("daily_volume_map", {})
    daily_turnover_map = app_state.get("daily_turnover_map", {})

    if not data:
        raise HTTPException(status_code=503, detail="data not loaded")
    if req.strategy == "purple":
        if req.timeframe not in PURPLE_REPORT_TIMEFRAMES:
            raise HTTPException(status_code=400, detail="purple only supports 60m or 1d")
        start_date = None
        if req.purple_start_date:
            try:
                start_date = pd.Timestamp(req.purple_start_date).normalize()
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"invalid purple_start_date: {e}")
        purple_reports = app_state.get("purple_reports", {})
        purple_scan_at = app_state.get("purple_scan_at", {})
        report_rows = purple_reports.get(req.timeframe, [])
        tf_source = data.get(req.timeframe, {})
        filtered = []
        for row in report_rows:
            if not _volume_ok(daily_volume_map.get(row.ticker), req.min_volume):
                continue
            if not _turnover_ok(daily_turnover_map.get(row.ticker), req.min_turnover):
                continue
            if start_date is not None:
                try:
                    trigger_date = pd.Timestamp(row.trigger_time).normalize()
                except Exception:
                    continue
                if trigger_date < start_date:
                    continue
            else:
                days_since = count_days_since_trigger(tf_source, row.ticker, row.trigger_time, req.timeframe)
                if days_since is None or days_since >= req.purple_days:
                    continue
            filtered.append(row)
        normalized_rows = []
        for row in filtered:
            daily_volume = daily_volume_map.get(row.ticker, row.volume)
            daily_turnover = daily_turnover_map.get(row.ticker, float(row.close) * int(daily_volume))
            normalized_rows.append(StockHit(
                ticker=row.ticker,
                name=row.name,
                trigger_time=row.trigger_time,
                close=row.close,
                volume=int(daily_volume),
                volume_lots=int(daily_volume) // 1000,
                turnover=float(daily_turnover),
                signal_label=row.signal_label,
            ))
        return ScanResponse(
            strategy=req.strategy,
            timeframe=req.timeframe,
            dmi_window=req.dmi_window,
            purple_days=req.purple_days,
            purple_start_date=req.purple_start_date,
            dmi_mode=req.dmi_mode,
            min_volume=req.min_volume,
            min_turnover=req.min_turnover,
            dmi_diff_min=req.dmi_diff_min,
            dmi_diff_max=req.dmi_diff_max,
            dmi_tangle_spread=req.dmi_tangle_spread,
            dmi_tangle_mean_min=req.dmi_tangle_mean_min,
            dmi_tangle_mean_max=req.dmi_tangle_mean_max,
            total_scan=len(stock_names),
            total_hits=len(normalized_rows),
            scan_at=purple_scan_at.get(req.timeframe, ""),
            results=normalized_rows,
        )

    if not tf_data:
        raise HTTPException(
            status_code=404,
            detail=f"?望? [{req.timeframe}] ?∟???箏???嚗??銵?python update_db.py --tf intraday",
        )

    if req.strategy == "dmi" and req.dmi_mode == "tangle" and req.timeframe != "1d":
        raise HTTPException(status_code=400, detail="DMI ?函鳥蝯芋撘?? 1d 皜祈岫")
    if req.dmi_tangle_mean_max < req.dmi_tangle_mean_min:
        raise HTTPException(status_code=400, detail="DMI tangle mean range is invalid")

    log.info(
        "??嚗?s %s window=%s vol>=%s turnover>=%s??dmi_diff=%s~%s",
        req.strategy, req.timeframe, req.dmi_window, req.min_volume, req.min_turnover, req.dmi_diff_min, req.dmi_diff_max,
    )
    hits: list[StockHit] = []

    for ticker, df in tf_data.items():
        try:
            daily_volume = daily_volume_map.get(ticker)
            daily_turnover = daily_turnover_map.get(ticker)
            daily_df = daily_tf_data.get(ticker)
            intraday_15m_df = data.get("15m", {}).get(ticker)
            if req.strategy == "dmi":
                if req.dmi_mode == "tangle":
                    signal = strategy_dmi_tangle(
                        df,
                        req.timeframe,
                        req.min_volume,
                        daily_volume,
                        req.dmi_tangle_spread,
                        req.dmi_tangle_mean_min,
                        req.dmi_tangle_mean_max,
                    )
                elif req.dmi_mode == "ready":
                    signal = strategy_dmi_ready(
                        df,
                        req.timeframe,
                        req.dmi_window,
                        req.min_volume,
                        daily_volume,
                        req.dmi_diff_max,
                    )
                else:
                    signal = strategy_dmi(
                        df,
                        req.timeframe,
                        req.dmi_window,
                        req.min_volume,
                        daily_volume,
                        req.dmi_diff_min,
                        req.dmi_diff_max,
                    )
            else:
                signal = strategy_macd(df, req.timeframe, req.dmi_window, req.min_volume, daily_volume)

            if not signal:
                continue

            scan_df      = _scan_ready_intraday_frame(df, req.timeframe)
            if scan_df.empty:
                continue
            trigger_row  = scan_df.iloc[signal["trigger_idx"]]
            trigger_dt   = scan_df["_dt"].iloc[signal["trigger_idx"]]
            trigger_time = _format_trigger_time(trigger_dt, req.timeframe)
            close, volume, volume_lots, turnover, snapshot_basis = _resolve_display_snapshot(
                req.timeframe,
                trigger_dt,
                trigger_row,
                daily_df,
                intraday_15m_df,
                daily_volume,
                daily_turnover,
            )
            if not _turnover_ok(turnover, req.min_turnover):
                continue

            hits.append(StockHit(
                ticker=ticker,
                name=stock_names.get(ticker, ticker),
                trigger_time=trigger_time,
                close=close,
                volume=volume,
                volume_lots=volume_lots,
                turnover=turnover,
                di_plus=signal.get("di_plus", 0.0),
                di_minus=signal.get("di_minus", 0.0),
                adx=signal.get("adx", 0.0),
                adxr=signal.get("adxr", 0.0),
                dmi_diff=signal.get("dmi_diff", 0.0),
                dmi_spread=signal.get("dmi_spread", 0.0),
                dmi_mean=signal.get("dmi_mean", 0.0),
                dmi_mode=signal.get("dmi_mode", req.dmi_mode),
                macd_val=signal.get("macd_val", 0.0),
                macd_sig=signal.get("macd_sig", 0.0),
                signal_label=signal.get("signal_label", ""),
                snapshot_basis=snapshot_basis,
            ))

        except Exception as e:
            log.warning(f"scan warning [{ticker}]: {e}")

    hits.sort(key=lambda x: x.trigger_time, reverse=True)
    log.info(f"scan hits: {len(hits)}/{len(tf_data)}")

    return ScanResponse(
        strategy=req.strategy,
        timeframe=req.timeframe,
        dmi_window=req.dmi_window,
        purple_days=req.purple_days,
        purple_start_date=req.purple_start_date,
        dmi_mode=req.dmi_mode,
        min_volume=req.min_volume,
        min_turnover=req.min_turnover,
        dmi_diff_min=req.dmi_diff_min,
        dmi_diff_max=req.dmi_diff_max,
        dmi_tangle_spread=req.dmi_tangle_spread,
        dmi_tangle_mean_min=req.dmi_tangle_mean_min,
        dmi_tangle_mean_max=req.dmi_tangle_mean_max,
        total_scan=len(tf_data),
        total_hits=len(hits),
        scan_at="",
        results=hits,
    )


@app.get("/reload", summary="reload")
async def reload():
    total = refresh_app_state()
    return {"status": "ok", "message": f"撌脤??啗??伐???{total} 瑼望?"}


@app.get("/status", summary="status")
async def status():
    data    = app_state.get("data", {})
    purple_reports = app_state.get("purple_reports", {})
    purple_scan_at = app_state.get("purple_scan_at", {})
    db_updated_at = app_state.get("db_updated_at", "")
    api_loaded_at = app_state.get("api_loaded_at", "")
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
    return {
        "status": "ok",
        "timeframes": summary,
        "purple_reports": purple_summary,
        "db_updated_at": db_updated_at,
        "api_loaded_at": api_loaded_at,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend_api:app", host="0.0.0.0", port=8000, reload=True)

