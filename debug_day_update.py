import argparse
import sqlite3
from datetime import datetime, timedelta

import pandas as pd

from update_db import (
    configure_yfinance_cache,
    download_daily_batch,
    download_intraday_batch,
    init_db,
    log,
    resample_from_15m,
    upsert_daily,
    upsert_intraday,
    upsert_stocks,
    _drop_intraday_daily_placeholders,
)


def resolve_ticker(symbol: str, source_db: str) -> str:
    symbol = symbol.strip().upper()
    if "." in symbol:
        return symbol

    conn = sqlite3.connect(source_db)
    try:
        rows = conn.execute(
            "SELECT Ticker FROM stocks WHERE Ticker LIKE ? ORDER BY Ticker",
            (f"{symbol}.%",),
        ).fetchall()
    finally:
        conn.close()

    matches = [row[0] for row in rows]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"ticker code not found: {symbol}; try full ticker like {symbol}.TW")
    raise SystemExit(f"multiple tickers matched {symbol}: {', '.join(matches)}; use full ticker")


def ensure_stock_row(conn: sqlite3.Connection, ticker: str) -> None:
    market = ticker.split(".")[-1] if "." in ticker else ""
    code = ticker.split(".")[0]
    stock_df = pd.DataFrame(
        [{"ticker": ticker, "name": code, "market": market}]
    )
    upsert_stocks(conn, stock_df)


def filter_intraday_date(df: pd.DataFrame, target_date: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["Ticker", "Datetime", "Open", "High", "Low", "Close", "Volume"])

    out = df.copy()
    out["_date"] = pd.to_datetime(out["Datetime"]).dt.strftime("%Y-%m-%d")
    out = out[out["_date"] == target_date].copy()
    if "_date" in out.columns:
        out = out.drop(columns=["_date"])
    return out


def filter_daily_date(df: pd.DataFrame, target_date: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"])
    out = df[df["Date"] == target_date].copy()
    return out


def clear_target_rows(conn: sqlite3.Connection, ticker: str, target_date: str) -> None:
    conn.execute(
        "DELETE FROM daily_candles WHERE Ticker=? AND Date=?",
        (ticker, target_date),
    )
    conn.execute(
        "DELETE FROM intraday_candles WHERE Ticker=? AND Datetime >= ? AND Datetime < ?",
        (
            ticker,
            f"{target_date} 00:00:00",
            f"{(datetime.strptime(target_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')} 00:00:00",
        ),
    )
    conn.commit()


def summarize_intraday(df: pd.DataFrame) -> str:
    if df.empty:
        return "0"
    times = pd.to_datetime(df["Datetime"]).dt.strftime("%H:%M").tolist()
    return f"{len(df)} bars ({', '.join(times)})"


def fetch_symbol_day(
    ticker: str,
    target_date: str,
    output_db: str,
    intraday_period: str,
) -> None:
    output_conn = init_db(output_db)
    try:
        ensure_stock_row(output_conn, ticker)
        clear_target_rows(output_conn, ticker, target_date)

        raw_15m = download_intraday_batch([ticker], "15m", days=5, period_override=intraday_period)
        cleaned_15m = _drop_intraday_daily_placeholders(raw_15m)

        raw_15m_day = filter_intraday_date(raw_15m, target_date)
        cleaned_15m_day = filter_intraday_date(cleaned_15m, target_date)
        tf_frames = {
            "15m_raw": raw_15m_day,
            "15m": cleaned_15m_day,
            "30m": filter_intraday_date(resample_from_15m(cleaned_15m, "30m"), target_date),
            "60m": filter_intraday_date(resample_from_15m(cleaned_15m, "60m"), target_date),
            "180m": filter_intraday_date(resample_from_15m(cleaned_15m, "180m"), target_date),
            "240m": filter_intraday_date(resample_from_15m(cleaned_15m, "240m"), target_date),
        }

        next_date = (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        daily = download_daily_batch([ticker], target_date, next_date)
        daily_day = filter_daily_date(daily, target_date)

        if not daily_day.empty:
            upsert_daily(output_conn, daily_day)
        for timeframe, frame in tf_frames.items():
            if not frame.empty:
                upsert_intraday(output_conn, frame, timeframe)

        log.info("[%s] 1d: %s rows", ticker, len(daily_day))
        for timeframe, frame in tf_frames.items():
            log.info("[%s] %s: %s", ticker, timeframe, summarize_intraday(frame))
    finally:
        output_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch one day's debug candles into a separate DB")
    parser.add_argument("symbols", nargs="+", help="Stock code or full ticker, e.g. 2206 or 2206.TW")
    parser.add_argument("--date", default="2026-04-20", help="Target date in YYYY-MM-DD")
    parser.add_argument("--db", default=None, help="Output DB path; default debug_YYYYMMDD.db")
    parser.add_argument("--source-db", default="stock_data.db", help="Source DB used to resolve ticker suffix")
    parser.add_argument(
        "--intraday-period",
        default="1mo",
        help="Yahoo intraday period override; default 1mo",
    )
    args = parser.parse_args()

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit(f"invalid --date: {exc}") from exc

    output_db = args.db or f"debug_{args.date.replace('-', '')}.db"
    configure_yfinance_cache()

    for symbol in args.symbols:
        ticker = resolve_ticker(symbol, args.source_db)
        log.info("fetching %s for %s into %s", ticker, args.date, output_db)
        fetch_symbol_day(
            ticker=ticker,
            target_date=args.date,
            output_db=output_db,
            intraday_period=args.intraday_period,
        )


if __name__ == "__main__":
    main()
