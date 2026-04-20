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
DEFAULT_INTRADAY_BARS = 5
DEFAULT_TRADING_POLL_SECONDS = 60
DEFAULT_OFFHOURS_POLL_SECONDS = 600
DEFAULT_EOD_START = "14:00"
DEFAULT_RELOAD_URL = "http://127.0.0.1:8000/reload"


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


def _download_latest_ts_batch(symbols: List[str], period: str = "5d", interval: str = WATCH_INTERVAL) -> Dict[str, Optional[pd.Timestamp]]:
    out: Dict[str, Optional[pd.Timestamp]] = {s: None for s in symbols}
    if not symbols:
        return out
    try:
        raw = _silence_yf_download(
            tickers=" ".join(symbols),
            period=period,
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="ticker",
        )
        if raw is None or raw.empty:
            return out

        if isinstance(raw.columns, pd.MultiIndex):
            for sym in symbols:
                try:
                    sub = pd.DataFrame()
                    for level in range(raw.columns.nlevels):
                        try:
                            candidate = raw.xs(sym, axis=1, level=level)
                            if candidate is not None and not candidate.empty:
                                sub = candidate
                                break
                        except Exception:
                            continue
                    sub = _normalize_ohlcv_frame(sub, symbol=sym).dropna(how="all")
                    if sub.empty:
                        continue
                    sub = _ensure_tz_taipei(sub)
                    out[sym] = sub.index.max()
                except Exception:
                    continue
        else:
            raw = _normalize_ohlcv_frame(raw, symbol=symbols[0]).dropna(how="all")
            if not raw.empty:
                raw = _ensure_tz_taipei(raw)
                out[symbols[0]] = raw.index.max()
    except Exception as e:
        log.warning("下載最新 sentinel timestamps 失敗：%s", e)
    return out


def _download_latest_bar_close(symbol: str, period: str = "5d", interval: str = WATCH_INTERVAL):
    try:
        df = _silence_yf_download(symbol, period=period, interval=interval, progress=False, auto_adjust=False)
        df = _normalize_ohlcv_frame(df, symbol=symbol)
        df = _ensure_tz_taipei(df).dropna(subset=["Close"])
        if df.empty:
            return None
        row = df.iloc[-1]
        return df.index[-1], float(row["Close"])
    except Exception:
        return None


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


def _detect_target_bar_end() -> Optional[pd.Timestamp]:
    ts_map = _download_latest_ts_batch(SENTINEL_SYMBOLS, period="5d", interval=WATCH_INTERVAL)
    ts_list = [t for t in ts_map.values() if t is not None]
    if not ts_list:
        return None
    return max(ts_list)


def _get_market_signature(target: pd.Timestamp) -> str:
    try:
        df = _silence_yf_download("2330.TW", period="5d", interval=WATCH_INTERVAL, progress=False, auto_adjust=False)
        df = _normalize_ohlcv_frame(df, symbol="2330.TW")
        if df.empty:
            return "N/A"
        df = _ensure_tz_taipei(df)
        if target in df.index:
            row = df.loc[target]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            vol = int(row["Volume"]) if "Volume" in row else 0
            close = float(row["Close"]) if "Close" in row else 0.0
            return f"{_bar_key(target)}_V{vol}_C{close:.1f}"
    except Exception:
        pass
    return "N/A"


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


def run_intraday_incremental_update(db_path: str, stocks: pd.DataFrame, bars: int = DEFAULT_INTRADAY_BARS) -> None:
    conn = init_db(db_path)
    try:
        upsert_stocks(conn, stocks)
        tickers = stocks["ticker"].tolist()
        writes = {"15m": 0, "30m": 0, "60m": 0, "180m": 0, "240m": 0}

        for i, ticker in enumerate(tickers, start=1):
            if i % 100 == 0:
                log.info(
                    "  盤中增量進度 %s/%s，15m=%s 30m=%s 60m=%s 180m=%s 240m=%s",
                    i, len(tickers),
                    writes["15m"], writes["30m"], writes["60m"], writes["180m"], writes["240m"],
                )

            df15 = _tail_rows(download_intraday_single(ticker, "15m", days=1), bars)
            if not df15.empty:
                writes["15m"] += upsert_intraday(conn, df15, "15m")

            df30 = _tail_rows(download_intraday_single(ticker, "30m", days=1), bars)
            if not df30.empty:
                writes["30m"] += upsert_intraday(conn, df30, "30m")

            df60_full = download_intraday_single(ticker, "60m", days=1)
            df60 = _tail_rows(df60_full, bars)
            if not df60.empty:
                writes["60m"] += upsert_intraday(conn, df60, "60m")

            if not df60_full.empty:
                for timeframe in ("180m", "240m"):
                    df_rs = _tail_rows(resample_from_60m(df60_full, timeframe), bars)
                    if not df_rs.empty:
                        writes[timeframe] += upsert_intraday(conn, df_rs, timeframe)

            time.sleep(0.08)

        log.info(
            "✅ 盤中增量更新完成：15m=%s / 30m=%s / 60m=%s / 180m=%s / 240m=%s",
            f"{writes['15m']:,}", f"{writes['30m']:,}", f"{writes['60m']:,}",
            f"{writes['180m']:,}", f"{writes['240m']:,}",
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
    tickers = stocks["ticker"].tolist()
    state = _load_state(state_path)

    target = _detect_target_bar_end()
    if target is None:
        log.warning("無法偵測最新 30m bar，略過本輪")
        return

    bar_key = _bar_key(target)
    current_signature = _get_market_signature(target)

    if _is_market_open_now(now_tw):
        last_done = state.get("last_intraday_bar_key")
        last_sig = state.get("last_intraday_signature", "N/A")
        if last_done == bar_key and last_sig == current_signature:
            log.info("[盤中] 無更新：target=%s sig=%s", bar_key, current_signature)
        else:
            if last_done == bar_key and last_sig != current_signature:
                log.info("[盤中] 同 bar 資料更新：target=%s old=%s new=%s", bar_key, last_sig, current_signature)
            if wait_for_market_ready(tickers, target, eod=False):
                run_intraday_incremental_update(DB_PATH, stocks, bars=intraday_bars)
                _notify_api_reload(reload_url)
                state["last_intraday_bar_key"] = bar_key
                state["last_intraday_signature"] = current_signature
                state["last_intraday_run_ts"] = datetime.now().isoformat()
                _save_state(state_path, state)

    if _after_eod_start(eod_start, now_tw) and state.get("last_eod_date") != today_str:
        log.info("[盤後] 偵測到 %s 之後尚未做今日整理，準備執行", eod_start)
        if wait_for_market_ready(tickers, target, eod=True):
            run_eod_refresh(DB_PATH, stocks, purple_tf=purple_tf, purple_lookback=purple_lookback)
            _notify_api_reload(reload_url)
            state["last_eod_date"] = today_str
            state["last_eod_run_ts"] = datetime.now().isoformat()
            _save_state(state_path, state)


def main():
    parser = argparse.ArgumentParser(description="盤中自動更新 watcher（30m 哨兵觸發 + 14:00 盤後整理）")
    parser.add_argument("--once", action="store_true", help="只執行一輪檢查後離開")
    parser.add_argument("--state", type=str, default=DEFAULT_STATE_FILE, help="狀態檔路徑")
    parser.add_argument("--bars", type=int, default=DEFAULT_INTRADAY_BARS, help="盤中只 upsert 最近幾根 K 棒")
    parser.add_argument("--poll-trading-seconds", type=int, default=DEFAULT_TRADING_POLL_SECONDS, help="盤中輪詢秒數")
    parser.add_argument("--poll-offhours-seconds", type=int, default=DEFAULT_OFFHOURS_POLL_SECONDS, help="非盤中輪詢秒數")
    parser.add_argument("--eod-start", type=str, default=DEFAULT_EOD_START, help="盤後整理開始時間，例如 14:00")
    parser.add_argument("--purple-tf", choices=["60m", "1d", "all"], default="all", help="盤後紫圈重建週期")
    parser.add_argument("--purple-lookback", type=int, default=7, help="盤後紫圈回溯天數")
    parser.add_argument("--reload-url", type=str, default=DEFAULT_RELOAD_URL, help="每次寫入 DB 後通知 API reload 的 URL；留空可停用")
    args = parser.parse_args()

    configure_yfinance_cache()
    stocks = get_all_stocks()
    log.info(
        "盤中 watcher 啟動：stocks=%s bars=%s eod_start=%s reload_url=%s",
        len(stocks),
        args.bars,
        args.eod_start,
        args.reload_url or "(disabled)",
    )

    if args.once:
        loop_once(stocks, args.state, args.bars, args.eod_start, args.purple_tf, args.purple_lookback, args.reload_url)
        return

    try:
        while True:
            try:
                loop_once(stocks, args.state, args.bars, args.eod_start, args.purple_tf, args.purple_lookback, args.reload_url)
            except Exception as e:
                log.exception("watcher 本輪執行失敗：%s", e)

            sleep_s = args.poll_trading_seconds if _is_market_open_now() else args.poll_offhours_seconds
            time.sleep(sleep_s)
    except KeyboardInterrupt:
        log.info("watcher 已由使用者停止")


if __name__ == "__main__":
    main()
