import argparse
import json
import logging
import os
import time
from datetime import datetime, time as dt_time

import pandas as pd

from fubon_probe import (
    TW,
    FubonProbeClient,
    bar_signature,
    extract_candle_rows,
    extract_latest_bar,
    parse_bar_time,
)
from update_db import (
    DEFAULT_RELOAD_URL,
    get_all_stocks,
    init_db,
    notify_api_reload,
    resample_from_15m,
    upsert_daily,
    upsert_intraday,
    upsert_stocks,
)
from update_db_fubon import BUFFER_TICKERS, build_date_range, filter_stocks, intraday_rows_to_df

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("fubon_intraday_watcher.log", encoding="utf-8"),
    ],
    force=True,
)
log = logging.getLogger("fubon_intraday_watcher")

DEFAULT_STATE_FILE = "fubon_intraday_watch_state.json"
DEFAULT_LOCK_FILE = "fubon_intraday_watcher.lock"
DEFAULT_POLL_SECONDS = 30
DEFAULT_OFFHOURS_POLL_SECONDS = 300
DEFAULT_INTRADAY_DAYS = 1
DEFAULT_SENTINEL_READY_RATIO = 0.60
TIMEFRAME_MINUTES = {"30m": 30, "60m": 60, "180m": 180, "240m": 240}
SESSION_END_HOUR = 13
SESSION_END_MINUTE = 30
SENTINEL_SYMBOLS = [
    "2330.TW",
    "2317.TW",
    "2454.TW",
    "2308.TW",
    "2412.TW",
    "2881.TW",
    "2891.TW",
    "1301.TW",
    "1303.TW",
    "2002.TW",
    "2603.TW",
    "2615.TW",
    "0050.TW",
    "006208.TW",
]


def _is_market_day(now_tw: pd.Timestamp | None = None) -> bool:
    now_tw = now_tw or pd.Timestamp.now(tz="Asia/Taipei")
    return now_tw.weekday() < 5


def _is_market_open_now(now_tw: pd.Timestamp | None = None) -> bool:
    now_tw = now_tw or pd.Timestamp.now(tz="Asia/Taipei")
    if not _is_market_day(now_tw):
        return False
    current = now_tw.time()
    return dt_time(9, 0) <= current <= dt_time(13, 35)


def _load_state(state_path: str) -> dict:
    if not os.path.exists(state_path):
        return {}
    try:
        with open(state_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def _save_state(state_path: str, state: dict) -> None:
    tmp_path = state_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, state_path)


def _acquire_process_lock(lock_path: str):
    if not lock_path:
        return None

    handle = open(lock_path, "a+", encoding="utf-8")
    try:
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
        raise RuntimeError(f"watcher already running; lock is held at {lock_path}")


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


def _latest_local_map_from_df15(df_15m: pd.DataFrame) -> dict[str, pd.Timestamp]:
    if df_15m.empty:
        return {}
    dt_local = pd.to_datetime(df_15m["Datetime"], errors="coerce").dt.tz_localize("UTC").dt.tz_convert(TW)
    work = df_15m.assign(_dt_local=dt_local).dropna(subset=["_dt_local"])
    latest = work.groupby("Ticker")["_dt_local"].max()
    return {ticker: pd.Timestamp(ts) for ticker, ts in latest.items()}


def _last_expected_15m_start(bucket_start_local: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    session_end = bucket_start_local.normalize() + pd.Timedelta(hours=SESSION_END_HOUR, minutes=SESSION_END_MINUTE)
    bucket_end = min(bucket_start_local + pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe]), session_end)
    return bucket_end - pd.Timedelta(minutes=15)


def _filter_finalized_resampled_rows(
    df_resampled: pd.DataFrame,
    latest_15m_local: dict[str, pd.Timestamp],
    timeframe: str,
) -> pd.DataFrame:
    if df_resampled.empty:
        return df_resampled

    keep_rows: list[int] = []
    for idx, row in df_resampled.iterrows():
        ticker = str(row["Ticker"])
        latest_local = latest_15m_local.get(ticker)
        if latest_local is None:
            continue

        bucket_start = pd.Timestamp(row["Datetime"])
        if bucket_start.tzinfo is None:
            bucket_start = bucket_start.tz_localize("UTC").tz_convert(TW)
        else:
            bucket_start = bucket_start.tz_convert(TW)

        if latest_local >= _last_expected_15m_start(bucket_start, timeframe):
            keep_rows.append(idx)

    if not keep_rows:
        return df_resampled.iloc[0:0].copy()
    return df_resampled.loc[keep_rows].reset_index(drop=True)


def _build_provisional_daily_rows(df_15m: pd.DataFrame, target_date: str) -> pd.DataFrame:
    if df_15m.empty:
        return pd.DataFrame(columns=["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"])

    dt_local = pd.to_datetime(df_15m["Datetime"], errors="coerce").dt.tz_localize("UTC").dt.tz_convert(TW)
    work = df_15m.assign(_dt_local=dt_local).dropna(subset=["_dt_local"]).copy()
    if work.empty:
        return pd.DataFrame(columns=["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"])

    work["_date"] = work["_dt_local"].dt.strftime("%Y-%m-%d")
    work = work.loc[work["_date"] == target_date].copy()
    if work.empty:
        return pd.DataFrame(columns=["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"])

    work = work.sort_values(["Ticker", "_dt_local"]).reset_index(drop=True)
    grouped = work.groupby("Ticker", sort=False)

    daily = pd.DataFrame(
        {
            "Ticker": grouped["Ticker"].first().values,
            "Date": target_date,
            "Open": grouped["Open"].first().values,
            "High": pd.to_numeric(grouped["High"].max(), errors="coerce").values,
            "Low": pd.to_numeric(grouped["Low"].min(), errors="coerce").values,
            "Close": grouped["Close"].last().values,
            "Volume": pd.to_numeric(grouped["Volume"].sum(), errors="coerce").fillna(0).astype(int).values,
        }
    )
    return daily.reset_index(drop=True)


def _filter_df_to_local_date(df: pd.DataFrame, target_date: str) -> pd.DataFrame:
    if df.empty:
        return df
    dt_local = pd.to_datetime(df["Datetime"], errors="coerce").dt.tz_localize("UTC").dt.tz_convert(TW)
    work = df.assign(_dt_local=dt_local).dropna(subset=["_dt_local"]).copy()
    work = work.loc[work["_dt_local"].dt.strftime("%Y-%m-%d") == target_date].copy()
    if work.empty:
        return df.iloc[0:0].copy()
    return work.drop(columns=["_dt_local"]).reset_index(drop=True)


def _fetch_sentinel_snapshot(
    client: FubonProbeClient,
    sentinel_symbols: list[str],
    target_date: str,
) -> tuple[str | None, float, list[tuple[str, str]]]:
    latest_dt_map: dict[str, pd.Timestamp] = {}
    signature_parts: list[tuple[str, str]] = []

    for symbol in sentinel_symbols:
        try:
            payload = client.fetch_intraday_candles(symbol, "15m")
            latest = extract_latest_bar(payload)
        except Exception as exc:
            log.warning("[sentinel] fetch failed [%s]: %s", symbol, exc)
            continue

        if not latest:
            continue

        latest_dt = parse_bar_time(latest.get("date"))
        if latest_dt is None or latest_dt.strftime("%Y-%m-%d") != target_date:
            continue

        latest_dt_map[symbol] = pd.Timestamp(latest_dt)
        signature_parts.append((symbol, repr(bar_signature(latest))))

    total = max(len(sentinel_symbols), 1)
    if not latest_dt_map:
        return None, 0.0, []

    market_latest = max(latest_dt_map.values())
    matching = sorted(
        (symbol, signature)
        for symbol, signature in signature_parts
        if latest_dt_map.get(symbol) == market_latest
    )
    ready_ratio = len(matching) / total
    token = market_latest.isoformat()
    return token, ready_ratio, matching


def _flush_intraday_chunk(
    conn,
    frames_15m: list[pd.DataFrame],
    writes: dict[str, int],
    target_date: str,
) -> None:
    if not frames_15m:
        return

    df_15m = pd.concat(frames_15m, ignore_index=True)
    frames_15m.clear()
    if df_15m.empty:
        return

    writes["15m"] += upsert_intraday(conn, df_15m, "15m")
    df_daily = _build_provisional_daily_rows(df_15m, target_date)
    if not df_daily.empty:
        writes["1d"] += upsert_daily(conn, df_daily)
    latest_local = _latest_local_map_from_df15(df_15m)

    for timeframe in ("30m", "60m", "180m", "240m"):
        df_resampled = resample_from_15m(df_15m, timeframe)
        df_finalized = _filter_finalized_resampled_rows(df_resampled, latest_local, timeframe)
        if not df_finalized.empty:
            writes[timeframe] += upsert_intraday(conn, df_finalized, timeframe)


def run_intraday_cycle(
    db_path: str,
    stocks: pd.DataFrame,
    client: FubonProbeClient,
    intraday_days: int,
    target_date: str,
) -> dict[str, int]:
    conn = init_db(db_path)
    writes = {tf: 0 for tf in ("1d", "15m", "30m", "60m", "180m", "240m")}
    frames_15m: list[pd.DataFrame] = []
    errors = 0
    try:
        upsert_stocks(conn, stocks)
        total = len(stocks)
        log.info("[watch] intraday cycle start: stocks=%s target_date=%s source=intraday15m", total, target_date)

        for idx, row in stocks.iterrows():
            ticker = row["ticker"]
            try:
                payload = client.fetch_intraday_candles(ticker, "15m")
                df_15m = intraday_rows_to_df(ticker, extract_candle_rows(payload), "15m")
                df_15m = _filter_df_to_local_date(df_15m, target_date)
                if not df_15m.empty:
                    frames_15m.append(df_15m)
            except Exception as exc:
                errors += 1
                log.warning("[watch][15m] failed [%s]: %s", ticker, exc)

            processed = idx + 1
            if processed % BUFFER_TICKERS == 0 or processed == total:
                _flush_intraday_chunk(conn, frames_15m, writes, target_date)
                log.info(
                    "[watch] progress %s/%s 1d=%s 15m=%s 30m=%s 60m=%s 180m=%s 240m=%s errors=%s",
                    processed,
                    total,
                    writes["1d"],
                    writes["15m"],
                    writes["30m"],
                    writes["60m"],
                    writes["180m"],
                    writes["240m"],
                    errors,
                )

        return writes
    finally:
        conn.close()


def loop_once(args, client: FubonProbeClient, stocks: pd.DataFrame) -> None:
    now_tw = pd.Timestamp.now(tz="Asia/Taipei")
    if not _is_market_open_now(now_tw):
        log.info("[watch] market closed: now=%s", now_tw.strftime("%Y-%m-%d %H:%M:%S"))
        return

    state = _load_state(args.state)
    target_date = now_tw.strftime("%Y-%m-%d")
    available = set(stocks["ticker"].tolist())
    sentinel_symbols = [symbol for symbol in SENTINEL_SYMBOLS if symbol in available]
    token, ready_ratio, matching = _fetch_sentinel_snapshot(client, sentinel_symbols, target_date)

    if not token:
        log.info("[watch] no valid 15m sentinel bar for %s yet", target_date)
        return

    log.info(
        "[watch] sentinel ready_ratio=%.2f%% need=%.0f%% matching=%s",
        ready_ratio * 100,
        args.sentinel_ready_ratio * 100,
        len(matching),
    )
    if ready_ratio < args.sentinel_ready_ratio:
        return

    if state.get("last_trigger_token") == token:
        log.info("[watch] no new 15m market token")
        return

    writes = run_intraday_cycle(args.db_path, stocks, client, args.intraday_days, target_date)
    total_writes = sum(writes.values())
    if total_writes <= 0:
        log.warning("[watch] cycle wrote no rows")
        return

    notify_api_reload(args.reload_url)
    state["last_trigger_token"] = token
    state["last_cycle_run_ts"] = datetime.now().isoformat()
    state["last_cycle_writes"] = writes
    _save_state(args.state, state)
    log.info(
        "[watch] cycle done: 1d=%s 15m=%s 30m=%s 60m=%s 180m=%s 240m=%s",
        writes["1d"],
        writes["15m"],
        writes["30m"],
        writes["60m"],
        writes["180m"],
        writes["240m"],
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fubon intraday watcher: 15m sentinel trigger, 15m native update, finalized higher-timeframe synthesis, auto reload."
    )
    parser.add_argument("--once", action="store_true", help="Run one poll cycle and exit.")
    parser.add_argument("--state", default=DEFAULT_STATE_FILE, help="Watcher state JSON path.")
    parser.add_argument("--lock-file", default=DEFAULT_LOCK_FILE, help="Single-instance lock file path.")
    parser.add_argument("--db-path", default="stock_data.db", help="SQLite DB path.")
    parser.add_argument("--tickers", default="", help="Comma-separated tickers or codes, e.g. 2330,2454,2330.TW")
    parser.add_argument("--limit", type=int, default=0, help="Only watch/update the first N selected stocks.")
    parser.add_argument("--intraday-days", type=int, default=DEFAULT_INTRADAY_DAYS, help="How many recent days to refetch for each 15m cycle.")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS, help="Polling interval during market hours.")
    parser.add_argument("--poll-offhours-seconds", type=int, default=DEFAULT_OFFHOURS_POLL_SECONDS, help="Polling interval outside market hours.")
    parser.add_argument("--reload-url", default=DEFAULT_RELOAD_URL, help="API reload URL; empty string disables reload.")
    parser.add_argument("--request-gap-seconds", type=float, default=0.15, help="Minimum gap between Fubon requests.")
    parser.add_argument("--retries", type=int, default=3, help="Retry count per Fubon request.")
    parser.add_argument("--retry-sleep-seconds", type=float, default=1.0, help="Base retry sleep in seconds.")
    parser.add_argument("--sentinel-ready-ratio", type=float, default=DEFAULT_SENTINEL_READY_RATIO, help="Required ratio of sentinels sharing the latest 15m bar.")
    return parser.parse_args()


def main():
    args = parse_args()
    lock_handle = None
    try:
        try:
            lock_handle = _acquire_process_lock(args.lock_file)
        except RuntimeError as exc:
            log.error("%s", exc)
            return

        stocks = filter_stocks(get_all_stocks(), args.tickers, args.limit)
        if stocks.empty:
            raise SystemExit("No stocks matched the given --tickers/--limit selection")

        client = FubonProbeClient(
            request_gap_seconds=args.request_gap_seconds,
            retries=args.retries,
            retry_sleep_seconds=args.retry_sleep_seconds,
        )

        log.info(
            "[watch] start: stocks=%s intraday_days=%s poll=%ss offhours_poll=%ss reload=%s",
            len(stocks),
            args.intraday_days,
            args.poll_seconds,
            args.poll_offhours_seconds,
            args.reload_url or "(disabled)",
        )

        if args.once:
            loop_once(args, client, stocks)
            return

        while True:
            try:
                loop_once(args, client, stocks)
            except Exception as exc:
                log.exception("[watch] loop failed: %s", exc)
            sleep_seconds = args.poll_seconds if _is_market_open_now() else args.poll_offhours_seconds
            time.sleep(max(sleep_seconds, 1))
    except KeyboardInterrupt:
        log.info("[watch] stopped by user")
    finally:
        _release_process_lock(lock_handle)


if __name__ == "__main__":
    main()
