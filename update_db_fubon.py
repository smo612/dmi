import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd

from fubon_probe import FubonProbeClient, extract_candle_rows, parse_bar_time
from update_db import (
    DB_PATH,
    DEFAULT_DAILY_DAYS,
    DEFAULT_INTRADAY_DAYS,
    DEFAULT_RELOAD_URL,
    get_all_stocks,
    init_db,
    log,
    notify_api_reload,
    resample_from_15m,
    update_purple_signals,
    upsert_daily,
    upsert_intraday,
    upsert_stocks,
)

BUFFER_TICKERS = 40


def build_date_range(days: int) -> tuple[str, str]:
    today = datetime.now().date()
    start = today - timedelta(days=max(int(days), 0))
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def filter_stocks(stocks: pd.DataFrame, tickers_arg: str = "", limit: int = 0) -> pd.DataFrame:
    selected = stocks.copy()
    if tickers_arg.strip():
        wanted = {part.strip().upper() for part in tickers_arg.split(",") if part.strip()}
        wanted_codes = {item.split(".", 1)[0] for item in wanted}
        selected = selected[
            selected["ticker"].str.upper().isin(wanted)
            | selected["code"].str.upper().isin(wanted_codes)
        ].copy()
    if limit > 0:
        selected = selected.head(limit).copy()
    return selected.reset_index(drop=True)


def daily_rows_to_df(ticker: str, rows: list[dict]) -> pd.DataFrame:
    records: list[dict] = []
    for row in rows:
        trade_date = str(row.get("date") or "").strip()
        if not trade_date:
            continue
        records.append(
            {
                "Ticker": ticker,
                "Date": trade_date,
                "Open": float(row.get("open") or 0),
                "High": float(row.get("high") or 0),
                "Low": float(row.get("low") or 0),
                "Close": float(row.get("close") or 0),
                "Volume": int(row.get("volume") or 0),
            }
        )
    return pd.DataFrame(records)


def intraday_rows_to_df(ticker: str, rows: list[dict]) -> pd.DataFrame:
    records: list[dict] = []
    for row in rows:
        bar_time = parse_bar_time(row.get("date"))
        if not bar_time:
            continue
        # Store intraday timestamps in the same UTC-naive shape as the
        # existing Yahoo pipeline. The API converts them back to Taipei time.
        db_time = bar_time.astimezone(timezone.utc).replace(tzinfo=None)
        records.append(
            {
                "Ticker": ticker,
                "Datetime": db_time.strftime("%Y-%m-%d %H:%M:%S"),
                "Open": float(row.get("open") or 0),
                "High": float(row.get("high") or 0),
                "Low": float(row.get("low") or 0),
                "Close": float(row.get("close") or 0),
                "Volume": int(row.get("volume") or 0),
            }
        )
    return pd.DataFrame(records)


def flush_daily_buffer(conn: sqlite3.Connection, frames: list[pd.DataFrame]) -> int:
    if not frames:
        return 0
    df = pd.concat(frames, ignore_index=True)
    frames.clear()
    return upsert_daily(conn, df)


def flush_intraday_buffers(conn: sqlite3.Connection, frames_by_tf: dict[str, list[pd.DataFrame]]) -> dict[str, int]:
    written = {tf: 0 for tf in frames_by_tf}
    for timeframe, frames in frames_by_tf.items():
        if not frames:
            continue
        df = pd.concat(frames, ignore_index=True)
        frames.clear()
        written[timeframe] = upsert_intraday(conn, df, timeframe)
    return written


def update_daily_fubon(
    conn: sqlite3.Connection,
    stocks: pd.DataFrame,
    client: FubonProbeClient,
    days: int,
) -> None:
    start_date, end_date = build_date_range(days)
    total = len(stocks)
    daily_frames: list[pd.DataFrame] = []
    total_written = 0
    errors = 0

    log.info(
        "[FUBON] start daily update: stocks=%s range=%s~%s",
        total,
        start_date,
        end_date,
    )
    for idx, row in stocks.iterrows():
        ticker = row["ticker"]
        code = row["code"]
        try:
            payload = client.fetch_historical_candles(code, "D", start_date, end_date)
            df = daily_rows_to_df(ticker, extract_candle_rows(payload))
            if not df.empty:
                daily_frames.append(df)
        except Exception as exc:
            errors += 1
            log.warning("[FUBON][1d] failed [%s]: %s", ticker, exc)

        processed = idx + 1
        if processed % BUFFER_TICKERS == 0 or processed == total:
            total_written += flush_daily_buffer(conn, daily_frames)
            log.info("[FUBON][1d] progress %s/%s rows=%s errors=%s", processed, total, total_written, errors)

    log.info("[FUBON] daily update done: rows=%s errors=%s", total_written, errors)


def update_intraday_fubon(
    conn: sqlite3.Connection,
    stocks: pd.DataFrame,
    client: FubonProbeClient,
    days: int,
) -> None:
    start_date, end_date = build_date_range(days)
    total = len(stocks)
    frames_by_tf = {tf: [] for tf in ("15m", "30m", "60m", "180m", "240m")}
    total_written = {tf: 0 for tf in frames_by_tf}
    errors = 0

    log.info(
        "[FUBON] start intraday update: stocks=%s range=%s~%s",
        total,
        start_date,
        end_date,
    )
    for idx, row in stocks.iterrows():
        ticker = row["ticker"]
        code = row["code"]
        try:
            payload = client.fetch_historical_candles(code, "15", start_date, end_date)
            df15 = intraday_rows_to_df(ticker, extract_candle_rows(payload))
            if not df15.empty:
                frames_by_tf["15m"].append(df15)
                for timeframe in ("30m", "60m", "180m", "240m"):
                    df_resampled = resample_from_15m(df15, timeframe)
                    if not df_resampled.empty:
                        frames_by_tf[timeframe].append(df_resampled)
        except Exception as exc:
            errors += 1
            log.warning("[FUBON][15m] failed [%s]: %s", ticker, exc)

        processed = idx + 1
        if processed % BUFFER_TICKERS == 0 or processed == total:
            chunk_written = flush_intraday_buffers(conn, frames_by_tf)
            for timeframe, count in chunk_written.items():
                total_written[timeframe] += count
            log.info(
                "[FUBON][intra] progress %s/%s 15m=%s 30m=%s 60m=%s 180m=%s 240m=%s errors=%s",
                processed,
                total,
                total_written["15m"],
                total_written["30m"],
                total_written["60m"],
                total_written["180m"],
                total_written["240m"],
                errors,
            )

    log.info(
        "[FUBON] intraday update done: 15m=%s 30m=%s 60m=%s 180m=%s 240m=%s errors=%s",
        total_written["15m"],
        total_written["30m"],
        total_written["60m"],
        total_written["180m"],
        total_written["240m"],
        errors,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fubon-based updater for the stock scanner database")
    parser.add_argument("--tf", choices=["1d", "intraday", "all"], default="all")
    parser.add_argument("--purple", action="store_true")
    parser.add_argument("--purple-lookback", type=int, default=7, dest="purple_lookback")
    parser.add_argument("--purple-tf", choices=["60m", "1d", "all"], default="all", dest="purple_tf")
    parser.add_argument("--daily-days", type=int, default=DEFAULT_DAILY_DAYS)
    parser.add_argument("--intraday-days", type=int, default=DEFAULT_INTRADAY_DAYS)
    parser.add_argument("--tickers", type=str, default="", help="Comma-separated tickers or codes, e.g. 2330,6902")
    parser.add_argument("--limit", type=int, default=0, help="Only update the first N selected stocks")
    parser.add_argument("--db-path", type=str, default=DB_PATH)
    parser.add_argument("--reload-url", type=str, default=DEFAULT_RELOAD_URL)
    parser.add_argument("--request-gap-seconds", type=float, default=2.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-sleep-seconds", type=float, default=3.0)
    args = parser.parse_args()

    log.info("==================================================")
    log.info(
        "  FUBON updater start: tf=%s%s",
        args.tf,
        " + purple" if args.purple else "",
    )
    log.info("==================================================")

    conn = init_db(args.db_path)
    stocks = get_all_stocks()
    selected_stocks = filter_stocks(stocks, args.tickers, args.limit)
    if selected_stocks.empty:
        raise SystemExit("No stocks matched the given --tickers/--limit selection")

    upsert_stocks(conn, selected_stocks)
    client = FubonProbeClient(
        request_gap_seconds=args.request_gap_seconds,
        retries=args.retries,
        retry_sleep_seconds=args.retry_sleep_seconds,
    )

    if args.tf in ("1d", "all"):
        update_daily_fubon(conn, selected_stocks, client, days=args.daily_days)

    if args.tf in ("intraday", "all"):
        update_intraday_fubon(conn, selected_stocks, client, days=args.intraday_days)

    if args.purple:
        update_purple_signals(
            conn,
            selected_stocks,
            lookback_days=args.purple_lookback,
            purple_tf=args.purple_tf,
        )

    conn.close()
    notify_api_reload(args.reload_url)
    log.info("==================================================")
    log.info("  FUBON updater done")
    log.info("==================================================")


if __name__ == "__main__":
    main()
