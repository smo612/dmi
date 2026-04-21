import argparse
import json
import logging
import os
import random
import time
from datetime import datetime, time as dt_time
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from update_db import (
    DB_PATH,
    configure_yfinance_cache,
    download_intraday_batch,
    download_intraday_single,
    get_all_stocks,
    init_db,
    resample_from_60m,
    update_daily,
    update_intraday,
    update_purple_signals,
    upsert_intraday,
    upsert_stocks,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("market_watcher.log", encoding="utf-8"),
    ],
    force=True,
)
log = logging.getLogger("market_watcher")

WATCH_INTERVAL = "30m"
WATCH_SIGNAL_INTERVAL = "15m"
INTRADAY_SENTINEL_READY_RATIO = 0.60
FULL_FINAL_INTERVALS = ("15m", "30m", "60m", "1d")
FINAL_BAR_LABELS = {
    "15m": "13:15:00",
    "30m": "13:00:00",
    "60m": "13:00:00",
    "1d": "09:00:00",
}
SENTINEL_SYMBOLS = [
    "2330.TW", "2317.TW", "2454.TW", "2308.TW",
    "2412.TW", "2881.TW", "2891.TW",
    "1301.TW", "1303.TW", "2002.TW",
    "2603.TW", "2615.TW",
    "0050.TW", "006208.TW",
]
SENTINEL_QUORUM = 0.85
SAMPLE_SIZE = 120
SAMPLE_READY_RATIO_INTRA = 0.80
SAMPLE_READY_RATIO_EOD = 0.90
READY_MAX_WAIT_SECONDS = 15 * 60
READY_POLL_SECONDS = 60
STABILITY_CHECKS = 2
STABILITY_SLEEP_SECONDS = 20

DEFAULT_STATE_FILE = "market_watch_state.json"
DEFAULT_LOCK_FILE = "market_watcher.lock"
DEFAULT_INTRADAY_BARS = 5
DEFAULT_TRADING_POLL_SECONDS = 60
DEFAULT_OFFHOURS_POLL_SECONDS = 600
DEFAULT_EOD_START = "15:30"
DEFAULT_RELOAD_URL = "http://127.0.0.1:8000/reload"
INCREMENTAL_DOWNLOAD_CHUNK_SIZE = 80
INCREMENTAL_FALLBACK_PERIOD = "1mo"
STALE_TARGET_WARN_AFTER = "09:35"
STALE_FORCE_REFRESH_SECONDS = 10 * 60


def _ensure_tz_taipei(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    idx = pd.to_datetime(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC").tz_convert("Asia/Taipei")
    else:
        idx = idx.tz_convert("Asia/Taipei")
    df.index = idx
    return df


def _silence_yf_download(*args, **kwargs):
    return yf.download(*args, **kwargs)


def _normalize_ohlcv_frame(raw: pd.DataFrame, symbol: Optional[str] = None) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        names = [str(name or "").lower() for name in df.columns.names]
        tuples = list(df.columns)

        normalized_cols = []
        for col in tuples:
            left = str(col[0])
            right = str(col[1]) if len(col) > 1 else ""

            if left in {"Open", "High", "Low", "Close", "Adj Close", "Volume"}:
                normalized_cols.append(left)
                continue
            if right in {"Open", "High", "Low", "Close", "Adj Close", "Volume"}:
                normalized_cols.append(right)
                continue

            if symbol:
                if left == symbol and right:
                    normalized_cols.append(right)
                    continue
                if right == symbol and left:
                    normalized_cols.append(left)
                    continue

            if "price" in names and len(col) > 1:
                price_idx = names.index("price")
                normalized_cols.append(str(col[price_idx]))
            else:
                normalized_cols.append(left)

        df.columns = normalized_cols

    keep = [c for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in df.columns]
    if not keep:
        return pd.DataFrame()
    return df[keep].copy()


def _to_taipei_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("Asia/Taipei")


def _download_latest_ts_batch(symbols: List[str], period: str = "5d", interval: str = WATCH_INTERVAL) -> Dict[str, Optional[pd.Timestamp]]:
    out: Dict[str, Optional[pd.Timestamp]] = {s: None for s in symbols}
    if not symbols:
        return out
    try:
        df = download_intraday_batch(symbols, interval, period_override=period)
        if df is None or df.empty or "Datetime" not in df.columns:
            return out
        latest_per_symbol = (
            df.assign(_dt=pd.to_datetime(df["Datetime"]))
            .groupby("Ticker")["_dt"]
            .max()
            .to_dict()
        )
        for sym, dt_value in latest_per_symbol.items():
            if sym in out:
                out[sym] = _to_taipei_timestamp(dt_value)
    except Exception as e:
        log.warning("下載最新 sentinel timestamps 失敗：%s", e)
    return out


def _download_latest_bar_close(symbol: str, period: str = "5d", interval: str = WATCH_INTERVAL):
    try:
        df = download_intraday_single(symbol, interval, period_override=period)
        if df.empty:
            return None
        df = df.copy()
        df["_dt"] = pd.to_datetime(df["Datetime"])
        df = df.sort_values("_dt").dropna(subset=["Close"])
        if df.empty:
            return None
        row = df.iloc[-1]
        return _to_taipei_timestamp(row["_dt"]), float(row["Close"])
    except Exception:
        return None


def _download_latest_non_null_bar(symbol: str, period: str = "5d", interval: str = WATCH_SIGNAL_INTERVAL):
    try:
        df = download_intraday_single(symbol, interval, period_override=period)
        if df.empty:
            return None
        df = df.copy()
        df["_dt"] = pd.to_datetime(df["Datetime"])
        df = df.sort_values("_dt").dropna(subset=["Close"])
        if df.empty:
            return None
        row = df.iloc[-1]
        volume = int(row["Volume"]) if "Volume" in row and pd.notna(row["Volume"]) else 0
        return _to_taipei_timestamp(row["_dt"]), float(row["Close"]), volume
    except Exception:
        return None


def _download_latest_non_null_batch(symbols: List[str], period: str = "5d", interval: str = WATCH_SIGNAL_INTERVAL) -> Dict[str, Optional[pd.Timestamp]]:
    out: Dict[str, Optional[pd.Timestamp]] = {s: None for s in symbols}
    if not symbols:
        return out
    try:
        df = download_intraday_batch(symbols, interval, period_override=period)
        if df is None or df.empty or "Datetime" not in df.columns:
            return out
        latest_per_symbol = (
            df.assign(_dt=pd.to_datetime(df["Datetime"]))
            .dropna(subset=["Close"])
            .groupby("Ticker")["_dt"]
            .max()
            .to_dict()
        )
        for sym, dt_value in latest_per_symbol.items():
            if sym in out:
                out[sym] = _to_taipei_timestamp(dt_value)
    except Exception as e:
        log.warning("下載 latest non-null bars 失敗：%s", e)
    return out


def _bar_key(ts: pd.Timestamp) -> str:
    if getattr(ts, "tzinfo", None) is not None:
        ts = ts.tz_convert("Asia/Taipei").tz_localize(None)
    return ts.strftime("%Y%m%d%H%M")


def _load_state(state_path: str) -> Dict:
    if not os.path.exists(state_path):
        return {}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state_path: str, state: Dict) -> None:
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, state_path)


def _acquire_process_lock(lock_path: str):
    if not lock_path:
        return None

    if os.path.exists(lock_path):
        handle = open(lock_path, "r+", encoding="utf-8")
    else:
        handle = open(lock_path, "w+", encoding="utf-8")

    try:
        handle.seek(0)
        handle.write("0")
        handle.flush()
        handle.seek(0)

        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\nstarted_at={datetime.now().isoformat()}\n")
        handle.flush()
        return handle
    except OSError:
        handle.close()
        raise RuntimeError(f"watcher 已在執行中；若確定舊程序已停止，可刪除 {lock_path} 後重試")


def _release_process_lock(handle) -> None:
    if handle is None:
        return

    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass

    try:
        handle.seek(0)
        handle.truncate()
        handle.flush()
    except Exception:
        pass
    finally:
        handle.close()


def _notify_api_reload(reload_url: str) -> None:
    if not reload_url:
        return
    try:
        resp = requests.get(reload_url, timeout=30)
        resp.raise_for_status()
        log.info("[API] reload 成功：%s", reload_url)
    except Exception as e:
        log.warning("[API] reload 失敗：%s", e)


def _is_market_day(now_tw: Optional[pd.Timestamp] = None) -> bool:
    now_tw = now_tw or pd.Timestamp.now(tz="Asia/Taipei")
    return now_tw.weekday() < 5


def _is_market_open_now(now_tw: Optional[pd.Timestamp] = None) -> bool:
    now_tw = now_tw or pd.Timestamp.now(tz="Asia/Taipei")
    if not _is_market_day(now_tw):
        return False
    t = now_tw.time()
    return dt_time(9, 0) <= t <= dt_time(13, 45)


def _after_eod_start(eod_start: str, now_tw: Optional[pd.Timestamp] = None) -> bool:
    now_tw = now_tw or pd.Timestamp.now(tz="Asia/Taipei")
    if not _is_market_day(now_tw):
        return False
    hh, mm = map(int, eod_start.split(":"))
    return now_tw.time() >= dt_time(hh, mm)


def _after_clock(hhmm: str, now_tw: Optional[pd.Timestamp] = None) -> bool:
    now_tw = now_tw or pd.Timestamp.now(tz="Asia/Taipei")
    hh, mm = map(int, hhmm.split(":"))
    return now_tw.time() >= dt_time(hh, mm)


def _is_stale_intraday_target(target: pd.Timestamp, now_tw: Optional[pd.Timestamp] = None) -> bool:
    now_tw = now_tw or pd.Timestamp.now(tz="Asia/Taipei")
    if not _is_market_open_now(now_tw):
        return False
    if not _after_clock(STALE_TARGET_WARN_AFTER, now_tw):
        return False
    target_local = target.tz_convert("Asia/Taipei") if getattr(target, "tzinfo", None) else target.tz_localize("Asia/Taipei")
    return target_local.date() < now_tw.date()


def _seconds_since_iso(iso_value: Optional[str]) -> Optional[float]:
    if not iso_value:
        return None
    try:
        ts = pd.Timestamp(iso_value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("Asia/Taipei")
        else:
            ts = ts.tz_convert("Asia/Taipei")
        now_tw = pd.Timestamp.now(tz="Asia/Taipei")
        return (now_tw - ts).total_seconds()
    except Exception:
        return None


def _prefer_fresher_frame(primary: pd.DataFrame, fallback: pd.DataFrame) -> pd.DataFrame:
    if primary is None or primary.empty:
        return fallback if fallback is not None else pd.DataFrame()
    if fallback is None or fallback.empty:
        return primary

    merged = pd.concat([primary, fallback], ignore_index=True)
    merged = merged.drop_duplicates(subset=["Ticker", "Datetime"], keep="last")
    merged = merged.sort_values("Datetime").reset_index(drop=True)
    return merged


def _detect_target_bar_end() -> Optional[pd.Timestamp]:
    ts_map = _download_latest_non_null_batch(SENTINEL_SYMBOLS, period="5d", interval=WATCH_SIGNAL_INTERVAL)
    ts_list = [t for t in ts_map.values() if t is not None]
    if not ts_list:
        return None
    return max(ts_list)


def _get_market_signature(target: pd.Timestamp) -> str:
    try:
        latest = _download_latest_non_null_bar("2330.TW", period="5d", interval=WATCH_SIGNAL_INTERVAL)
        if latest is None:
            return "N/A"
        ts, close, vol = latest
        if ts != target:
            return "N/A"
        return f"{_bar_key(target)}_V{vol}_C{close:.1f}"
    except Exception:
        pass
    return "N/A"


def _sentinel_non_null_ready_ratio(target: pd.Timestamp, interval: str = WATCH_SIGNAL_INTERVAL) -> float:
    ts_map = _download_latest_non_null_batch(SENTINEL_SYMBOLS, period="5d", interval=interval)
    ok = 0
    total = max(len(SENTINEL_SYMBOLS), 1)
    for sym in SENTINEL_SYMBOLS:
        t = ts_map.get(sym)
        if t is not None and t >= target:
            ok += 1
    return ok / total


def _is_interval_fully_ready(interval: str, target_date: str, ts_value: Optional[pd.Timestamp]) -> bool:
    if ts_value is None:
        return False
    final_label = FINAL_BAR_LABELS.get(interval)
    if not final_label:
        return False
    return ts_value.strftime("%Y-%m-%d %H:%M:%S") == f"{target_date} {final_label}"


def _sentinel_full_ready(interval: str, target_date: str, need_ratio: float = SENTINEL_QUORUM) -> bool:
    ts_map = _download_latest_non_null_batch(SENTINEL_SYMBOLS, period="5d", interval=interval)
    total = max(len(SENTINEL_SYMBOLS), 1)
    ok = 0
    for sym in SENTINEL_SYMBOLS:
        if _is_interval_fully_ready(interval, target_date, ts_map.get(sym)):
            ok += 1
    return (ok / total) >= need_ratio


def _sentinel_full_ready_ratio(interval: str, target_date: str) -> float:
    ts_map = _download_latest_non_null_batch(SENTINEL_SYMBOLS, period="5d", interval=interval)
    total = max(len(SENTINEL_SYMBOLS), 1)
    ok = 0
    for sym in SENTINEL_SYMBOLS:
        if _is_interval_fully_ready(interval, target_date, ts_map.get(sym)):
            ok += 1
    return ok / total


def _sentinel_quorum_ok(target: pd.Timestamp) -> bool:
    ts_map = _download_latest_ts_batch(SENTINEL_SYMBOLS, period="5d", interval=WATCH_INTERVAL)
    ok = 0
    for sym in SENTINEL_SYMBOLS:
        t = ts_map.get(sym)
        if t is not None and t >= target:
            ok += 1
    return (ok / max(len(SENTINEL_SYMBOLS), 1)) >= SENTINEL_QUORUM


def _stable_latest_bar(symbol: str = "2330.TW", checks: int = STABILITY_CHECKS) -> bool:
    prev_ts = None
    prev_close = None
    for i in range(checks):
        cur = _download_latest_bar_close(symbol, period="5d", interval=WATCH_INTERVAL)
        if cur is None:
            return False
        ts, close = cur
        if prev_ts is not None:
            if ts != prev_ts:
                return False
            if np.isfinite(close) and np.isfinite(prev_close) and abs(close - prev_close) > 1e-5:
                return False
        prev_ts = ts
        prev_close = close
        if i != checks - 1:
            time.sleep(STABILITY_SLEEP_SECONDS)
    return True


def _sample_ready_ratio(all_symbols: List[str], target: pd.Timestamp, sample_size: int = SAMPLE_SIZE) -> float:
    if not all_symbols:
        return 0.0
    rng = random.Random(int(_bar_key(target)))
    sample = all_symbols if len(all_symbols) <= sample_size else rng.sample(all_symbols, sample_size)
    ok = 0
    seen = 0
    for i in range(0, len(sample), 80):
        chunk = sample[i:i + 80]
        ts_map = _download_latest_ts_batch(chunk, period="5d", interval=WATCH_INTERVAL)
        for sym in chunk:
            seen += 1
            t = ts_map.get(sym)
            if t is not None and t >= target:
                ok += 1
        time.sleep(0.15)
    return ok / max(seen, 1)


def wait_for_market_ready(all_symbols: List[str], target: pd.Timestamp, eod: bool = False) -> bool:
    start = time.time()
    key = _bar_key(target)
    need_ratio = SAMPLE_READY_RATIO_EOD if eod else SAMPLE_READY_RATIO_INTRA

    while time.time() - start <= READY_MAX_WAIT_SECONDS:
        if not _sentinel_quorum_ok(target):
            log.info("[Ready] target=%s sentinel quorum 未達，等待中...", key)
            time.sleep(READY_POLL_SECONDS)
            continue

        if not _stable_latest_bar("2330.TW", checks=STABILITY_CHECKS):
            log.info("[Ready] target=%s 30m bar 尚未穩定，等待中...", key)
            time.sleep(READY_POLL_SECONDS)
            continue

        rr = _sample_ready_ratio(all_symbols, target)
        log.info("[Ready] target=%s sample_ready_ratio=%.2f%% (need %.0f%%)", key, rr * 100, need_ratio * 100)
        if rr >= need_ratio:
            return True

        time.sleep(READY_POLL_SECONDS)

    log.warning("[Ready] timeout: target=%s 市場資料仍未達 ready 條件", key)
    return False


def _tail_rows(df: pd.DataFrame, bars: int) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return df.sort_values("Datetime").tail(bars).copy()


def _tail_rows_per_ticker(df: pd.DataFrame, bars: int) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return (
        df.sort_values(["Ticker", "Datetime"])
        .groupby("Ticker", group_keys=False)
        .tail(bars)
        .copy()
    )


def _max_dt_text(df: pd.DataFrame) -> str:
    if df is None or df.empty or "Datetime" not in df.columns:
        return ""
    try:
        return str(pd.to_datetime(df["Datetime"]).max())
    except Exception:
        return ""


def _batch_is_fresh_for_today(df: pd.DataFrame, now_tw: pd.Timestamp) -> bool:
    max_text = _max_dt_text(df)
    if not max_text:
        return False
    try:
        max_dt = pd.Timestamp(max_text)
        return max_dt.date() >= now_tw.date()
    except Exception:
        return False


def _latest_db_intraday_ts(db_path: str, timeframe: str) -> Optional[pd.Timestamp]:
    try:
        conn = init_db(db_path)
        try:
            row = conn.execute(
                "SELECT MAX(Datetime) FROM intraday_candles WHERE Timeframe=?",
                (timeframe,),
            ).fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        ts = pd.Timestamp(row[0])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC").tz_convert("Asia/Taipei")
        else:
            ts = ts.tz_convert("Asia/Taipei")
        return ts
    except Exception:
        return None


def _latest_db_daily_date(db_path: str) -> Optional[str]:
    try:
        conn = init_db(db_path)
        try:
            row = conn.execute("SELECT MAX(Date) FROM daily_candles").fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        return str(row[0])
    except Exception:
        return None


def run_intraday_incremental_update(db_path: str, stocks: pd.DataFrame, bars: int = DEFAULT_INTRADAY_BARS) -> None:
    conn = init_db(db_path)
    try:
        now_tw = pd.Timestamp.now(tz="Asia/Taipei")
        upsert_stocks(conn, stocks)
        raw_tickers = stocks["ticker"].tolist()
        priority = [sym for sym in SENTINEL_SYMBOLS if sym in raw_tickers]
        seen = set(priority)
        tickers = priority + [ticker for ticker in raw_tickers if ticker not in seen]
        writes = {"15m": 0, "30m": 0, "60m": 0, "180m": 0, "240m": 0}
        latest_seen: dict[str, str] = {"15m": "", "30m": "", "60m": ""}
        priority_done_logged = False
        log.info(
            "▶ 盤中增量刷新開始：優先處理 %s 檔哨兵/大型權值股，15m 批次下載後本地合成高週期（chunk=%s）",
            len(priority),
            INCREMENTAL_DOWNLOAD_CHUNK_SIZE,
        )

        for chunk_start in range(0, len(tickers), INCREMENTAL_DOWNLOAD_CHUNK_SIZE):
            chunk = tickers[chunk_start:chunk_start + INCREMENTAL_DOWNLOAD_CHUNK_SIZE]
            processed = chunk_start + len(chunk)

            df15_full = download_intraday_batch(chunk, "15m", days=1)
            df30_full = download_intraday_batch(chunk, "30m", days=1)
            df60_full = download_intraday_batch(chunk, "60m", days=1)
            used_fallback = []
            if _is_market_open_now(now_tw) and not _batch_is_fresh_for_today(df15_full, now_tw):
                df15_fallback = download_intraday_batch(
                    chunk,
                    "15m",
                    days=1,
                    period_override=INCREMENTAL_FALLBACK_PERIOD,
                )
                if not df15_fallback.empty:
                    df15_full = df15_fallback
                    used_fallback.append("15m")
            if _is_market_open_now(now_tw) and not _batch_is_fresh_for_today(df30_full, now_tw):
                df30_fallback = download_intraday_batch(
                    chunk,
                    "30m",
                    days=1,
                    period_override=INCREMENTAL_FALLBACK_PERIOD,
                )
                if not df30_fallback.empty:
                    df30_full = df30_fallback
                    used_fallback.append("30m")
            if _is_market_open_now(now_tw) and not _batch_is_fresh_for_today(df60_full, now_tw):
                df60_fallback = download_intraday_batch(
                    chunk,
                    "60m",
                    days=1,
                    period_override=INCREMENTAL_FALLBACK_PERIOD,
                )
                if not df60_fallback.empty:
                    df60_full = df60_fallback
                    used_fallback.append("60m")

            df180_full = resample_from_60m(df60_full, "180m") if not df60_full.empty else pd.DataFrame()
            df240_full = resample_from_60m(df60_full, "240m") if not df60_full.empty else pd.DataFrame()

            frames = {
                "15m": _tail_rows_per_ticker(df15_full, bars),
                "30m": _tail_rows_per_ticker(df30_full, bars),
                "60m": _tail_rows_per_ticker(df60_full, bars),
                "180m": _tail_rows_per_ticker(df180_full, bars),
                "240m": _tail_rows_per_ticker(df240_full, bars),
            }

            full_frames = {
                "15m": df15_full,
                "30m": df30_full,
                "60m": df60_full,
            }
            for tf, df_full in full_frames.items():
                if not df_full.empty:
                    latest_seen[tf] = max(latest_seen[tf], str(df_full["Datetime"].max()))

            for tf, df_tail in frames.items():
                if not df_tail.empty:
                    writes[tf] += upsert_intraday(conn, df_tail, tf)

            if used_fallback:
                log.info(
                    "  [native fallback] chunk %s-%s intervals=%s period=%s latest15=%s latest30=%s latest60=%s",
                    chunk_start + 1,
                    processed,
                    ",".join(used_fallback),
                    INCREMENTAL_FALLBACK_PERIOD,
                    latest_seen["15m"] or "N/A",
                    latest_seen["30m"] or "N/A",
                    latest_seen["60m"] or "N/A",
                )

            if not priority_done_logged and processed >= len(priority):
                log.info(
                    "✅ 哨兵優先批完成：15m=%s 30m=%s 60m=%s，latest 15m=%s 30m=%s 60m=%s",
                    f"{writes['15m']:,}",
                    f"{writes['30m']:,}",
                    f"{writes['60m']:,}",
                    latest_seen["15m"] or "N/A",
                    latest_seen["30m"] or "N/A",
                    latest_seen["60m"] or "N/A",
                )
                priority_done_logged = True

            if processed % 160 == 0 or processed == len(tickers):
                log.info(
                    "  盤中增量進度 %s/%s，15m=%s 30m=%s 60m=%s 180m=%s 240m=%s，latest 15m=%s 30m=%s 60m=%s",
                    processed, len(tickers),
                    writes["15m"], writes["30m"], writes["60m"], writes["180m"], writes["240m"],
                    latest_seen["15m"] or "N/A",
                    latest_seen["30m"] or "N/A",
                    latest_seen["60m"] or "N/A",
                )

            time.sleep(0.03)

        log.info(
            "✅ 盤中增量更新完成：15m=%s / 30m=%s / 60m=%s / 180m=%s / 240m=%s",
            f"{writes['15m']:,}", f"{writes['30m']:,}", f"{writes['60m']:,}",
            f"{writes['180m']:,}", f"{writes['240m']:,}",
        )
        log.info(
            "📌 盤中來源最新時間：15m=%s / 30m=%s / 60m=%s",
            latest_seen["15m"] or "N/A",
            latest_seen["30m"] or "N/A",
            latest_seen["60m"] or "N/A",
        )
    finally:
        conn.close()


def run_eod_refresh(db_path: str, stocks: pd.DataFrame, purple_tf: str, purple_lookback: int) -> None:
    conn = init_db(db_path)
    try:
        upsert_stocks(conn, stocks)
        tickers = stocks["ticker"].tolist()
        log.info("▶ 14:00 後開始盤後整理：更新今天日K / 分K / 紫圈")
        update_daily(conn, tickers, days=1)
        update_intraday(conn, tickers, days=1)
        update_purple_signals(conn, stocks, lookback_days=purple_lookback, purple_tf=purple_tf)
        log.info("✅ 盤後整理完成")
    finally:
        conn.close()


def loop_once(
    stocks: pd.DataFrame,
    state_path: str,
    intraday_bars: int,
    eod_start: str,
    purple_tf: str,
    purple_lookback: int,
    reload_url: str,
) -> None:
    now_tw = pd.Timestamp.now(tz="Asia/Taipei")
    today_str = now_tw.strftime("%Y-%m-%d")
    state = _load_state(state_path)

    target = _detect_target_bar_end()
    if target is None:
        log.warning("[??] ?????? 15m ?? bar")
        return

    bar_key = _bar_key(target)
    current_signature = _get_market_signature(target)
    target_local = target.tz_convert("Asia/Taipei") if getattr(target, "tzinfo", None) else target

    if _is_market_open_now(now_tw):
        last_done = state.get("last_intraday_bar_key")
        last_sig = state.get("last_intraday_signature", "N/A")

        if target_local.date() < now_tw.date():
            log.warning(
                "[??] ?????? 15m ??? %s?????????? bar",
                target_local.strftime("%Y-%m-%d %H:%M"),
            )
        elif last_done == bar_key and last_sig == current_signature:
            log.info("[??] ????target=%s sig=%s", bar_key, current_signature)
        else:
            ready_ratio = _sentinel_non_null_ready_ratio(target, interval=WATCH_SIGNAL_INTERVAL)
            log.info(
                "[??] target=%s sentinel_non_null_ratio=%.2f%% (need %.0f%%)",
                bar_key,
                ready_ratio * 100,
                INTRADAY_SENTINEL_READY_RATIO * 100,
            )
            if ready_ratio >= INTRADAY_SENTINEL_READY_RATIO:
                run_intraday_incremental_update(DB_PATH, stocks, bars=intraday_bars)
                latest_15m = _latest_db_intraday_ts(DB_PATH, "15m")
                if latest_15m is not None and latest_15m >= target:
                    _notify_api_reload(reload_url)
                    state["last_intraday_bar_key"] = bar_key
                    state["last_intraday_signature"] = current_signature
                    state["last_intraday_run_ts"] = datetime.now().isoformat()
                    _save_state(state_path, state)
                    log.info(
                        "[??] ???????target=%s db_15m=%s",
                        bar_key,
                        latest_15m.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                else:
                    log.warning(
                        "[??] ??? DB ??? target?target=%s db_15m=%s",
                        bar_key,
                        latest_15m.strftime("%Y-%m-%d %H:%M:%S") if latest_15m is not None else "None",
                    )
            else:
                log.info("[??] ?????????????? DB")

    if _after_eod_start(eod_start, now_tw) and state.get("last_eod_date") != today_str:
        ready_ratios = {
            interval: _sentinel_full_ready_ratio(interval, today_str)
            for interval in FULL_FINAL_INTERVALS
        }
        ready = all(ratio >= SENTINEL_QUORUM for ratio in ready_ratios.values())
        log.info(
            "[??] FULL ratios 15m=%.2f%% 30m=%.2f%% 60m=%.2f%% 1d=%.2f%% (need %.0f%%)",
            ready_ratios["15m"] * 100,
            ready_ratios["30m"] * 100,
            ready_ratios["60m"] * 100,
            ready_ratios["1d"] * 100,
            SENTINEL_QUORUM * 100,
        )
        if ready:
            run_eod_refresh(DB_PATH, stocks, purple_tf=purple_tf, purple_lookback=purple_lookback)
            latest_daily = _latest_db_daily_date(DB_PATH)
            latest_15m = _latest_db_intraday_ts(DB_PATH, "15m")
            intraday_ok = latest_15m is not None and latest_15m.date().strftime("%Y-%m-%d") == today_str
            daily_ok = latest_daily == today_str
            if intraday_ok and daily_ok:
                _notify_api_reload(reload_url)
                state["last_eod_date"] = today_str
                state["last_eod_run_ts"] = datetime.now().isoformat()
                state["last_intraday_bar_key"] = bar_key
                state["last_intraday_signature"] = current_signature
                state["last_intraday_run_ts"] = datetime.now().isoformat()
                _save_state(state_path, state)
                log.info(
                    "[??] ???????daily=%s intraday_15m=%s",
                    latest_daily,
                    latest_15m.strftime("%Y-%m-%d %H:%M:%S"),
                )
            else:
                log.warning(
                    "[??] DB ?????????? state?daily=%s intraday_15m=%s",
                    latest_daily or "None",
                    latest_15m.strftime("%Y-%m-%d %H:%M:%S") if latest_15m is not None else "None",
                )
        else:
            log.info("[??] ?????? final label?????????")

def main():
    parser = argparse.ArgumentParser(description="盤中自動更新 watcher（30m 哨兵觸發 + 14:00 盤後整理）")
    parser.add_argument("--once", action="store_true", help="只執行一輪檢查後離開")
    parser.add_argument("--state", type=str, default=DEFAULT_STATE_FILE, help="狀態檔路徑")
    parser.add_argument("--lock-file", type=str, default=DEFAULT_LOCK_FILE, help="單實例 lock 檔；留空可停用")
    parser.add_argument("--bars", type=int, default=DEFAULT_INTRADAY_BARS, help="盤中只 upsert 最近幾根 K 棒")
    parser.add_argument("--poll-trading-seconds", type=int, default=DEFAULT_TRADING_POLL_SECONDS, help="盤中輪詢秒數")
    parser.add_argument("--poll-offhours-seconds", type=int, default=DEFAULT_OFFHOURS_POLL_SECONDS, help="非盤中輪詢秒數")
    parser.add_argument("--eod-start", type=str, default=DEFAULT_EOD_START, help="盤後整理開始時間，例如 14:00")
    parser.add_argument("--purple-tf", choices=["60m", "1d", "all"], default="all", help="盤後紫圈重建週期")
    parser.add_argument("--purple-lookback", type=int, default=7, help="盤後紫圈回溯天數")
    parser.add_argument("--reload-url", type=str, default=DEFAULT_RELOAD_URL, help="每次寫入 DB 後通知 API reload 的 URL；留空可停用")
    args = parser.parse_args()

    lock_handle = None
    try:
        try:
            lock_handle = _acquire_process_lock(args.lock_file)
        except RuntimeError as e:
            log.error("%s", e)
            return

        configure_yfinance_cache()
        stocks = get_all_stocks()
        log.info(
            "盤中 watcher 啟動：stocks=%s bars=%s eod_start=%s reload_url=%s lock=%s",
            len(stocks),
            args.bars,
            args.eod_start,
            args.reload_url or "(disabled)",
            args.lock_file or "(disabled)",
        )

        if args.once:
            loop_once(stocks, args.state, args.bars, args.eod_start, args.purple_tf, args.purple_lookback, args.reload_url)
            return

        while True:
            try:
                loop_once(stocks, args.state, args.bars, args.eod_start, args.purple_tf, args.purple_lookback, args.reload_url)
            except Exception as e:
                log.exception("watcher 本輪執行失敗：%s", e)

            sleep_s = args.poll_trading_seconds if _is_market_open_now() else args.poll_offhours_seconds
            time.sleep(sleep_s)
    except KeyboardInterrupt:
        log.info("watcher 已由使用者停止")
    finally:
        _release_process_lock(lock_handle)


if __name__ == "__main__":
    main()
